"""Synthetic ticket revocation ordering/replay/race; no native/provider calls."""
from contextlib import contextmanager
from types import SimpleNamespace
import uuid

import pytest

from eidolon_cli import organization_setup_subscription_auth as auth
from eidolon_cli.organization_setup_subscription_store import TrustedBinding, StoreError


@pytest.fixture
def revocation_rig():
    rig = SimpleNamespace(authority=False, live=True, calls=[], receipt=None, ambiguous=False,
                          ticket=None, epoch=str(uuid.uuid4()), stage='none')
    binding = TrustedBinding(str(uuid.uuid4()), '/synthetic/profile', str(uuid.uuid4()),
                             str(uuid.uuid4()), str(uuid.uuid4()), 'oaiapp_synthetic', 'synthetic-account')
    @contextmanager
    def authority():
        assert not rig.authority
        rig.authority = True
        try:
            yield
        finally:
            rig.authority = False
    class RevokeLease:
        closed = False
        def close(self):
            self.closed = True
        def is_set(self):
            return self.closed or not rig.live
        def run(self, request):
            assert rig.receipt is not None and not rig.authority
            try:
                result = request('synthetic-refresh', self)
                if result is True and not rig.ambiguous and not self.is_set():
                    rig.receipt['remoteRevocation'] = 'confirmed'
                rig.stage = 'completed'
                return dict(rig.receipt)
            finally:
                self.close()
    class Store:
        def returning_binding(self):
            return binding
        def prepare_ticket(self, checked, revision, *, kind, source_revision, owner_guard, cancel):
            assert checked == binding and rig.authority and kind == 'revoke'
            assert owner_guard() is True and not cancel.is_set() and revision == 1
            if rig.ticket is None:
                rig.ticket = {'version': 1, 'sequence': '1', 'nonce': 'b' * 64, 'kind': kind,
                    'bindingDigest': 'c' * 64, 'authorizationEpoch': rig.epoch,
                    'priorStoreRevision': revision, 'sourceRevision': source_revision}
                rig.stage = 'prepared'
            return dict(rig.ticket)
        def reconcile_ticket(self, checked, *, ticket, owner_guard, cancel):
            assert rig.authority and checked == binding and owner_guard() is True and not cancel.is_set()
            if ticket != rig.ticket:
                raise StoreError('ticket_retired')
            return {'state': rig.stage, 'ticket': dict(ticket), 'receipt': rig.receipt,
                    'revision': rig.receipt['revision'] if rig.receipt else 1,
                    'verification': 'verified_current'}
        def disconnect_grant(self, checked, *, expected_revision, ticket, owner_guard, cancel):
            assert checked == binding and rig.authority and owner_guard() is True
            assert not cancel.is_set() and ticket == rig.ticket and rig.stage == 'prepared'
            rig.receipt = {'revision': expected_revision + 1, 'kind': 'revoke',
                           'localRetired': True, 'remoteRevocation': 'unconfirmed'}
            rig.stage = 'admitted'
            rig.lease = RevokeLease()
            return dict(rig.receipt), rig.lease
    owner = auth.OwnerContext(binding.profile_locator, binding.organization_id, binding.owner_id,
        'a' * 64, object(), object(), lambda: rig.live, authority, lambda: False, lambda: rig.live)
    def http(url, form):
        assert rig.receipt['localRetired'] and not rig.authority
        assert url == auth.REVOKE
        assert form == {'token': 'synthetic-refresh', 'token_type_hint': 'refresh_token',
                        'client_id': 'oaiapp_synthetic'}
        rig.calls.append(form)
        return True
    rig.store, rig.owner, rig.http = Store(), owner, http
    rig.host = SimpleNamespace(installation_id=lambda: binding.host_id)
    rig.service = auth.SubscriptionAuthService(rig.store, rig.host, owner, synthetic=True, http=http)
    return rig


def disconnect(rig):
    prepared = rig.service.prepare_operation('revoke', 1)
    return rig.service.execute_operation(prepared['ticket']) if prepared['ticket'] else prepared


def test_tombstone_before_single_revoke_and_restart_replay(revocation_rig):
    rig = revocation_rig
    first = disconnect(rig)
    assert first['receipt']['remoteRevocation'] == 'confirmed' and len(rig.calls) == 1
    assert first['catalogRevision'] is None
    restarted = auth.SubscriptionAuthService(rig.store, rig.host, rig.owner, synthetic=True, http=rig.http)
    assert restarted.execute_operation(first['ticket']) == first
    assert len(rig.calls) == 1 and rig.lease.closed


def test_earlier_refresh_ambiguity_prevents_remote_confirmation(revocation_rig):
    rig = revocation_rig
    rig.ambiguous = True
    result = disconnect(rig)
    assert result['receipt']['localRetired'] and result['receipt']['remoteRevocation'] == 'unconfirmed'
    assert len(rig.calls) == 1


def test_unknown_revoke_keeps_durable_tombstone_and_no_replay(revocation_rig):
    rig = revocation_rig
    def unknown(*args):
        rig.calls.append('sent')
        raise RuntimeError('synthetic-secret')
    rig.service._http = unknown
    result = disconnect(rig)
    assert 'synthetic-secret' not in str(result)
    assert rig.service.execute_operation(result['ticket']) == result
    assert result['receipt']['remoteRevocation'] == 'unconfirmed' and rig.calls == ['sent']


def test_retirement_during_revoke_remains_unconfirmed(revocation_rig):
    rig = revocation_rig
    def retire(*args):
        auth.cancel_transport(rig.owner.transport, generation=rig.owner.owner_generation)
        return True
    rig.service._http = retire
    with pytest.raises(auth.AuthError, match='stale_owner'):
        disconnect(rig)
    assert rig.receipt['remoteRevocation'] == 'unconfirmed' and rig.lease.closed


def test_disconnect_closes_prior_capabilities_but_allows_new_revoke(revocation_rig):
    rig = revocation_rig
    closed = []
    class OldLease:
        def close(self):
            closed.append(True)
    rig.service._leases.add(OldLease())
    original = rig.store.disconnect_grant
    def admit(*args, **kwargs):
        assert closed and not rig.service._lease_cancel.is_set()
        return original(*args, **kwargs)
    rig.store.disconnect_grant = admit
    result = disconnect(rig)
    assert result['receipt']['remoteRevocation'] == 'confirmed' and len(rig.calls) == 1
    assert not rig.service._lease_cancel.is_set()


@pytest.mark.parametrize('reason', ['storage_recovery_required', 'transition_pending', 'publication_uncertain'])
def test_storage_unavailable_still_returns_verified_local_only_retirement(revocation_rig, reason):
    rig = revocation_rig
    receipt = {'revision': 2, 'kind': 'local', 'localRetired': True, 'remoteRevocation': 'unconfirmed'}
    def blocked(*args, **kwargs):
        raise StoreError(reason)
    def local_only(binding, **kwargs):
        assert rig.authority and kwargs['expected_revision'] == 1
        return dict(receipt)
    rig.store.prepare_ticket, rig.store.local_disconnect = blocked, local_only
    result = rig.service.prepare_operation('revoke', 1)
    assert result['state'] == 'local_retired' and result['ticket'] is None
    assert result['receipt'] == receipt and result['catalogRevision'] is None and not rig.calls


def test_failed_barrier_does_not_claim_retirement(revocation_rig):
    rig = revocation_rig
    def blocked(*args, **kwargs):
        raise StoreError('publication_uncertain')
    rig.store.prepare_ticket = rig.store.local_disconnect = blocked
    with pytest.raises(auth.AuthError, match='publication_uncertain'):
        rig.service.prepare_operation('revoke', 1)
    assert not rig.calls and rig.receipt is None

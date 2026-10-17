"""Synthetic refresh coordinator and signed-claim tests; no provider/native calls."""
from contextlib import contextmanager
from dataclasses import replace
import base64
import hashlib
import json
import threading
import time
from types import SimpleNamespace
import uuid

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import pytest

from eidolon_cli import organization_setup_subscription_auth as auth
from eidolon_cli.organization_setup_subscription_store import TrustedBinding, StoreError


class Lease:
    def __init__(self, rig):
        self.rig = rig
        self.authorization_epoch = rig.epoch
        self.prior_revision = 1
        self.identity_evidence = {'subject': 'synthetic-account', 'audience': 'oaiapp_synthetic',
                                  'nonce': 'synthetic-nonce', 'authTime': None, 'authorizedParty': None}
        self.granted_scopes = auth.SCOPES.split()
        self.closed = False
    def is_set(self):
        return self.closed or not self.rig.live or self.rig.store.phase == 'disconnected'
    def close(self):
        self.closed = True
    def run(self, request):
        assert not self.rig.authority
        if self.is_set():
            raise StoreError('lease_cancelled')
        return request('synthetic-old-refresh', self)


@pytest.fixture
def refresh_rig():
    rig = SimpleNamespace(live=True, fresh=True, authority=False, calls=[], commits=[], guards=[],
                          epoch=str(uuid.uuid4()), ticket=None, stage='none')
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
    def durable():
        rig.guards.append('durable')
        return rig.live
    def cheap():
        rig.guards.append('cheap')
        return rig.live
    class Store:
        phase, revision, cleanup_pending = 'active', 1, False
        def status(self):
            return {'state': self.phase, 'revision': self.revision, 'cleanup_pending': self.cleanup_pending}
        def connection_descriptor(self):
            return binding, self.revision
        def returning_binding(self):
            return binding
        def prepare_ticket(self, checked, revision, *, kind, source_revision, owner_guard, cancel):
            assert checked == binding and rig.authority and owner_guard() is True and not cancel.is_set()
            if rig.ticket is not None:
                if kind != rig.ticket['kind']:
                    raise StoreError('ticket_conflict')
                assert revision == rig.ticket['priorStoreRevision']
                return dict(rig.ticket)
            assert revision == self.revision and kind == 'refresh'
            rig.ticket = {'version': 1, 'sequence': '1', 'nonce': 'b' * 64, 'kind': kind,
                          'bindingDigest': 'c' * 64, 'authorizationEpoch': rig.epoch,
                          'priorStoreRevision': revision, 'sourceRevision': source_revision}
            rig.stage = 'prepared'
            return dict(rig.ticket)
        def reconcile_ticket(self, checked, *, ticket, owner_guard, cancel):
            assert checked == binding and rig.authority and owner_guard() is True and not cancel.is_set()
            if ticket != rig.ticket:
                raise StoreError('ticket_retired')
            receipt = ({'kind': 'refresh', 'revision': self.revision, 'localRetired': False,
                        'remoteRevocation': 'not_applicable'} if rig.stage == 'completed' else None)
            return {'state': rig.stage, 'ticket': dict(ticket), 'receipt': receipt,
                    'revision': self.revision, 'verification': 'verified_current'}
        def ticket_status(self):
            return {'state': rig.stage, 'ticket': dict(rig.ticket) if rig.ticket else None,
                    'revision': self.revision, 'verification': 'metadata_only'}
        def cancel_ticket(self, checked, *, ticket, owner_guard, cancel):
            assert checked == binding and ticket == rig.ticket and rig.authority
            rig.ticket, rig.stage = None, 'none'
            return {'state': 'none', 'ticket': None, 'receipt': None,
                    'revision': self.revision, 'verification': 'verified_current'}
        def reconcile_transition(self, checked, *, owner_guard, cancel):
            assert checked == binding and rig.authority
            if rig.ticket is None:
                return {'state': 'none', 'ticket': None, 'receipt': None,
                        'revision': self.revision, 'verification': 'verified_current'}
            return self.reconcile_ticket(checked, ticket=rig.ticket, owner_guard=owner_guard, cancel=cancel)
        def local_disconnect(self, checked, *, expected_revision, owner_guard, cancel):
            assert checked == binding and rig.authority and owner_guard() is True and not cancel.is_set()
            if expected_revision != self.revision:
                raise StoreError('revision_conflict')
            self.phase, self.revision = 'disconnected', self.revision + 1
            return {'kind': 'local', 'revision': self.revision, 'localRetired': True,
                    'remoteRevocation': 'unconfirmed'}
        def grant_descriptor(self, checked, revision):
            assert checked == binding and revision == self.revision and self.phase == 'active'
            return {'authorizationEpoch': rig.epoch, 'tokenRevision': revision}
        def admit_refresh(self, checked, revision, **kwargs):
            assert rig.authority and checked == binding and revision == self.revision
            assert kwargs['ticket'] == rig.ticket and rig.stage == 'prepared'
            assert kwargs['owner_guard']() is True
            assert rig.guards[-1] == 'cheap'
            if self.phase != 'active':
                raise StoreError('connection_unavailable')
            self.phase, rig.stage = 'refreshing', 'admitted'
            rig.lease = Lease(rig)
            return rig.lease
        def commit_refresh(self, checked, **kwargs):
            assert rig.authority and checked == binding and self.phase == 'refreshing'
            assert kwargs['owner_guard']() is True
            assert kwargs['ticket']['authorizationEpoch'] == rig.lease.authorization_epoch
            assert kwargs['identity_evidence'] == rig.lease.identity_evidence
            rig.commits.append(kwargs)
            self.phase, self.revision, rig.stage = 'active', 2, 'completed'
            return 2
    rig.store = Store()
    owner = auth.OwnerContext(binding.profile_locator, binding.organization_id, binding.owner_id,
        'a' * 64, object(), object(), durable, authority, lambda: rig.fresh, cheap)
    rig.tokens = {'access_token': 'synthetic-new-access', 'refresh_token': 'synthetic-new-refresh',
                  'token_type': 'Bearer', 'expires_in': 3600}
    def http(url, form=None):
        assert not rig.authority and rig.store.phase == 'refreshing'
        rig.calls.append((url, form))
        return dict(rig.tokens)
    rig.service = auth.SubscriptionAuthService(rig.store,
        SimpleNamespace(installation_id=lambda: binding.host_id, inspect=lambda: None), owner, synthetic=True, http=http)
    return rig


def refresh(rig):
    prepared = rig.service.prepare_operation('refresh', 1)
    return rig.service.execute_operation(prepared['ticket'])


def test_refresh_omits_scope_retains_identity_and_rotates_revision(refresh_rig):
    rig = refresh_rig
    result = refresh(rig)
    assert result['revision'] == result['catalogRevision'] == 2
    assert result['state'] == 'completed' and result['ticket'] == rig.ticket
    assert rig.calls == [(auth.TOKEN, {'grant_type': 'refresh_token', 'client_id': 'oaiapp_synthetic',
                                      'refresh_token': 'synthetic-old-refresh', 'resource': auth.RESOURCE})]
    assert rig.commits[0]['id_token'] is None
    assert rig.lease.closed and not rig.service._leases


@pytest.mark.parametrize('mutation', [
    {'refresh_token': 'synthetic-old-refresh'}, {'refresh_token': ''}, {'refresh_token': 'x' * 16385},
    {'access_token': '\n'}, {'token_type': 'MAC'}, {'expires_in': True}, {'expires_in': 604801},
    {'scope': 'openid'}, {'scope': None}, {'id_token': None},
    {'scope': auth.SCOPES + ' openid'}, {'scope': auth.SCOPES.replace(' ', '\n', 1)},
])
def test_invalid_refresh_response_never_rolls_back_or_retries(refresh_rig, mutation):
    rig = refresh_rig
    rig.tokens.update(mutation)
    with pytest.raises(auth.AuthError):
        refresh(rig)
    assert rig.store.phase == 'refreshing' and not rig.commits and len(rig.calls) == 1
    assert refresh(rig)['state'] == 'admitted'
    assert len(rig.calls) == 1


def test_ambiguous_http_never_resends(refresh_rig):
    rig = refresh_rig
    def fail(*args):
        rig.calls.append('sent')
        raise RuntimeError('synthetic-secret')
    rig.service._http = fail
    with pytest.raises(auth.AuthError) as error:
        refresh(rig)
    assert 'synthetic-secret' not in str(error.value)
    assert refresh(rig)['state'] == 'admitted'
    assert rig.calls == ['sent'] and not rig.commits


@pytest.mark.parametrize('change', ['owner', 'source', 'retirement'])
def test_inflight_owner_source_retirement_fences_commit(refresh_rig, change):
    rig = refresh_rig
    def http(*args):
        if change == 'owner':
            rig.live = False
        elif change == 'source':
            rig.fresh = False
        else:
            auth.cancel_transport(rig.service._owner.transport,
                                  generation=rig.service._owner.owner_generation)
        return rig.tokens
    rig.service._http = http
    with pytest.raises(auth.AuthError):
        refresh(rig)
    assert not rig.commits and rig.store.phase == 'refreshing'


def test_blocking_http_does_not_block_retirement(refresh_rig):
    rig = refresh_rig
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    def http(*args):
        entered.set()
        assert release.wait(3)
        return rig.tokens
    rig.service._http = http
    def run():
        try:
            refresh(rig)
        except auth.AuthError:
            pass
        finally:
            finished.set()
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(3)
        auth.cancel_transport(rig.service._owner.transport, generation=rig.service._owner.owner_generation)
        assert rig.lease.closed
    finally:
        release.set()
        worker.join(3)
    assert finished.is_set() and not rig.commits


@pytest.fixture(scope='module')
def signed_fixture():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid='synthetic-key', alg='RS256', use='sig')
    def sign(**changes):
        claims = {'iss': auth.ISSUER, 'sub': 'synthetic-account', 'aud': 'oaiapp_synthetic',
                  'iat': int(time.time()), 'exp': int(time.time()) + 600}
        claims.update(changes)
        return jwt.encode(claims, key, algorithm='RS256', headers={'kid': 'synthetic-key'})
    verifier = auth.TokenVerifier(lambda url: {'keys': [jwk]})
    evidence = {'subject': 'synthetic-account', 'audience': 'oaiapp_synthetic',
                'nonce': 'synthetic-nonce', 'authTime': None, 'authorizedParty': None}
    return verifier, sign, evidence


def test_refresh_optional_claims_and_access_hash(signed_fixture):
    verifier, sign, evidence = signed_fixture
    assert verifier.verify_refresh(sign(), 'oaiapp_synthetic', evidence, 'synthetic-access')
    expected = base64.urlsafe_b64encode(hashlib.sha256(b'synthetic-access').digest()[:16]).rstrip(b'=').decode()
    assert verifier.verify_refresh(sign(at_hash=expected, nonce='synthetic-nonce'),
                                   'oaiapp_synthetic', evidence, 'synthetic-access')
    with pytest.raises(auth.AuthError):
        verifier.verify_initial(sign(), 'oaiapp_synthetic', 'synthetic-nonce', 'synthetic-access')


@pytest.mark.parametrize('claims', [
    {'sub': 'wrong'}, {'aud': 'wrong'}, {'nonce': 'wrong'}, {'auth_time': 1}, {'azp': 'wrong'},
    {'at_hash': 'wrong'}, {'exp': 1}, {'iat': True}, {'iat': 9999999999},
])
def test_refresh_supplied_claim_contradictions_rejected(signed_fixture, claims):
    verifier, sign, evidence = signed_fixture
    with pytest.raises(auth.AuthError):
        verifier.verify_refresh(sign(**claims), 'oaiapp_synthetic', evidence, 'synthetic-access')


def test_initial_evidence_comes_only_from_verified_id(signed_fixture):
    verifier, sign, _ = signed_fixture
    result = verifier.verify_initial(sign(nonce='synthetic-nonce', auth_time=1, azp='oaiapp_synthetic'),
                                     'oaiapp_synthetic', 'synthetic-nonce', 'synthetic-access')
    assert result['authTime'] == 1 and result['authorizedParty'] == 'oaiapp_synthetic'
    with pytest.raises(auth.AuthError):
        verifier.verify_initial(sign(nonce='synthetic-nonce', at_hash='wrong'),
                                'oaiapp_synthetic', 'synthetic-nonce', 'synthetic-access')


def test_concurrent_refresh_is_single_admission(refresh_rig):
    rig = refresh_rig
    entered, release = threading.Event(), threading.Event()
    outcomes = []
    def http(*args):
        rig.calls.append('sent')
        entered.set()
        assert release.wait(3)
        return rig.tokens
    rig.service._http = http
    def run():
        try:
            outcomes.append(refresh(rig))
        except auth.AuthError as error:
            outcomes.append(error.reason)
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(3)
        assert refresh(rig)['state'] == 'admitted'
        assert not rig.lease.closed
        assert rig.calls == ['sent']
    finally:
        release.set()
        worker.join(3)
    assert len(outcomes) == 1 and outcomes[0]['state'] == 'completed'
    assert len(rig.commits) == 1


@pytest.mark.parametrize('claims', [
    {'auth_time': True}, {'auth_time': -1}, {'auth_time': 9999999999},
    {'azp': 'wrong'}, {'at_hash': 'wrong'},
])
def test_initial_supplied_identity_contradictions_rejected(signed_fixture, claims):
    verifier, sign, _ = signed_fixture
    with pytest.raises(auth.AuthError):
        verifier.verify_initial(sign(nonce='synthetic-nonce', **claims),
                                'oaiapp_synthetic', 'synthetic-nonce', 'synthetic-access')


def test_refresh_closes_prior_capabilities_without_poisoning_new_operation(refresh_rig):
    rig = refresh_rig
    old = Lease(rig)
    rig.service._leases.add(old)
    original = rig.store.admit_refresh
    def admit(*args, **kwargs):
        assert old.closed and not rig.service._lease_cancel.is_set()
        return original(*args, **kwargs)
    rig.store.admit_refresh = admit
    assert refresh(rig)['state'] == 'completed'
    assert old.closed and not rig.service._lease_cancel.is_set()


def test_source_change_during_refresh_publication_never_returns_success(refresh_rig):
    rig = refresh_rig
    original = rig.store.commit_refresh
    def commit(*args, **kwargs):
        revision = original(*args, **kwargs)
        rig.fresh = False
        return revision
    rig.store.commit_refresh = commit
    with pytest.raises(auth.AuthError, match='setup_unavailable'):
        refresh(rig)
    assert rig.store.phase == 'active' and rig.store.revision == 2
    assert len(rig.calls) == len(rig.commits) == 1
    assert rig.commits[0]['ticket'] == rig.ticket
    assert rig.commits[0]['ticket']['authorizationEpoch'] == rig.lease.authorization_epoch
    # Durable exact operation remains reconcilable, never replay the POST.
    with pytest.raises(auth.AuthError, match='setup_unavailable'):
        refresh(rig)
    assert len(rig.calls) == 1


def test_lost_prepare_ack_and_unused_ticket_restart_never_sends(refresh_rig):
    rig = refresh_rig
    first = rig.service.prepare_operation('refresh', 1)
    assert first['state'] == 'prepared' and first['catalogRevision'] is None
    assert rig.service.prepare_operation('refresh', 1) == first
    restarted = auth.SubscriptionAuthService(rig.store, rig.service._host, rig.service._owner,
        synthetic=True, http=rig.service._http)
    hint = restarted.status()['operation']
    assert hint['ticket'] == first['ticket'] and hint['verification'] == 'metadata_only'
    assert restarted.reconcile_operation(hint['ticket'])['state'] == 'prepared'
    assert not rig.calls and not rig.commits
    cancelled = restarted.cancel_operation(hint['ticket'])
    assert cancelled['state'] == 'none' and cancelled['catalogRevision'] is None
    with pytest.raises(auth.AuthError, match='ticket_retired'):
        restarted.execute_operation(hint['ticket'])
    assert not rig.calls


@pytest.mark.parametrize('stage', ['allocating', 'prepared', 'admitted', 'uncertain', 'local_retired'])
def test_actual_service_nonterminal_recovery_never_grants_catalog_revision(refresh_rig, stage):
    rig = refresh_rig
    ticket = rig.service.prepare_operation('refresh', 1)['ticket']
    rig.stage = stage
    result = rig.service.recover_operation_current()
    assert result['state'] == stage and result['ticket'] == ticket
    assert result['catalogRevision'] is None and not rig.calls


def test_completed_refresh_replay_and_reconcile_are_local_with_current_catalog_fence(refresh_rig):
    rig = refresh_rig
    first = refresh(rig)
    assert first['catalogRevision'] == 2 and len(rig.calls) == 1
    replay = rig.service.execute_operation(first['ticket'])
    reconciled = rig.service.reconcile_operation(first['ticket'])
    recovered = rig.service.recover_operation_current()
    assert replay == reconciled == first
    assert recovered == dict(first, cleanupPending=False)
    assert len(rig.calls) == len(rig.commits) == 1
    original = rig.store.grant_descriptor
    def wrong_epoch(*args):
        return dict(original(*args), authorizationEpoch=str(uuid.uuid4()))
    rig.store.grant_descriptor = wrong_epoch
    with pytest.raises(auth.AuthError, match='operation_mismatch'):
        rig.service.reconcile_operation(first['ticket'])
    assert len(rig.calls) == 1


@pytest.mark.parametrize('field,value', [
    ('sequence', '01'), ('nonce', 'z' * 64), ('version', True),
    ('priorStoreRevision', True), ('sourceRevision', 'd' * 64),
])
def test_altered_ticket_never_admits_http(refresh_rig, field, value):
    rig = refresh_rig
    ticket = rig.service.prepare_operation('refresh', 1)['ticket']
    ticket[field] = value
    with pytest.raises(auth.AuthError):
        rig.service.execute_operation(ticket)
    assert not rig.calls and not rig.commits


def test_passive_status_does_not_prepare_verify_grant_or_recover(refresh_rig):
    rig = refresh_rig
    ticket = rig.service.prepare_operation('refresh', 1)['ticket']
    def forbidden(*args, **kwargs):
        raise AssertionError('Passive status attempted mutation or native grant access')
    rig.store.prepare_ticket = rig.store.grant_descriptor = rig.store.reconcile_transition = forbidden
    assert rig.service.status()['operation']['ticket'] == ticket
    assert not rig.calls


def test_old_source_ticket_local_cleanup_never_authorizes_execute_or_catalog(refresh_rig):
    rig = refresh_rig
    ticket = rig.service.prepare_operation('refresh', 1)['ticket']
    current_owner = replace(rig.service._owner, source_digest='d' * 64)
    restarted = auth.SubscriptionAuthService(rig.store, rig.service._host, current_owner,
        synthetic=True, http=rig.service._http)
    with pytest.raises(auth.AuthError, match='stale_attempt'):
        restarted.execute_operation(ticket)
    assert restarted.reconcile_operation(ticket)['catalogRevision'] is None
    assert restarted.cancel_operation(ticket)['state'] == 'none'
    assert not rig.calls


def test_old_source_completed_receipt_recovery_has_no_catalog_authority(refresh_rig):
    rig = refresh_rig
    result = refresh(rig)
    current_owner = replace(rig.service._owner, source_digest='d' * 64)
    restarted = auth.SubscriptionAuthService(rig.store, rig.service._host, current_owner,
        synthetic=True, http=rig.service._http)
    def forbidden(*args, **kwargs):
        raise AssertionError('Historical result must not acquire catalog grant')
    rig.store.grant_descriptor = forbidden
    assert restarted.reconcile_operation(result['ticket'])['catalogRevision'] is None
    assert restarted.recover_operation_current()['catalogRevision'] is None
    assert len(rig.calls) == 1


@pytest.mark.parametrize('stage', ['admitted', 'uncertain'])
def test_cancel_never_discards_admitted_or_uncertain_operation(refresh_rig, stage):
    rig = refresh_rig
    ticket = rig.service.prepare_operation('refresh', 1)['ticket']
    rig.stage = stage
    lease = Lease(rig)
    rig.service._leases.add(lease)
    with pytest.raises(auth.AuthError, match='ticket_conflict'):
        rig.service.cancel_operation(ticket)
    assert rig.stage == stage and not lease.closed and not rig.calls


@pytest.mark.parametrize('stage', ['admitted', 'uncertain'])
def test_explicit_disconnect_locally_retires_stranded_refresh_without_new_ticket(refresh_rig, stage):
    rig = refresh_rig
    ticket = rig.service.prepare_operation('refresh', 1)['ticket']
    rig.stage, rig.store.phase = stage, 'refreshing'
    result = rig.service.prepare_operation('revoke', 1)
    assert result['state'] == 'local_retired' and result['ticket'] is None
    assert result['catalogRevision'] is None and result['receipt']['localRetired']
    assert result['receipt']['remoteRevocation'] == 'unconfirmed'
    assert rig.ticket == ticket and rig.stage == stage
    assert rig.store.phase == 'disconnected' and not rig.calls


@pytest.mark.parametrize('stage', ['admitted', 'uncertain'])
def test_stranded_refresh_disconnect_refuses_stale_revision(refresh_rig, stage):
    rig = refresh_rig
    rig.service.prepare_operation('refresh', 1)
    rig.stage, rig.store.phase = stage, 'refreshing'
    with pytest.raises(auth.AuthError, match='stale_attempt'):
        rig.service.prepare_operation('revoke', 0)
    assert rig.store.phase == 'refreshing' and rig.store.revision == 1 and not rig.calls


def test_conflicting_prepared_refresh_is_not_broad_local_disconnect_authority(refresh_rig):
    rig = refresh_rig
    rig.service.prepare_operation('refresh', 1)
    with pytest.raises(auth.AuthError, match='ticket_conflict'):
        rig.service.prepare_operation('revoke', 1)
    assert rig.store.phase == 'active' and rig.stage == 'prepared' and not rig.calls


@pytest.mark.parametrize('pending', [False, True])
def test_explicit_recovery_preserves_durable_retirement_receipt_despite_cleanup(refresh_rig, pending):
    rig = refresh_rig
    receipt = {'kind': 'local', 'revision': 2, 'localRetired': True,
               'remoteRevocation': 'unconfirmed'}
    rig.store.phase, rig.store.revision, rig.store.cleanup_pending = 'disconnected', 2, pending
    def recover(checked, *, owner_guard, cancel):
        assert rig.authority and owner_guard() is True and not cancel.is_set()
        return {'state': 'local_retired', 'ticket': None, 'receipt': dict(receipt),
                'revision': 2, 'verification': 'verified_current'}
    rig.store.reconcile_transition = recover
    result = rig.service.recover_operation_current()
    assert result['receipt'] == receipt and result['state'] == 'local_retired'
    assert result['cleanupPending'] is pending and result['catalogRevision'] is None
    assert not rig.calls and not rig.commits


@pytest.mark.parametrize('value', [None, 0, 1, 'false'])
def test_explicit_recovery_rejects_non_boolean_cleanup_pending(refresh_rig, value):
    rig = refresh_rig
    rig.store.cleanup_pending = value
    with pytest.raises(auth.AuthError, match='storage_recovery_required'):
        rig.service.recover_operation_current()
    assert not rig.calls and not rig.commits

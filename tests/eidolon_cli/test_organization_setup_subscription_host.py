"""Synthetic host-authority and durable admission tests; no providers or vaults."""
import json
import multiprocessing
import os
from pathlib import Path
import uuid

import pytest

POSIX_ONLY = pytest.mark.skipif(os.name != 'posix', reason='Positive synthetic host filesystem fixtures require POSIX; production store refuses unsupported platforms')

from eidolon_cli.organization_setup_subscription_host import (
    HostAuthority, HostError, INSTALLATION_NAME, PendingAttempts,
)


def uid():
    return str(uuid.uuid4())


@pytest.fixture
def host(tmp_path):
    root = tmp_path / 'root'
    root.mkdir(mode=0o700)
    authority = HostAuthority.capture(lambda: root, lambda: root)
    return root, authority, PendingAttempts(authority, synthetic=True, clock=lambda: 100)


def begin(manager, **changes):
    arguments = dict(attempt_id=uid(), organization_id=uid(), owner_id=uid(),
                     expected_store_revision=4, source_digest='a' * 64, expires_at=200)
    arguments.update(changes)
    return manager.begin(**arguments)


@POSIX_ONLY
def test_passive_status_does_not_create_identity(host):
    root, _, manager = host
    assert manager.inspect() is None
    assert not (root / INSTALLATION_NAME).exists()


@POSIX_ONLY
def test_stable_installation_and_duplicate_begin(host):
    _, authority, manager = host
    attempt = begin(manager)
    identity = manager.installation_id()
    assert uuid.UUID(identity).version == 4
    assert attempt.authorization_host_id == uuid.UUID(identity).urn
    other = PendingAttempts(authority, synthetic=True, clock=lambda: 100)
    assert other.installation_id() == identity
    assert other.inspect() == attempt
    with pytest.raises(HostError, match='stale_attempt'):
        begin(other, attempt_id=attempt.attempt_id)
    with pytest.raises(HostError, match='attempt_pending'):
        begin(other)


@pytest.mark.linux_only
@pytest.mark.parametrize('stage', [None, 'cancelled', 'pending', 'exchange_admitted', 'publishing', 'published'])
def test_legacy_provider_identity_requires_exact_profile_consent(host, stage):
    from dataclasses import asdict
    from eidolon_cli.organization_setup_subscription_host import PendingAttempt
    from eidolon_cli.organization_setup_subscription_store import _json

    root, authority, manager = host
    identity = uid()
    with manager._locked(create=True):
        manager._io._write('initialized', b'1')
        manager._io._write('identity.json', _json({'installation_id': identity}))
        if stage is not None:
            old = PendingAttempt(uid(), uid(), uid(), 4, 'a' * 64, 99, stage,
                                 'c' * 64 if stage in {'publishing', 'published'} else None)
            legacy = asdict(old)
            for key in ('authorization_host_id', 'host_identity_migration', 'authorization_binding_digest'):
                legacy.pop(key)
            manager._io._write(manager._name, _json(legacy))
    original = {p.name: p.read_bytes() for p in (root / INSTALLATION_NAME).iterdir() if p.is_file()}
    assert manager.installation_id() == identity
    assert manager.host_identity_state('c' * 64) == 'migration_required'
    with pytest.raises(HostError, match='host_identity_migration_required'):
        begin(manager, binding_digest='c' * 64)
    assert original == {p.name: p.read_bytes() for p in (root / INSTALLATION_NAME).iterdir() if p.is_file()}
    if stage in {'pending', 'exchange_admitted', 'publishing'}:
        with pytest.raises(HostError, match='attempt_pending'):
            begin(manager, binding_digest='c' * 64, confirm_host_identity_change=True)
        assert original == {p.name: p.read_bytes() for p in (root / INSTALLATION_NAME).iterdir() if p.is_file()}
        if stage == 'publishing':
            return
        manager.cancel(old.attempt_id)

    predecessor = (root / INSTALLATION_NAME / manager._name).read_bytes() if stage is not None else None
    attempt = begin(manager, binding_digest='c' * 64, confirm_host_identity_change=True)
    if predecessor is not None:
        archive = root / INSTALLATION_NAME / 'ticket-archive' / (manager._name + '.' + old.attempt_id + '.host-migration')
        assert json.loads(archive.read_bytes()) == {'version': 1, 'remoteOutcome': 'unknown',
                                                  'recordJson': predecessor.decode('utf-8')}
    assert attempt.authorization_host_id == uuid.UUID(identity).urn
    assert attempt.host_identity_migration is True
    manager.cancel(attempt.attempt_id)
    restarted = PendingAttempts(authority, synthetic=True, clock=lambda: 100)
    assert restarted.host_identity_state('c' * 64) == 'migration_required'
    assert restarted.host_identity_state() == 'migration_required'
    with pytest.raises(HostError, match='host_identity_migration_required'):
        begin(restarted, binding_digest='c' * 64)
    profiles = root / 'profiles'
    profiles.mkdir(mode=0o700)
    profile = profiles / 'other'
    profile.mkdir(mode=0o700)
    other = PendingAttempts(authority, profile, synthetic=True, clock=lambda: 100)
    assert other.host_identity_state('c' * 64) == 'migration_required'
    assert other.host_identity_state() == 'migration_required'

    attempt = begin(restarted, binding_digest='c' * 64, confirm_host_identity_change=True)
    restarted.admit_exchange(attempt.attempt_id)
    with restarted.publication_guard(attempt.attempt_id, binding_digest='c' * 64) as publication:
        publication.commit(5)
    assert restarted.host_identity_state('c' * 64) == 'current'
    assert other.host_identity_state('c' * 64) == 'migration_required'
    proof = next((root / INSTALLATION_NAME).glob('oauth-consent-*')).read_bytes()
    returning = begin(restarted, binding_digest='c' * 64)
    assert returning.authorization_host_id == attempt.authorization_host_id
    assert returning.host_identity_migration is False
    restarted.admit_exchange(returning.attempt_id)
    with restarted.publication_guard(returning.attempt_id, binding_digest='c' * 64) as publication:
        publication.commit(5)
    assert next((root / INSTALLATION_NAME).glob('oauth-consent-*')).read_bytes() == proof
    assert (root / INSTALLATION_NAME / 'identity.json').read_bytes() == original['identity.json']


@pytest.mark.linux_only
@pytest.mark.parametrize('host_id', [
    'not-a-host', str(uuid.uuid4()), uuid.uuid1().urn,
    uuid.uuid4().urn.upper(), 'urn:uuid:' + uuid.uuid4().hex, uuid.uuid4().urn,
])
def test_invalid_provider_identity_never_rewrites_or_begins(host, host_id):
    root, _, manager = host
    attempt = begin(manager)
    manager.cancel(attempt.attempt_id)
    path = root / INSTALLATION_NAME / 'oauth-host.json'
    value = json.loads(path.read_text())
    value['host_id'] = host_id
    path.write_text(json.dumps(value))
    original = path.read_bytes()
    with pytest.raises(HostError, match='recovery_required'):
        manager.host_identity_state()
    with pytest.raises(HostError, match='recovery_required'):
        begin(manager, confirm_host_identity_change=True)
    assert path.read_bytes() == original
    assert manager.inspect().attempt_id == attempt.attempt_id


@POSIX_ONLY
def test_admission_once_survives_new_process_object(host):
    _, authority, manager = host
    attempt = begin(manager)
    assert manager.admit_exchange(attempt.attempt_id).stage == 'exchange_admitted'
    other = PendingAttempts(authority, synthetic=True, clock=lambda: 101)
    with pytest.raises(HostError, match='exchange_already_admitted'):
        other.admit_exchange(attempt.attempt_id)
    with pytest.raises(HostError, match='attempt_pending'):
        begin(other)


@POSIX_ONLY
def test_process_loss_waits_expiry_and_fences_old_attempt(host):
    _, authority, manager = host
    attempt = begin(manager)
    manager.admit_exchange(attempt.attempt_id)
    later = PendingAttempts(authority, synthetic=True, clock=lambda: 201)
    replacement = begin(later, expires_at=300)
    assert replacement.attempt_id != attempt.attempt_id
    with pytest.raises(HostError, match='stale_attempt'):
        manager.cancel(attempt.attempt_id)


@POSIX_ONLY
def test_cancel_before_publish(host):
    _, _, manager = host
    attempt = begin(manager)
    manager.admit_exchange(attempt.attempt_id)
    manager.cancel(attempt.attempt_id)
    with pytest.raises(HostError, match='stale_attempt'):
        with manager.publication_guard(attempt.attempt_id):
            pytest.fail('Cancelled attempt published')


@POSIX_ONLY
def test_publish_requires_explicit_revision_commit(host):
    _, _, manager = host
    attempt = begin(manager)
    manager.admit_exchange(attempt.attempt_id)
    with manager.publication_guard(attempt.attempt_id) as publication:
        assert publication.record.expected_store_revision == 4
        publication.commit(5)
    assert manager.inspect().stage == 'published'
    with pytest.raises(HostError, match='stale_attempt'):
        with manager.publication_guard(attempt.attempt_id):
            pytest.fail('Duplicate publication')


@pytest.mark.parametrize('mode', ['missing_commit', 'wrong_revision', 'exception'])
@POSIX_ONLY
def test_uncertain_publication_remains_fenced(host, mode):
    _, _, manager = host
    attempt = begin(manager)
    manager.admit_exchange(attempt.attempt_id)
    with pytest.raises(HostError):
        with manager.publication_guard(attempt.attempt_id) as publication:
            if mode == 'wrong_revision':
                publication.commit(7)
            elif mode == 'exception':
                raise RuntimeError('synthetic secret must not escape')
    assert manager.inspect().stage == 'publishing'
    with pytest.raises(HostError, match='stale_attempt'):
        manager.cancel(attempt.attempt_id)


@POSIX_ONLY
def test_expired_attempt_cannot_admit_or_publish(host):
    _, authority, manager = host
    attempt = begin(manager)
    later = PendingAttempts(authority, synthetic=True, clock=lambda: 200)
    with pytest.raises(HostError, match='attempt_expired'):
        later.admit_exchange(attempt.attempt_id)


@POSIX_ONLY
def test_profile_switch_and_immutable_launch_home(host, monkeypatch):
    root, authority, manager = host
    profiles = root / 'profiles'
    profiles.mkdir(mode=0o700)
    profile = profiles / 'work'
    profile.mkdir(mode=0o700)
    monkeypatch.setenv('EIDOLON_HOME', '/untrusted')
    work = PendingAttempts(authority, profile, synthetic=True, clock=lambda: 100)
    first = begin(manager)
    second = begin(work)
    assert manager.installation_id() == work.installation_id()
    assert manager.inspect() == first
    assert work.inspect() == second
    assert authority.profile == root


@pytest.mark.parametrize('relative', ['profiles/Bad', 'profiles/work/nested', 'elsewhere'])
@POSIX_ONLY
def test_reject_out_of_scope_profile(host, relative):
    root, authority, _ = host
    target = root / relative
    target.mkdir(mode=0o700, parents=True)
    with pytest.raises(HostError, match='invalid_authority'):
        PendingAttempts(authority, target, synthetic=True).inspect()


@POSIX_ONLY
def test_reject_symlink_and_permissive_root(host):
    root, authority, manager = host
    root.chmod(0o755)
    with pytest.raises(HostError, match='unsafe_filesystem'):
        manager.inspect()
    root.chmod(0o700)
    profiles = root / 'profiles'
    profiles.mkdir(mode=0o700)
    (profiles / 'alias').symlink_to(root, target_is_directory=True)
    with pytest.raises(HostError, match='unsafe_filesystem'):
        PendingAttempts(authority, profiles / 'alias', synthetic=True).inspect()


@POSIX_ONLY
def test_missing_identity_never_recreated(host):
    root, _, manager = host
    begin(manager)
    (root / INSTALLATION_NAME / 'identity.json').unlink()
    with pytest.raises(HostError, match='recovery_required'):
        begin(manager)


@POSIX_ONLY
def test_existing_private_store_blocks_new_identity(host):
    root, _, manager = host
    (root / '.setup-subscription-private').mkdir(mode=0o700)
    with pytest.raises(HostError, match='recovery_required'):
        begin(manager)
    assert not (root / INSTALLATION_NAME / 'identity.json').exists()


@POSIX_ONLY
def test_missing_pending_record_does_not_reset_admission(host):
    root, authority, manager = host
    begin(manager)
    (root / INSTALLATION_NAME / (authority.profile_hash + '.json')).unlink()
    with pytest.raises(HostError, match='recovery_required'):
        begin(manager)


@POSIX_ONLY
def test_metadata_allowlist_and_fixed_errors(host):
    root, authority, manager = host
    attempt = begin(manager)
    data = json.loads((root / INSTALLATION_NAME / (authority.profile_hash + '.json')).read_text())
    assert set(data) == {'attempt_id', 'organization_id', 'owner_id',
                         'expected_store_revision', 'source_digest', 'expires_at', 'stage',
                         'publication_binding_digest', 'authorization_host_id',
                         'host_identity_migration', 'authorization_binding_digest'}
    with pytest.raises(HostError) as error:
        manager.admit_exchange('secret-code-verifier')
    assert 'secret-code-verifier' not in str(error.value)


def _race_begin(root, start, results):
    authority = HostAuthority.capture(lambda: Path(root), lambda: Path(root))
    manager = PendingAttempts(authority, synthetic=True, clock=lambda: 100)
    start.wait()
    try:
        begin(manager)
        results.put('success')
    except HostError as error:
        results.put(error.reason)


@POSIX_ONLY
def test_cross_process_begin_race_has_one_winner(host):
    root, _, _ = host
    context = multiprocessing.get_context('spawn')
    start, results = context.Event(), context.Queue()
    workers = [context.Process(target=_race_begin, args=(str(root), start, results)) for _ in range(2)]
    for worker in workers:
        worker.start()
    start.set()
    try:
        assert sorted(results.get(timeout=15) for _ in workers) == ['attempt_pending', 'success']
    finally:
        for worker in workers:
            worker.join(timeout=15)
            if worker.is_alive():
                worker.terminate()
                worker.join()
        results.close()
    assert all(worker.exitcode == 0 for worker in workers)


def _race_admit(root, attempt_id, start, results):
    authority = HostAuthority.capture(lambda: Path(root), lambda: Path(root))
    manager = PendingAttempts(authority, synthetic=True, clock=lambda: 100)
    start.wait()
    try:
        manager.admit_exchange(attempt_id)
        results.put('success')
    except HostError as error:
        results.put(error.reason)


@POSIX_ONLY
def test_cross_process_exchange_is_single_use(host):
    root, _, manager = host
    attempt = begin(manager)
    context = multiprocessing.get_context('spawn')
    start, results = context.Event(), context.Queue()
    workers = [context.Process(target=_race_admit,
               args=(str(root), attempt.attempt_id, start, results)) for _ in range(2)]
    for worker in workers:
        worker.start()
    start.set()
    try:
        assert sorted(results.get(timeout=15) for _ in workers) == ['exchange_already_admitted', 'success']
    finally:
        for worker in workers:
            worker.join(timeout=15)
            if worker.is_alive():
                worker.terminate()
                worker.join()
        results.close()
    assert all(worker.exitcode == 0 for worker in workers)


@POSIX_ONLY
def test_cancel_serializes_after_local_publication(host):
    import threading
    _, authority, manager = host
    attempt = begin(manager)
    manager.admit_exchange(attempt.attempt_id)
    entering, finished = threading.Event(), threading.Event()
    results = []

    def cancel():
        other = PendingAttempts(authority, synthetic=True, clock=lambda: 100)
        entering.set()
        try:
            other.cancel(attempt.attempt_id)
        except HostError as error:
            results.append(error.reason)
        finally:
            finished.set()

    with manager.publication_guard(attempt.attempt_id) as publication:
        worker = threading.Thread(target=cancel)
        worker.start()
        assert entering.wait(5)
        assert not finished.wait(0.05)
        publication.commit(5)
    worker.join(timeout=5)
    assert finished.is_set()
    assert results == ['stale_attempt']
    assert manager.inspect().stage == 'published'


def _uncertain_attempt(manager):
    attempt = begin(manager)
    manager.admit_exchange(attempt.attempt_id)
    with pytest.raises(HostError, match='recovery_required'):
        with manager.publication_guard(attempt.attempt_id):
            pass
    assert manager.inspect().stage == 'publishing'
    return attempt


@POSIX_ONLY
def test_reconcile_exact_uncertain_publication_after_expiry(host):
    _, authority, manager = host
    attempt = _uncertain_attempt(manager)
    expired = PendingAttempts(authority, synthetic=True, clock=lambda: 300)
    with expired.reconciliation_guard(attempt.attempt_id) as publication:
        assert publication.record.organization_id == attempt.organization_id
        assert publication.record.owner_id == attempt.owner_id
        assert publication.record.source_digest == attempt.source_digest
        publication.commit(5)  # Synthetic exact authenticated store proof.
    assert expired.inspect().stage == 'published'
    with expired.reconciliation_guard(attempt.attempt_id) as publication:
        publication.commit(5)  # Exact proof is still mandatory on replay.
    with pytest.raises(HostError):
        expired.admit_exchange(attempt.attempt_id)


@pytest.mark.parametrize('stage', ['pending', 'exchange_admitted', 'cancelled'])
@POSIX_ONLY
def test_reconcile_rejects_every_nonpublishing_stage(host, stage):
    _, _, manager = host
    attempt = begin(manager)
    if stage in {'exchange_admitted', 'published'}:
        manager.admit_exchange(attempt.attempt_id)
    if stage == 'published':
        with manager.publication_guard(attempt.attempt_id) as publication:
            publication.commit(5)
    elif stage == 'cancelled':
        manager.cancel(attempt.attempt_id)
    with pytest.raises(HostError, match='stale_attempt'):
        with manager.reconciliation_guard(attempt.attempt_id):
            pytest.fail('Wrong durable stage admitted')
    assert manager.inspect().stage == stage


@POSIX_ONLY
def test_reconcile_rejects_wrong_attempt(host):
    _, _, manager = host
    attempt = _uncertain_attempt(manager)
    with pytest.raises(HostError, match='stale_attempt'):
        with manager.reconciliation_guard(uid()):
            pytest.fail('Wrong attempt admitted')
    assert manager.inspect().attempt_id == attempt.attempt_id
    assert manager.inspect().stage == 'publishing'


@pytest.mark.parametrize('mode', ['missing_commit', 'wrong_revision', 'exception'])
@POSIX_ONLY
def test_failed_reconciliation_preserves_uncertain_fence(host, mode):
    _, _, manager = host
    attempt = _uncertain_attempt(manager)
    with pytest.raises(HostError) as error:
        with manager.reconciliation_guard(attempt.attempt_id) as publication:
            if mode == 'wrong_revision':
                publication.commit(6)
            elif mode == 'exception':
                raise RuntimeError('synthetic secret must not escape')
    assert 'synthetic secret' not in str(error.value)
    assert manager.inspect().stage == 'publishing'
    with pytest.raises(HostError, match='exchange_already_admitted'):
        manager.admit_exchange(attempt.attempt_id)


def test_windows_production_refuses_before_lock_import(tmp_path, monkeypatch):
    import builtins
    import eidolon_cli.organization_setup_subscription_store as store_module
    root = tmp_path / 'root'
    root.mkdir()
    authority = HostAuthority.capture(lambda: root, lambda: root)
    manager = PendingAttempts(authority)
    imported = builtins.__import__
    def deny_fcntl(name, *args, **kwargs):
        if name == 'fcntl':
            pytest.fail('Unsupported platform attempted a POSIX lock import')
        return imported(name, *args, **kwargs)
    monkeypatch.setattr(store_module.sys, 'platform', 'win32')
    monkeypatch.setattr(builtins, '__import__', deny_fcntl)
    with pytest.raises(HostError, match='filesystem_protection_unavailable'):
        manager.inspect()
    assert not (root / INSTALLATION_NAME).exists()


@POSIX_ONLY
def test_published_rename_fsync_failure_can_reconcile_exactly(host, monkeypatch):
    _, _, manager = host
    attempt = _uncertain_attempt(manager)
    sync = manager._io._sync_directory
    def fail_sync_after_rename():
        raise OSError('synthetic fsync failure')
    monkeypatch.setattr(manager._io, '_sync_directory', fail_sync_after_rename)
    with pytest.raises(HostError, match='operation_failed'):
        with manager.reconciliation_guard(attempt.attempt_id) as publication:
            publication.commit(5)
    assert manager.inspect().stage == 'published'
    monkeypatch.setattr(manager._io, '_sync_directory', sync)
    with manager.reconciliation_guard(attempt.attempt_id) as publication:
        assert publication.record.attempt_id == attempt.attempt_id
        assert publication.record.expected_store_revision == 4
        publication.commit(5)
    assert manager.inspect().stage == 'published'
    with pytest.raises(HostError, match='exchange_already_admitted'):
        manager.admit_exchange(attempt.attempt_id)


@pytest.mark.linux_only
@pytest.mark.parametrize('failure', ['lost_ack', 'collision'])
def test_migration_predecessor_archive_failure_cannot_replace_pending_slot(host, monkeypatch, failure):
    from eidolon_cli.organization_setup_subscription_store import _json
    root, _, manager = host
    with manager._locked(create=True):
        manager._io._write('initialized', b'1')
        manager._io._write('identity.json', _json({'installation_id': uid()}))
    attempt = begin(manager, binding_digest='c' * 64, confirm_host_identity_change=True)
    manager.cancel(attempt.attempt_id)
    path = root / INSTALLATION_NAME / manager._name
    original = path.read_bytes()
    name = manager._name + '.' + attempt.attempt_id + '.host-migration'
    if failure == 'collision':
        manager._io._archive_directory(create=True)
        (root / INSTALLATION_NAME / 'ticket-archive' / name).write_bytes(b'conflicting evidence')
        (root / INSTALLATION_NAME / 'ticket-archive' / name).chmod(0o600)
    else:
        sync = manager._io._archive_sync
        def lose_ack():
            sync()
            raise RuntimeError('synthetic lost acknowledgment')
        monkeypatch.setattr(manager._io, '_archive_sync', lose_ack)
    with pytest.raises(HostError):
        begin(manager, binding_digest='c' * 64, confirm_host_identity_change=True)
    assert path.read_bytes() == original
    assert manager.host_identity_state('c' * 64) == 'migration_required'
    assert not list((root / INSTALLATION_NAME).glob('oauth-consent-*'))
    if failure == 'lost_ack':
        archived = (root / INSTALLATION_NAME / 'ticket-archive' / name).read_bytes()
        assert json.loads(archived)['recordJson'].encode() == original
        monkeypatch.setattr(manager._io, '_archive_sync', sync)
        begin(manager, binding_digest='c' * 64, confirm_host_identity_change=True)
        assert (root / INSTALLATION_NAME / 'ticket-archive' / name).read_bytes() == archived

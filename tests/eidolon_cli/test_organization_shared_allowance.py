"""Shared debit conservation across processes, owner epochs and interrupted work."""
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import multiprocessing
from pathlib import Path
import shutil
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from eidolon_constants import reset_eidolon_home_override, set_eidolon_home_override
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_metadata import save_metadata
from eidolon_cli.organization_pacing import OrganizationPacingDeferred
from eidolon_cli.organization_shared_allowance import (
    SharedAllowanceError, SharedAllowanceLedger, capture_installation_scope, debit,
    normalize_configuration, physical_call_key, read_member_projection, resolve_authority,
    resolve_binding, verify_receipt,
)
from eidolon_cli.organization_store import OrganizationStore


def iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


def dimensions(calls=20, tokens=20000, cost=None):
    return {'modelCalls': calls, 'tokens': tokens, 'configuredCostUsd': cost}


def make_scope(home):
    home.mkdir(parents=True, exist_ok=True)
    (home / 'config.yaml').write_text('{}\n')
    path = home / 'organization' / 'state.db'
    metadata = save_metadata(path, display_name='Organization', owner_display_name='Owner',
                             expected_organization_id=None, expected_revision=None,
                             idempotency_key='metadata')['metadata']
    store = OrganizationStore(path, OrganizationSettings())
    return SimpleNamespace(home=home, metadata=metadata, store=store,
                           organization_id=metadata['organizationId'], owner_id=metadata['owner']['ownerId'])


@pytest.fixture
def installation(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    root = make_scope(tmp_path / 'installation')
    monkeypatch.setenv('HERMES_HOME', str(root.home))
    root.alpha = make_scope(root.home / 'profiles' / 'alpha')
    root.beta = make_scope(root.home / 'profiles' / 'beta')
    root.ledger = SharedAllowanceLedger(root.home, home=root.home)
    return root


def configuration(root, *, calls=20, tokens=20000, cost=None, protected_calls=0, protected_tokens=0, protected_cost=None):
    now = time.time()
    return {'periodId': 'period-one', 'startsAt': iso(now - 60), 'endsAt': iso(now + 3600),
            'limits': dimensions(calls * 2, tokens * 2, format(2 * Decimal(cost), 'f') if cost is not None else None),
            'protected': dimensions(protected_calls * 2, protected_tokens * 2, format(2 * Decimal(protected_cost), 'f') if protected_cost is not None else None),
            'allocations': [{'profile': name, 'organizationId': scope.organization_id, 'ownerId': scope.owner_id,
                             'limits': dimensions(calls, tokens, cost),
                             'protected': dimensions(protected_calls, protected_tokens, protected_cost)}
                            for name, scope in [('default', root), ('alpha', root.alpha)]]}


def configure(root, config=None, *, revision=0, key='initialize', **changes):
    value = configuration(root, **changes) if config is None else config
    root.ledger.configure(value, expected_revision=revision, idempotency_key=key,
                          root_organization_id=root.organization_id, root_owner_id=root.owner_id)
    return normalize_configuration(value)


def payload(index=1, *, kind='objective', request_type='request.review', cost=None, **changes):
    result = {'kind': kind, 'provider': 'local-fixture', 'model': 'bounded', 'inputLimit': 50,
              'outputLimit': 50, 'reservedCostUsd': cost}
    if kind == 'objective':
        result.update(objectiveId='objective', requestId='request', attemptToken='attempt',
                      callIndex=index, requestType=request_type)
    else:
        result.update(threadId='thread', turnId=f'turn-{index}', identityId='identity')
    return {**result, **changes}


def reserve(scope, data):
    binding = resolve_binding(scope.home, scope.organization_id)
    key = physical_call_key(binding, data)
    return binding, key, debit(binding, key, data)


def disk_state(home):
    result = {}
    for path in home.rglob('*'):
        if str(path).endswith(('-wal', '-shm')):
            continue  # WAL-aware readonly connections may maintain transient read locks.
        if path.is_dir():
            result[str(path.relative_to(home)) + '/'] = None
        elif path.suffix == '.db':
            with closing(sqlite3.connect(path)) as conn:
                result[str(path.relative_to(home))] = tuple(conn.iterdump())
        else:
            result[str(path.relative_to(home))] = path.read_bytes()
    return result


def process_reserve(home, organization_id, value):
    try:
        binding = resolve_binding(home, organization_id)
        key = physical_call_key(binding, value)
        receipt = debit(binding, key, value)
        verify_receipt(binding, receipt, key, value)
        return True
    except (SharedAllowanceError, OrganizationPacingDeferred):
        return False


def test_passive_reads_and_context_capture_never_provision_or_follow_process_root(installation, tmp_path, monkeypatch):
    root = installation
    before = disk_state(root.home)
    missing = tmp_path / 'missing'
    assert read_member_projection(missing, None)['state'] == 'unconfigured'
    assert not missing.exists()
    token = set_eidolon_home_override(root.alpha.home)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'unrelated-process-root'))
    try:
        assert resolve_authority(root.alpha.home)['root'] == root.home
        assert root.ledger.read()['state'] == 'unconfigured'
        assert resolve_binding(root.alpha.home, None) is None
        with pytest.raises(SharedAllowanceError, match='installation_owner_required'):
            SharedAllowanceLedger(root.home, home=root.alpha.home).configure(configuration(root), expected_revision=0,
                idempotency_key='forged', root_organization_id=root.organization_id, root_owner_id=root.owner_id)
        with pytest.raises(SharedAllowanceError, match='invalid_installation_root'):
            capture_installation_scope(root.alpha.home, root.alpha.home)
    finally:
        reset_eidolon_home_override(token)
    assert disk_state(root.home) == before


@pytest.mark.parametrize('dimension', ['modelCalls', 'tokens', 'configuredCostUsd'])
def test_multiprocess_debits_atomically_conserve_pool_and_allocation_envelopes(installation, dimension):
    root = installation
    options = {'calls': 2} if dimension == 'modelCalls' else {'tokens': 200} if dimension == 'tokens' else {'cost': '0.2'}
    configure(root, **options)
    work = [(str(scope.home), scope.organization_id, payload(index + 1, cost='0.1' if dimension == 'configuredCostUsd' else None))
            for scope in (root, root.alpha) for index in range(5)]
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context('spawn')) as executor:
        results = list(executor.map(process_reserve, *(zip(*work))))
    assert sum(results) == 4
    view = root.ledger.read()
    expected = {'modelCalls': 4, 'tokens': 400, 'configuredCostUsd': '0.4' if dimension == 'configuredCostUsd' else None}
    assert view['pool']['reserved'] == expected
    assert view['member']['reserved']['modelCalls'] == 2
    assert read_member_projection(root.alpha.home, root.alpha.organization_id)['member']['reserved']['modelCalls'] == 2
    assert Decimal(view['pool']['remaining'][dimension]) == 0


def test_orphan_global_commit_retains_capacity_and_same_key_never_reauthorizes_send(installation):
    root = installation
    configure(root, calls=2)
    data = payload()
    with pytest.raises(RuntimeError, match='simulated crash'):
        with root.store._write():
            binding, key, receipt = reserve(root, data)
            raise RuntimeError('simulated crash after durable global debit before local commit')
    assert root.ledger.read()['member']['reserved']['modelCalls'] == 1
    assert verify_receipt(binding, receipt, key, data) == receipt
    for retry in (data, {**data, 'model': 'changed'}):
        with pytest.raises(SharedAllowanceError, match='already_debited_no_replay'):
            debit(binding, physical_call_key(binding, retry), retry)
    with pytest.raises(SharedAllowanceError, match='matching_immutable_receipt_required'):
        verify_receipt(binding, receipt, key, {**data, 'outputLimit': 51})
    reserve(root, payload(2))
    with pytest.raises(SharedAllowanceError, match='exhausted_modelCalls'):
        reserve(root, payload(3))
    assert root.ledger.read()['member']['reserved']['modelCalls'] == 2


@pytest.mark.parametrize('mutation', ['database_missing', 'marker_missing', 'invalid_database', 'missing_table', 'changed_receipt', 'forged_owner', 'misfiled_period'])
def test_missing_or_corrupt_required_evidence_is_never_opted_out_or_rebuilt(installation, mutation):
    root = installation
    configure(root)
    reserve(root, payload())
    if mutation == 'database_missing':
        root.ledger.path.unlink()
    elif mutation == 'marker_missing':
        root.ledger.marker.unlink()
    elif mutation == 'invalid_database':
        root.ledger.path.write_bytes(b'not a sqlite database')
    else:
        with sqlite3.connect(root.ledger.path) as conn:
            if mutation == 'missing_table':
                conn.execute('DROP TABLE allowance_configurations')
            else:
                trigger = conn.execute("SELECT sql FROM sqlite_master WHERE name='allowance_debits_update'").fetchone()[0]
                conn.execute('DROP TRIGGER allowance_debits_update')
                record = json.loads(conn.execute('SELECT receipt FROM allowance_debits').fetchone()[0])
                if mutation == 'misfiled_period':
                    conn.execute("UPDATE allowance_debits SET period_id='wrong-period'")
                else:
                    if mutation == 'forged_owner':
                        record['ownerId'] = root.beta.owner_id
                        body = {key: value for key, value in record.items() if key != 'sha256'}
                        record['sha256'] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
                    else:
                        record['reserved']['tokens'] = 1
                    conn.execute('UPDATE allowance_debits SET receipt=?', (json.dumps(record),))
                conn.execute(trigger)
    before = {path: path.read_bytes() for path in (root.ledger.path, root.ledger.marker) if path.is_file()}
    with pytest.raises(SharedAllowanceError):
        root.ledger.read()
    with pytest.raises(SharedAllowanceError):
        resolve_binding(root.home, root.organization_id)
        reserve(root, payload(2))
    assert all(path.read_bytes() == value for path, value in before.items())


def test_receipt_records_are_immutable_and_malformed_owner_replay_is_rejected(installation):
    root = installation
    config = configure(root)
    binding, key, receipt = reserve(root, payload())
    for table in ('allowance_identity', 'allowance_configurations', 'allowance_debits'):
        with sqlite3.connect(root.ledger.path) as conn, pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute(f'DELETE FROM {table}')
    with sqlite3.connect(root.ledger.path) as conn:
        trigger = conn.execute("SELECT sql FROM sqlite_master WHERE name='allowance_configurations_update'").fetchone()[0]
        conn.execute('DROP TRIGGER allowance_configurations_update')
        conn.execute("UPDATE allowance_configurations SET result='{}'")
        conn.execute(trigger)
    with pytest.raises(SharedAllowanceError, match='corrupt_owner_receipt'):
        configure(root, config, key='initialize')
    malformed = {**receipt, 'ownerId': root.beta.owner_id}
    with pytest.raises(SharedAllowanceError, match='corrupt_debit_receipt'):
        verify_receipt(binding, malformed, key, payload())


def test_completion_protection_applies_to_both_envelopes_and_owner_chat_is_ordinary(installation):
    root = installation
    configure(root, calls=3, protected_calls=2, tokens=1000, protected_tokens=400)
    reserve(root, payload(kind='owner_chat'))
    with pytest.raises(SharedAllowanceError, match='protected_or_exhausted_modelCalls'):
        reserve(root, payload(2, kind='owner_chat'))
    reserve(root, payload(1))
    reserve(root, payload(2))
    view = root.ledger.read()
    assert view['member']['reserved']['modelCalls'] == 3
    assert view['member']['protectedRemaining']['modelCalls'] == 0
    assert view['pool']['protectedRemaining']['modelCalls'] == 2
    assert view['member']['protectedRemaining']['tokens'] == 200


def test_shared_stage_spacing_uses_low_stale_observation_without_refunds_or_inner_clock_reset(installation, monkeypatch):
    root = installation
    configure(root)
    now = time.time()
    observation = {'remainingPercent': 5, 'asOf': iso(now - 1), 'expiresAt': iso(now + 30), 'resetAt': iso(now + 10)}
    result = root.ledger.observe(observation, expected_revision=1, idempotency_key='observation',
                                root_organization_id=root.organization_id, root_owner_id=root.owner_id)
    assert result['revision'] == 2
    first = payload(request_type='work.draft')
    reserve(root, first)
    started = root.ledger.read()['pacing']['deferUntil']
    monkeypatch.setattr('eidolon_cli.organization_shared_allowance.time.time', lambda: now + 40)
    reserve(root, payload(2, request_type='work.draft'))
    view = root.ledger.read()
    assert view['pacing']['deferUntil'] == started
    assert view['observation']['status'] == 'reset_unconfirmed'
    assert view['member']['reserved']['modelCalls'] == 2
    with pytest.raises(OrganizationPacingDeferred) as deferred:
        reserve(root.alpha, payload(request_type='work.draft'))
    assert deferred.value.defer_until > now + 40
    reserve(root.alpha, payload(request_type='request.review'))
    monkeypatch.setattr('eidolon_cli.organization_shared_allowance.time.time', lambda: now + 301)
    reserve(root.alpha, payload(request_type='work.draft', requestId='new-request'))
    assert root.ledger.read()['pool']['reserved']['modelCalls'] == 4


def test_owner_periods_are_explicit_nonoverlapping_sticky_and_preserve_original_debits(installation, monkeypatch):
    root = installation
    initial = configure(root)
    binding, key, receipt = reserve(root, payload())
    changed = deepcopy(initial)
    changed['endsAt'] = iso(time.time() + 7200)
    with pytest.raises(SharedAllowanceError, match='period_bounds_immutable'):
        configure(root, changed, revision=1, key='extend')
    end = datetime.fromisoformat(initial['endsAt']).timestamp()
    next_period = {**deepcopy(initial), 'periodId': 'period-two', 'startsAt': iso(end), 'endsAt': iso(end + 3600)}
    with pytest.raises(SharedAllowanceError, match='previous_period_not_closed'):
        configure(root, next_period, revision=1, key='premature')
    monkeypatch.setattr('eidolon_cli.organization_shared_allowance.time.time', lambda: end + 1)
    with pytest.raises(SharedAllowanceError, match='period_inactive'):
        reserve(root, payload(2))
    removed = {**next_period, 'allocations': [next_period['allocations'][0]]}
    with pytest.raises(SharedAllowanceError, match='enrollment_cannot_be_removed'):
        configure(root, removed, revision=1, key='remove')
    configure(root, next_period, revision=1, key='next')
    assert root.ledger.read()['pool']['reserved']['modelCalls'] == 0
    with pytest.raises(SharedAllowanceError, match='stale_or_invalid_binding'):
        verify_receipt(binding, receipt, key, payload())
    with pytest.raises(SharedAllowanceError, match='already_debited_no_replay'):
        reserve(root, payload())
    reserve(root, payload(2))
    with sqlite3.connect(root.ledger.path) as conn:
        assert conn.execute('SELECT count(*) FROM allowance_debits').fetchone()[0] == 2


def test_named_alias_copy_and_recreated_profile_never_inherit_or_evade_enrollment(installation):
    root = installation
    configure(root)
    copied = root.home / 'profiles' / 'copy'
    shutil.copytree(root.alpha.home, copied)
    with pytest.raises(SharedAllowanceError, match='enrolled_identity_changed'):
        resolve_binding(copied, root.alpha.organization_id)
    alias = root.home / 'profiles' / 'alias'
    alias.symlink_to(root.alpha.home, target_is_directory=True)
    with pytest.raises(SharedAllowanceError, match='noncanonical_path'):
        resolve_binding(alias, root.alpha.organization_id)
    with sqlite3.connect(root.beta.store.path) as source, sqlite3.connect(root.alpha.store.path) as destination:
        source.backup(destination)
    with pytest.raises(SharedAllowanceError, match='enrolled_identity_changed'):
        resolve_binding(root.alpha.home, root.beta.organization_id)


@pytest.mark.parametrize('fence', ['capacity-locks', 'owner-chat-locks'])
def test_enrollment_waits_for_cancelled_but_unwinding_physical_executions(installation, fence):
    from eidolon_cli.organization_service import _ExecutionLock
    root = installation
    lock_key = '0' if fence == 'capacity-locks' else 'profile-slot-0'
    with _ExecutionLock(root.alpha.home / 'organization' / fence, lock_key) as acquired:
        assert acquired
        with pytest.raises(SharedAllowanceError, match='scope_execution_still_stopping'):
            configure(root)
    assert not root.ledger.path.exists() and not root.ledger.marker.exists()
    configure(root)


def test_enrollment_serializes_with_profile_admission_without_global_to_local_deadlock(installation):
    root = installation
    held, release = threading.Event(), threading.Event()
    def profile_writer():
        with root.alpha.store._write():
            held.set()
            assert release.wait(10)
            assert resolve_binding(root.alpha.home, root.alpha.organization_id) is None
    with ThreadPoolExecutor(max_workers=2) as executor:
        writer = executor.submit(profile_writer)
        assert held.wait(10)
        configured = executor.submit(configure, root)
        release.set()
        writer.result(15)
        configured.result(15)
    assert resolve_binding(root.alpha.home, root.alpha.organization_id) is not None


def test_interrupted_initial_activation_stays_fail_closed_and_never_recreates_empty_budget(installation, monkeypatch):
    root = installation
    def crash(*_):
        raise RuntimeError('interrupted initial transaction')
    monkeypatch.setattr(root.ledger, '_validate_configuration_change', crash)
    with pytest.raises(RuntimeError, match='interrupted initial transaction'):
        configure(root)
    assert root.ledger.marker.exists()
    with pytest.raises(SharedAllowanceError):
        resolve_binding(root.alpha.home, root.alpha.organization_id)
    with pytest.raises(SharedAllowanceError):
        root.ledger.read()

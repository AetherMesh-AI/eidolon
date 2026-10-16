"""Real prepared pair through owner registration and durable organization gates.

This uses the separately pinned public renderer fixture, not current-main source.
No provider calls are made. Workload execution itself is never substituted.
"""
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import asdict, replace
import json
import stat
import threading
import time
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_prepared_validation_setup as setup
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_root_grants import folder_profile  # noqa: F401
from tests.eidolon_cli.test_organization_project_workspace_native import prepared as native_workspace  # noqa: F401
from tests.eidolon_cli.test_organization_prepared_validation_setup import owner, prepared  # noqa: F401


def owner_source_policy(source):
    """Narrow the fixture policy without changing or omitting any selected bytes."""
    from eidolon_cli.organization_project_snapshot_bundle import _validate_selection
    # Standalone capture permits 1024-byte paths; the prepared owner contract
    # deliberately permits at most 512. Fail if any selected path cannot fit.
    limits = replace(source.limits, max_path_bytes=min(source.limits.max_path_bytes, 512))
    paths = list(_validate_selection([item.path for item in source.files], limits))
    return {'files': paths, 'limits': asdict(limits),
            'executionOnlyHashes': {item.path: item.sha256 for item in source.files if item.execution_only}}


@pytest.fixture
def native_owner(folder_profile, native_workspace):
    from eidolon_cli.organization_folder_setup import register_folder
    source, dependencies, manifest, profile = native_workspace
    from tests.eidolon_cli.prepared_workspace_fixture import _load_renderer_selection
    expected, execution_only = _load_renderer_selection()
    assert {item.path: item.sha256 for item in source.files} == expected
    assert len(source.files) == 2264 and source.byte_count == 21506068
    assert len(execution_only) == 53
    assert profile['bundleSha256'] == '979c240d2b685960974ec6536798cf904762b44823c3576a3ebdebf4f492cbea'
    owner = folder_profile
    register_folder(owner.home, owner.apply)
    for item in source.files:
        target = owner.root / item.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.content)
    selection = {'projectId': 'notes', 'enabled': True,
        'source': owner_source_policy(source),
        'profile': deepcopy(profile), 'dependencies': {'directory': str(dependencies), 'manifest': manifest}}
    return SimpleNamespace(**vars(owner), dependency=dependencies, selection=selection)


def test_native_owner_policy_preserves_complete_pinned_inventory_without_native_opt_in():
    from tests.eidolon_cli.prepared_workspace_fixture import _LIMITS, _load_renderer_selection
    expected, execution_only = _load_renderer_selection()
    source = SimpleNamespace(limits=_LIMITS, files=tuple(SimpleNamespace(
        path=path, sha256=sha256, execution_only=path in execution_only) for path, sha256 in expected.items()))
    policy = owner_source_policy(source)
    assert len(policy['files']) == len(source.files) == 2264
    assert policy['files'] == list(expected)
    assert {item.path: item.sha256 for item in source.files} == expected
    assert policy['executionOnlyHashes'] == execution_only and len(execution_only) == 53
    assert policy['limits'] == {**asdict(_LIMITS), 'max_path_bytes': 512}
    assert source.limits.max_path_bytes == 1024
    assert max(len(path.encode('utf-8')) for path in expected) <= 512


@pytest.mark.parametrize('path', ['/'.join(['x' * 200, 'x' * 200, 'x' * 111]),
                                 '/'.join(['\u00e9' * 100, '\u00e9' * 100, '\u00e9' * 56])])
def test_native_owner_policy_rejects_overlong_paths_without_dropping_files(path):
    from eidolon_cli.organization_project_snapshot_bundle import ProjectSnapshotError, ProjectSnapshotLimits, _validate_selection
    source = SimpleNamespace(limits=ProjectSnapshotLimits(10, 1024, 4096), files=(
        SimpleNamespace(path='src/valid.ts', sha256='a' * 64, execution_only=False),
        SimpleNamespace(path=path, sha256='b' * 64, execution_only=False)))
    assert path in _validate_selection([item.path for item in source.files], source.limits)
    with pytest.raises(ProjectSnapshotError):
        owner_source_policy(source)
    assert len(source.files) == 2 and source.limits.max_path_bytes == 1024


def test_native_owner_policy_preserves_an_already_tighter_path_bound():
    from eidolon_cli.organization_project_snapshot_bundle import ProjectSnapshotLimits
    source = SimpleNamespace(limits=ProjectSnapshotLimits(10, 1024, 4096, max_path_bytes=128),
        files=(SimpleNamespace(path='src/valid.ts', sha256='a' * 64, execution_only=False),))
    assert owner_source_policy(source)['limits'] == asdict(source.limits)


@pytest.mark.linux_only
def test_native_owner_policy_passes_real_registration_and_planning_without_runtime(owner):
    from eidolon_cli.organization_project_snapshot_bundle import ProjectSnapshotLimits, capture_project_snapshot
    from eidolon_cli.organization_project_validation_profile import normalize_validation_profile
    from tests.eidolon_cli.test_organization_prepared_validation_stage_runtime import setup_running
    from tests.eidolon_cli.test_organization_prepared_validation_lifecycle import CRITERIA
    original = deepcopy(owner.selection)
    source = capture_project_snapshot(owner.root, original['source']['files'], root_alias='root0',
        expected_root_identity=(owner.root.stat().st_dev, owner.root.stat().st_ino),
        check_authorized=lambda: True, deadline_monotonic=time.monotonic() + 30,
        limits=ProjectSnapshotLimits(**{**original['source']['limits'], 'max_path_bytes': 1024}),
        reviewed_execution_only_hashes=original['source']['executionOnlyHashes'])
    before = {item.path: item.content for item in source.files}
    invalid = deepcopy(owner.preview_params)
    invalid['selection']['source']['limits']['max_path_bytes'] = 1024
    with pytest.raises(ValueError, match='source paths are limited to 512'):
        setup.preview_prepared_validation(owner.home, invalid)
    owner.selection['source'] = owner_source_policy(source)
    state = setup_running(owner, clear_execution_only=False, acceptance_criteria=CRITERIA, full_planning=True)
    assert state.claim['type'] == 'request.project_test'
    contract = state.saved['receipt']['candidate']['contract']
    assert contract['source'] == owner.selection['source']
    assert contract['profile'] == normalize_validation_profile(original['profile'])
    assert contract['dependencies']['bundleSha256'] == original['profile']['bundleSha256']
    assert {path: (owner.root / path).read_bytes() for path in before} == before
    with state.store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM prepared_validation_sets').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM prepared_validation_runtime_events').fetchone()[0] == 0


@contextmanager
def keep_native_claims_alive(store, objective_id, record_property):
    """Exercise ordinary lease renewal; never change lease settings or deadlines."""
    stop = threading.Event()
    failures, durations, renewed = [], [], []

    def heartbeat():
        while not stop.wait(1):
            started = time.monotonic()
            try:
                with store._connect() as conn:
                    claims = [dict(row) for row in conn.execute(
                        "SELECT * FROM requests WHERE objective_id=? AND status='running' AND token IS NOT NULL",
                        (objective_id,))]
                for claim in claims:
                    if store.heartbeat(claim):
                        renewed.append(True)
            except Exception as error:
                failures.append({'type': type(error).__name__, 'sqliteCode': getattr(error, 'sqlite_errorcode', None),
                                 'sqliteName': getattr(error, 'sqlite_errorname', None)})
            finally:
                durations.append(time.monotonic() - started)

    worker = threading.Thread(target=heartbeat, name='prepared-native-heartbeat', daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(15)
        record_property('heartbeat_max_seconds', max(durations, default=0))
        record_property('heartbeat_failures', failures)
        record_property('heartbeat_successful_renewals', len(renewed))
        assert not worker.is_alive(), 'Native heartbeat did not stop within its bounded wait'
        assert not failures, 'Native heartbeat could not retain ordinary request leases'
        assert renewed, 'Native concurrent heartbeat made no successful write progress'


@contextmanager
def measure_native_phase(name, record_property):
    started = time.monotonic()
    try:
        yield
    finally:
        record_property('phase_' + name + '_seconds', time.monotonic() - started)


@contextmanager
def measure_native_writes(store, monkeypatch, record_property):
    """Retain lock timings even when native finish rolls back or its lease expires."""
    writes = []
    original_write = store._write

    @contextmanager
    def measured_write():
        started = None
        acquiring = time.monotonic()
        wait = None
        try:
            with original_write() as conn:
                started = time.monotonic()
                wait = started - acquiring
                yield conn
        finally:
            writes.append({'thread': threading.current_thread().name,
                           'waitSeconds': wait if wait is not None else time.monotonic() - acquiring,
                           'lockSeconds': time.monotonic() - started if started is not None else None})

    with monkeypatch.context() as patch:
        patch.setattr(store, '_write', measured_write)
        try:
            yield
        finally:
            record_property('write_transaction_max_seconds', max(
                (item['lockSeconds'] for item in writes if item['lockSeconds'] is not None), default=0))
            record_property('write_transaction_timings', writes)


def test_native_write_timing_survives_rollback_and_failed_acquisition(monkeypatch):
    recorded = {}
    @contextmanager
    def write():
        yield object()
    store = SimpleNamespace(_write=write)
    with pytest.raises(ValueError, match='synthetic rollback'):
        with measure_native_writes(store, monkeypatch, recorded.__setitem__):
            with store._write():
                raise ValueError('synthetic rollback')
    assert len(recorded['write_transaction_timings']) == 1
    assert recorded['write_transaction_max_seconds'] >= 0
    assert recorded['write_transaction_timings'][0]['lockSeconds'] is not None
    assert store._write is write
    @contextmanager
    def blocked():
        raise RuntimeError('synthetic failed acquisition')
        yield
    store._write = blocked
    with pytest.raises(RuntimeError, match='synthetic failed acquisition'):
        with measure_native_writes(store, monkeypatch, recorded.__setitem__):
            with store._write():
                pass
    assert recorded['write_transaction_timings'][0]['lockSeconds'] is None
    assert recorded['write_transaction_timings'][0]['waitSeconds'] >= 0
    assert store._write is blocked


def test_actual_owner_selected_pair_finishes_reviews_and_reopens(native_owner, monkeypatch, record_property):
    from tests.eidolon_cli.test_organization_prepared_validation_stage_runtime import setup_running
    from tests.eidolon_cli.test_organization_project_workspace_native import UI_TEST
    from eidolon_cli.organization_prepared_validation_current import verify_current_prepared_pair
    from tests.eidolon_cli.test_organization_prepared_validation_lifecycle import CRITERIA, record_complete_context, finish_acceptance
    state = setup_running(native_owner, read_path=UI_TEST, clear_execution_only=False,
                          acceptance_criteria=CRITERIA, full_planning=True)
    store = state.store
    with measure_native_writes(store, monkeypatch, record_property), keep_native_claims_alive(store, state.objective, record_property):
        with measure_native_phase('execute_pair', record_property):
            assert store.run_project_stage(state.claim, state.cancel) == {}
        with measure_native_phase('finish_execution', record_property):
            assert store.finish(state.claim, {}) is True
        review = store.claim_next()
        assert review['type'] == 'request.test_review'
        context = record_complete_context(store, review)
        payload = json.loads(review['payload'])
        assert set(payload['evidenceIds']) == {item['id'] for item in context['evidence']}
        assert len(payload['evidenceIds']) == 4
        receipts = [item for item in context['evidence'] if item.get('kind') == 'project_execution']
        assert {item['projectExecution']['checkKind'] for item in receipts} == {'project_typecheck', 'project_tests'}
        assert len({item['projectExecution']['snapshotSha256'] for item in receipts}) == 1
        assert all(item['projectExecution']['cleanupConfirmed'] for item in receipts)
        # A model may only judge the exact already-retained pair and scoped evidence.
        with pytest.raises(ValueError):
            store.finish(review, {'approved': True, 'summary': 'Missing mandatory evidence', 'evidenceIds': payload['evidenceIds'][:-1]})
        assert store.finish(review, {'approved': True,
            'summary': 'Synthetic independent review of the actual bounded renderer typecheck and selected UI test scope.',
            'evidenceIds': payload['evidenceIds']}) is True
        # A retained successful pair cannot authorize changed source or dependencies.
        source_path = native_owner.root / UI_TEST
        original = source_path.read_bytes()
        try:
            source_path.write_bytes(original + b'\n// changed after validation\n')
            with store._connect() as conn, pytest.raises(ValueError, match='source|snapshot|lineage'):
                verify_current_prepared_pair(store, conn, state.objective, 'notes')
        finally:
            source_path.write_bytes(original)
        package = native_owner.dependency / 'node_modules/typescript/package.json'
        original_package = package.read_bytes()
        original_mode = stat.S_IMODE(package.stat().st_mode)
        try:
            package.chmod(original_mode | stat.S_IWUSR)
            package.write_bytes(original_package + b' ')
            package.chmod(original_mode)
            with store._connect() as conn, pytest.raises(ValueError):
                verify_current_prepared_pair(store, conn, state.objective, 'notes')
        finally:
            package.chmod(original_mode | stat.S_IWUSR)
            package.write_bytes(original_package)
            package.chmod(original_mode)
        finish_acceptance(store)
        reopened = OrganizationStore(store.path, store.settings)
        with reopened._connect() as conn:
            verified = verify_current_prepared_pair(reopened, conn, state.objective, 'notes')
            assert verified['runtimeReceiptsVerified'] is True
            assert verified['independentReviewVerified'] is True
            assert verified['sourceDeliveryAuthorized'] is False
            assert verified['replayAllowed'] is False
            assert conn.execute('SELECT count(*) FROM prepared_validation_steps').fetchone()[0] == 2
            assert conn.execute('SELECT count(*) FROM prepared_validation_pair_reviews').fetchone()[0] == 1
            assert conn.execute('SELECT status FROM objective_control WHERE objective_id=?', (state.objective,)).fetchone()[0] == 'accepted'
            assert all(conn.execute('SELECT 1 FROM prepared_validation_runtime_events WHERE run_id=? AND kind=?',
                                   (item['id'], 'receipt')).fetchone() for item, _ in verified['checks'])
        try:
            source_path.write_bytes(original + b'\n// changed after reopen\n')
            with reopened._connect() as conn, pytest.raises(ValueError, match='source|snapshot|lineage'):
                verify_current_prepared_pair(reopened, conn, state.objective, 'notes')
        finally:
            source_path.write_bytes(original)
        view = setup.prepared_validation_setup(native_owner.home, native_owner.identity['organizationId'])
        assert view['registrationRefs']


def expire_after_final_native_verification(state, monkeypatch, record_property, *, window_seconds=20):
    """Shorten a real budget after preflight, then cross it after final checks."""
    from eidolon_cli import organization_prepared_validation_finish as finish
    from eidolon_cli.organization_prepared_validation_ledger import _charged_units
    original_prepare, original_finalize = finish.prepare_finish_verification, finish.finalize_finish_verification
    boundary = {}

    @contextmanager
    def prepare(*args):
        with original_prepare(*args) as token:
            assert token is not None
            with state.store._write() as conn:
                request = state.store._owned(conn, state.claim)
                assert request is not None
                prior = conn.execute('SELECT deadline FROM objective_budgets WHERE objective_id=?', (state.objective,)).fetchone()[0]
                deadline = min(prior, request['lease'] - .01, time.time() + window_seconds)
                assert deadline > time.time() + .1, 'No original authority window remains for the negative case'
                conn.execute('UPDATE objective_budgets SET deadline=? WHERE objective_id=?', (deadline, state.objective))
                boundary['deadline'] = deadline
            yield token

    def finalize(*args):
        result = original_finalize(*args)
        boundary['finalized'] = True
        wait_limit = time.monotonic() + window_seconds + 1
        while time.time() <= boundary['deadline']:
            assert time.monotonic() < wait_limit, 'Wall-clock expiry did not arrive within the monotonic wait cap'
            time.sleep(min(.02, max(.001, boundary['deadline'] - time.time() + .001)))
        boundary['crossed'] = True
        return result

    with monkeypatch.context() as patch:
        patch.setattr(finish, 'prepare_finish_verification', prepare)
        patch.setattr(finish, 'finalize_finish_verification', finalize)
        with pytest.raises(ValueError, match='Prepared completion exceeded its original lease or deadline before commit'):
            state.store.finish(state.claim, {})
    record_property('final_authority_expiry_boundary', boundary)
    assert boundary['finalized'] and boundary['crossed']
    with state.store._connect() as conn:
        assert conn.execute('SELECT deadline FROM objective_budgets WHERE objective_id=?', (state.objective,)).fetchone()[0] <= time.time()
        assert conn.execute('SELECT status FROM requests WHERE id=?', (state.claim['id'],)).fetchone()[0] == 'running'
        assert conn.execute("SELECT count(*) FROM requests WHERE objective_id=? AND type IN ('request.test_review','request.accept')", (state.objective,)).fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM prepared_validation_pair_reviews').fetchone()[0] == 0
        assert _charged_units(conn, state.objective) == 2
        assert conn.execute('SELECT count(*) FROM prepared_validation_steps').fetchone()[0] == 2


@pytest.mark.linux_only
def test_final_expiry_negative_fixture_reaches_the_real_commit_fence(owner, monkeypatch, record_property):
    from tests.eidolon_cli.test_organization_prepared_validation_stage_runtime import setup_running, host
    state = setup_running(owner)
    host(monkeypatch, state)
    assert state.store.run_project_stage(state.claim, state.cancel) == {}
    expire_after_final_native_verification(state, monkeypatch, record_property, window_seconds=5)


def test_actual_pair_cannot_commit_after_final_authority_expires(native_owner, monkeypatch, record_property):
    from tests.eidolon_cli.test_organization_prepared_validation_stage_runtime import setup_running
    from tests.eidolon_cli.test_organization_project_workspace_native import UI_TEST
    from tests.eidolon_cli.test_organization_prepared_validation_lifecycle import CRITERIA
    state = setup_running(native_owner, read_path=UI_TEST, clear_execution_only=False,
                          acceptance_criteria=CRITERIA, full_planning=True)
    with keep_native_claims_alive(state.store, state.objective, record_property):
        assert state.store.run_project_stage(state.claim, state.cancel) == {}
    # The positive case measures concurrent progress. This separate negative
    # deliberately holds its transaction across a shortened deadline and never
    # restarts heartbeat, extends authority, or restores the expired budget.
    with measure_native_writes(state.store, monkeypatch, record_property):
        expire_after_final_native_verification(state, monkeypatch, record_property)

"""Real prepared pair through owner registration and durable organization gates.

This uses the separately pinned public renderer fixture, not current-main source.
No provider calls are made. Workload execution itself is never substituted.
"""
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import asdict
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
        'source': {'files': [item.path for item in source.files], 'limits': asdict(source.limits),
                   'executionOnlyHashes': {item.path: item.sha256 for item in source.files if item.execution_only}},
        'profile': deepcopy(profile), 'dependencies': {'directory': str(dependencies), 'manifest': manifest}}
    return SimpleNamespace(**vars(owner), dependency=dependencies, selection=selection)


@contextmanager
def keep_native_claims_alive(store, objective_id, record_property):
    """Exercise ordinary lease renewal; never change lease settings or deadlines."""
    stop = threading.Event()
    failures, durations = [], []

    def heartbeat():
        while not stop.wait(1):
            started = time.monotonic()
            try:
                with store._connect() as conn:
                    claims = [dict(row) for row in conn.execute(
                        "SELECT * FROM requests WHERE objective_id=? AND status='running' AND token IS NOT NULL",
                        (objective_id,))]
                for claim in claims:
                    store.heartbeat(claim)
            except Exception as error:
                failures.append(type(error).__name__)
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
        assert not worker.is_alive(), 'Native heartbeat did not stop within its bounded wait'
        assert not failures, 'Native heartbeat could not retain ordinary request leases'


def test_actual_owner_selected_pair_finishes_reviews_and_reopens(native_owner, monkeypatch, record_property):
    from tests.eidolon_cli.test_organization_prepared_validation_stage_runtime import setup_running
    from tests.eidolon_cli.test_organization_project_workspace_native import UI_TEST
    from eidolon_cli.organization_prepared_validation_current import verify_current_prepared_pair
    from tests.eidolon_cli.test_organization_prepared_validation_lifecycle import CRITERIA, record_complete_context, finish_acceptance
    state = setup_running(native_owner, read_path=UI_TEST, clear_execution_only=False,
                          acceptance_criteria=CRITERIA, full_planning=True)
    store = state.store
    writes = []
    original_write = store._write

    @contextmanager
    def measured_write():
        started = None
        try:
            with original_write() as conn:
                started = time.monotonic()
                yield conn
        finally:
            if started is not None:
                # Include commit/rollback and lock release, excluding acquisition wait.
                writes.append(time.monotonic() - started)

    monkeypatch.setattr(store, '_write', measured_write)
    with keep_native_claims_alive(store, state.objective, record_property):
        assert store.run_project_stage(state.claim, state.cancel) == {}
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
    record_property('write_transaction_max_seconds', max(writes, default=0))

"""Finish-local byte observations over real SQLite, authority and file I/O."""
from contextlib import contextmanager
from copy import deepcopy
import json

import pytest

from eidolon_cli import organization_prepared_validation_current as current
from eidolon_cli import organization_prepared_validation_finish as finish
from eidolon_cli import organization_project_dependency_verification as dependency
from tests.eidolon_cli.test_organization_prepared_validation_lifecycle import (
    running, confirmed, folder_profile, owner, prepared,  # noqa: F401
    finish_host, finish_acceptance, review_result, change_current_authority,
)
from tests.eidolon_cli.test_organization_prepared_validation_stage_runtime import host, scalar

pytestmark = pytest.mark.linux_only


def ready(running, monkeypatch):
    host(monkeypatch, running)
    assert running.store.run_project_stage(running.claim, running.cancel) == {}


def after_preflight(monkeypatch, action):
    original = finish.prepare_finish_verification
    @contextmanager
    def intercept(*args):
        with original(*args) as token:
            if token is not None:
                action(token)
            yield token
    monkeypatch.setattr(finish, 'prepare_finish_verification', intercept)


def test_each_finish_hashes_once_outside_write_lock_and_heartbeat_remains_available(running, monkeypatch):
    scans, rechecks, concurrent_writes = [], [], []
    observe, recheck = dependency._observe_dependency_bundle, dependency._recheck_dependency_observation
    @contextmanager
    def observed(*args, **kwargs):
        with running.store._connect() as conn:
            # This real competing write lock must remain available throughout
            # expensive hashing, rather than accepting a mocked heartbeat.
            conn.execute('BEGIN IMMEDIATE')
            conn.rollback()
        scans.append(True)
        with observe(*args, **kwargs) as value:
            yield value
    verify_file = dependency._verify_file
    def verify_with_competing_writer(*args, **kwargs):
        with running.store._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.rollback()
        concurrent_writes.append(True)
        return verify_file(*args, **kwargs)
    monkeypatch.setattr(dependency, '_verify_file', verify_with_competing_writer)
    def checked(*args, **kwargs):
        rechecks.append(True)
        return recheck(*args, **kwargs)
    monkeypatch.setattr(dependency, '_observe_dependency_bundle', observed)
    monkeypatch.setattr(dependency, '_recheck_dependency_observation', checked)
    _, review = finish_host(running, monkeypatch)
    assert len(scans) == len(rechecks) == 1
    assert running.store.finish(review, review_result(running, review))
    assert len(scans) == len(rechecks) == 2
    finish_acceptance(running.store)
    assert len(scans) == len(rechecks) == 4
    assert len(concurrent_writes) > len(scans)
    with running.store._connect() as conn:
        assert current.verify_current_prepared_pair(running.store, conn, running.objective, 'notes')['independentReviewVerified']
    assert len(scans) == 5  # No cache escapes its exact finish operation.


@pytest.mark.parametrize('change', ['source', 'bundle', 'bundle_inode', 'source_inode', 'source_root',
    'author', 'registration', 'payload', 'request_agent', 'agent_identity', 'contract', 'generation', 'cancel', 'lease'])
def test_mutation_between_preflight_and_transaction_aborts_without_completion(running, monkeypatch, change):
    ready(running, monkeypatch)
    def mutate(_token):
        if change in {'source', 'bundle', 'bundle_inode', 'author', 'registration'}:
            change_current_authority(running, change)
        elif change == 'source_inode':
            path = running.owner.root / 'apps/desktop/src/main.test.tsx'
            content = path.read_bytes()
            path.rename(path.with_suffix('.retained'))
            path.write_bytes(content + b' ')
        elif change == 'source_root':
            running.owner.root.rename(running.owner.root.with_name('old-root'))
            running.owner.root.mkdir()
        else:
            with running.store._write() as conn:
                if change == 'payload':
                    conn.execute("UPDATE requests SET payload='{}' WHERE id=?", (running.claim['id'],))
                elif change == 'request_agent':
                    conn.execute("UPDATE requests SET agent_id='reviewer' WHERE id=?", (running.claim['id'],))
                elif change == 'agent_identity':
                    assert conn.execute("UPDATE agent_identity SET identity_id='reassigned-inspector' WHERE agent_id='inspector'").rowcount == 1
                elif change == 'contract':
                    conn.execute("UPDATE objective_control SET criteria='[\"changed criterion\"]' WHERE objective_id=?", (running.objective,))
                elif change == 'generation':
                    conn.execute('UPDATE organization_policy SET generation=generation+1 WHERE id=1')
                elif change == 'cancel':
                    conn.execute('UPDATE objectives SET cancelled=1 WHERE id=?', (running.objective,))
                elif change == 'lease':
                    conn.execute('UPDATE requests SET lease=0 WHERE id=?', (running.claim['id'],))
    after_preflight(monkeypatch, mutate)
    try:
        outcome = running.store.finish(running.claim, {})
    except (ValueError, RuntimeError):
        pass
    else:
        assert outcome is False  # Existing lost-claim behavior is preserved.
    assert scalar(running, "SELECT count(*) FROM requests WHERE type='request.test_review'") == 0
    assert scalar(running, 'SELECT count(*) FROM objective_acceptances') == 0
    assert scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2
    assert finish._ACTIVE.get() is None


@pytest.mark.parametrize('change', ['source', 'bundle'])
def test_mutation_after_handler_is_caught_by_final_observation_and_rolls_back(running, monkeypatch, change):
    ready(running, monkeypatch)
    maybe = running.store._maybe_integrate
    def mutate(conn, objective_id):
        maybe(conn, objective_id)
        change_current_authority(running, change)
    monkeypatch.setattr(running.store, '_maybe_integrate', mutate)
    with pytest.raises(ValueError, match='source|preimage|Dependency|dependency'):
        running.store.finish(running.claim, {})
    assert scalar(running, "SELECT status FROM requests WHERE id=?", (running.claim['id'],)) == 'running'
    assert scalar(running, "SELECT count(*) FROM requests WHERE type='request.test_review'") == 0
    assert scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2


def test_failed_final_walk_consumes_observation_and_retry_rehashes(running, monkeypatch):
    ready(running, monkeypatch)
    original = dependency._recheck_dependency_observation
    observations = []
    def fail_once(observation, **kwargs):
        observations.append(observation)
        result = original(observation, **kwargs)
        if len(observations) == 1:
            raise ValueError('Synthetic post-walk abort')
        return result
    monkeypatch.setattr(dependency, '_recheck_dependency_observation', fail_once)
    with pytest.raises(ValueError, match='post-walk abort'):
        running.store.finish(running.claim, {})
    with pytest.raises(ValueError):
        original(observations[0], check_authorized=lambda: True)
    assert running.store.finish(running.claim, {})
    assert len(observations) == 2 and observations[0] is not observations[1]


def test_pair_arriving_after_empty_selected_preflight_cannot_fall_back_under_lock(running, monkeypatch):
    def dispatch(token):
        assert token.targets == {}
        ready(running, monkeypatch)
    after_preflight(monkeypatch, dispatch)
    monkeypatch.setattr(dependency, '_observe_dependency_bundle',
        lambda *a, **kw: pytest.fail('A newly arrived target cannot trigger an uncached finish scan'))
    with pytest.raises(ValueError, match='no exact target|proof inputs changed'):
        running.store.finish(running.claim, {})
    assert scalar(running, "SELECT status FROM requests WHERE id=?", (running.claim['id'],)) == 'running'
    assert scalar(running, "SELECT count(*) FROM requests WHERE type='request.test_review'") == 0
    assert scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2
    assert finish._ACTIVE.get() is None


def test_context_scope_does_not_promote_unreviewed_pair_or_fall_back(running, monkeypatch):
    ready(running, monkeypatch)
    with finish.prepare_finish_verification(running.store, running.claim, {}) as token:
        with running.store._write() as conn:
            request = running.store._owned(conn, running.claim)
            with finish.finish_verification_scope(token, running.store, conn, request):
                with pytest.raises(ValueError, match='whole-pair review'):
                    current.verify_current_prepared_pair(running.store, conn, running.objective, 'notes')
                target = token.targets['notes']
                envelope = json.loads(target['envelope'])
                envelope['generation'] += 1
                with pytest.raises(ValueError, match='no exact target|proof inputs changed'):
                    finish.observed_finish_authority(running.store, conn, envelope, target['snapshot'], require_review=False)
                with running.store._connect() as other, pytest.raises(ValueError, match='foreign'):
                    current.verify_current_prepared_pair(running.store, other, running.objective, 'notes', require_review=False)
        with running.store._write() as conn, pytest.raises(ValueError, match='nonnested'):
            with finish.finish_verification_scope(token, running.store, conn, request):
                pytest.fail('A finish scope cannot be entered twice')
    with running.store._write() as conn, pytest.raises(ValueError, match='expired'):
        with finish.finish_verification_scope(token, running.store, conn, request):
            pytest.fail('A closed finish observation cannot be reused')


def test_rejected_review_finalizes_original_source_then_replans(running, monkeypatch):
    _, review = finish_host(running, monkeypatch)
    result = review_result(running, review)
    result['approved'] = False
    assert running.store.finish(review, result)
    assert scalar(running, 'SELECT round FROM objective_control WHERE objective_id=?', (running.objective,)) == 1
    assert scalar(running, 'SELECT approved FROM prepared_validation_pair_reviews') == 0
    assert scalar(running, 'SELECT count(*) FROM objective_acceptances') == 0


@pytest.mark.parametrize('column', ['criteria', 'required_checks', 'delivery_mode', 'amended_scope'])
@pytest.mark.parametrize('blob', [False, True])
def test_operation_cas_rejects_oversized_or_binary_fields_before_contract_reader(running, monkeypatch, column, blob):
    from eidolon_cli import organization_outcome_contracts as contracts
    value = b'x' * (1048576 + 1) if blob else 'x' * (1048576 + 1)
    with running.store._write() as conn:
        conn.execute(f'UPDATE objective_control SET {column}=? WHERE objective_id=?', (value, running.objective))
        monkeypatch.setattr(contracts, 'contract_view', lambda *a: pytest.fail('Malformed scalar must reject before raw contract reader'))
        with pytest.raises(ValueError, match='scalar'):
            finish._operation_binding(conn, running.objective)


@pytest.mark.parametrize('table,column', [('agents', 'manager_id'), ('agents', 'accepts'),
                                         ('staff_state', 'active'), ('staff_state', 'policy'),
                                         ('agent_identity', 'identity_id')])
@pytest.mark.parametrize('blob', [False, True])
def test_agent_cas_rejects_oversized_nullable_and_numeric_fields(running, table, column, blob):
    value = b'x' * 70000 if blob else 'x' * 70000
    key = 'id' if table == 'agents' else 'agent_id'
    with running.store._write() as conn:
        conn.execute(f'UPDATE {table} SET {column}=? WHERE {key}=?', (value, 'inspector'))
        with pytest.raises(ValueError, match='scalar|activation'):
            finish._agent_binding(conn, ('inspector',))


def test_agent_cas_preserves_absent_legacy_identity_and_binds_later_assignment(running):
    with running.store._write() as conn:
        conn.execute("DELETE FROM agent_identity WHERE agent_id='inspector'")
        before = finish._agent_binding(conn, ('inspector',))
        assert json.loads(before)[0][-1] is None
        conn.execute("INSERT INTO agent_identity VALUES ('inspector','new-inspector',0,'[]','')")
        assert finish._agent_binding(conn, ('inspector',)) != before


def two_projects(owner):
    from types import SimpleNamespace
    import shutil
    import threading
    from eidolon_cli import organization_prepared_validation_setup as setup
    from eidolon_cli.organization_folder_setup import folder_setup, preview_folder, register_folder
    from eidolon_cli.organization_config import OrganizationSettings
    from eidolon_cli.organization_store import OrganizationStore
    from tests.organization_package_helpers import decompose
    from tools.organization_file_read import organization_file_read_scope
    other = owner.root.with_name('other-root')
    shutil.copytree(owner.root, other)
    view = folder_setup(owner.home, owner.identity['organizationId'])
    params = {'expectedOrganizationId': view['organizationId'], 'expectedGeneration': view['policyGeneration'],
        'sourceRevision': view['sourceRevision'], 'folder': {'path': str(other), 'projectId': 'other', 'team': 'general'}}
    preview = preview_folder(owner.home, params)
    register_folder(owner.home, {**{key: value for key, value in params.items() if key != 'folder'},
        'candidate': preview['candidate'], 'previewVersion': preview['previewVersion'],
        'idempotencyKey': 'other-folder', 'confirmed': True})
    configuration = {'capabilities': ['work.inspect'], 'tool_grants': ['read_file', 'run_tests'],
        'max_project_runs': 12, 'max_tool_result_chars': 20000, 'max_inflight': 4,
        'roster': [{'id': 'inspector', 'name': 'Inspector', 'team': 'general',
                    'capabilities': ['work.inspect'], 'tool_grants': ['read_file', 'run_tests']}]}
    (owner.home / 'config.yaml').write_text(json.dumps({'organization': configuration}))
    store = OrganizationStore(owner.path, OrganizationSettings.from_config({'organization': configuration}))
    for project in ('notes', 'other'):
        view = setup.prepared_validation_setup(owner.home, owner.identity['organizationId'])
        selection = deepcopy(owner.selection)
        selection['projectId'] = project
        selection['source']['executionOnlyHashes'] = {}
        params = {**{key: view[value] for key, value in (
            ('expectedOrganizationId', 'organizationId'), ('expectedOwnerId', 'ownerId'),
            ('expectedGeneration', 'policyGeneration'), ('sourceRevision', 'sourceRevision'),
            ('expectedSidecarRevision', 'sidecarRevision'))}, 'selection': selection}
        preview = setup.preview_prepared_validation(owner.home, params)
        setup.save_prepared_validation(owner.home, {**{key: value for key, value in params.items() if key != 'selection'},
            **{key: preview[key] for key in ('candidate', 'candidateHash', 'previewVersion')},
            'idempotencyKey': 'save-' + project, 'confirmed': True})
    refs = setup.prepared_validation_setup(owner.home, owner.identity['organizationId'])['registrationRefs']
    with store._write() as conn:
        objective = store._create_objective(conn, 'Two independently reviewed prepared projects', idempotency_key='two',
            project_ids=['notes', 'other'], prepared_validations=refs, delivery_mode='managed_artifact')
        conn.execute("UPDATE staff_state SET active=1 WHERE agent_id='inspector'")
    decompose(store)
    plan = store.claim_next(recoverable=True)
    assert plan['type'] == 'request.plan'
    assert store.finish(plan, {'workers': 1, 'tasks': [{'title': 'Inspect ' + project,
        'description': 'Read exact complete selected source', 'type': 'work.inspect', 'team': 'general',
        'agentId': 'inspector', 'projectId': project, 'dependsOn': []} for project in ('notes', 'other')]})
    while True:
        claim = store.claim_next(recoverable=True)
        assert claim is not None
        if claim['type'] == 'request.project_test':
            return SimpleNamespace(store=store, owner=owner, objective=objective, claim=claim, cancel=threading.Event())
        if claim['type'] == 'work.inspect':
            with store._connect() as conn:
                project = conn.execute('SELECT project_id FROM task_projects WHERE task_id=?', (claim['task_id'],)).fetchone()[0]
            path = ('root0' if project == 'notes' else 'root1') + '/apps/desktop/src/main.test.tsx'
            arguments = {'path': path, 'offset': 1, 'limit': 2000}
            receipt = store.record_tool_start(claim, 'read-' + project, 'read_file', arguments)
            with organization_file_read_scope(store.settings.read_roots, store.settings.max_tool_result_chars) as scope:
                content = scope.read_file(**arguments)
            assert json.loads(content)['success']
            store.record_tool_finish(claim, receipt['id'], content, 'completed')
            assert store.finish(claim, {'summary': 'Read exact source', 'deliverable': 'Inspected ' + project})
        else:
            assert claim['type'] == 'request.review'
            assert store.finish(claim, {'approved': True, 'summary': 'Independent exact source review',
                'evidenceIds': json.loads(claim['payload'])['evidenceIds']})


@pytest.mark.parametrize('other_state', ['running', 'failed'])
def test_pair_review_progress_and_last_review_integration_across_two_projects(owner, monkeypatch, other_state):
    from eidolon_cli import organization_project_workspace_node as runtime
    from tests.eidolon_cli.test_organization_prepared_validation_stage_runtime import synthetic_receipt
    from tests.eidolon_cli.test_organization_project_workspace_receipt import events_for
    state = two_projects(owner)
    first_root = None
    review = result = None
    reviewed_while_other_running = []
    def run(source, directory, manifest, toolchain, profile, step_id, **options):
        nonlocal first_root
        if first_root is None:
            first_root = source.root_alias
        receipt = synthetic_receipt(source, profile, step_id, options)
        if source.root_alias != first_root:
            if other_state == 'running' and not reviewed_while_other_running:
                assert scalar(state, 'SELECT count(*) FROM prepared_validation_sets') == 2
                assert scalar(state, 'SELECT count(*) FROM prepared_validation_results') == 2
                assert state.store.finish(review, result)
                reviewed_while_other_running.append(True)
            elif other_state == 'failed':
                receipt['status'] = 'failed'
        for event in events_for(receipt):
            assert options['record_event'](event)
        return receipt
    monkeypatch.setattr(runtime, 'run_prepared_workspace_step', run)
    monkeypatch.setattr(runtime, '_docker', lambda *a, **k: pytest.fail('No Docker in synthetic lifecycle'))
    assert state.store.run_project_stage(state.claim, state.cancel) == {}
    assert state.store.finish(state.claim, {})
    claims = [state.store.claim_next(recoverable=True), state.store.claim_next(recoverable=True)]
    review = next(item for item in claims if item['type'] == 'request.test_review')
    second = next(item for item in claims if item['type'] == 'request.project_test')
    result = review_result(state, review)
    assert state.store.run_project_stage(second, state.cancel) == {}
    assert state.store.finish(second, {})
    if other_state == 'failed':
        assert state.store.finish(review, result)
        assert scalar(state, 'SELECT count(*) FROM prepared_validation_pair_reviews') == 1
        assert scalar(state, "SELECT count(*) FROM requests WHERE type='request.integrate'") == 0
    else:
        assert reviewed_while_other_running == [True]
        last = state.store.claim_next(recoverable=True)
        assert last['type'] == 'request.test_review'
        assert state.store.finish(last, review_result(state, last))
        assert scalar(state, 'SELECT count(*) FROM prepared_validation_pair_reviews') == 2
        finish_acceptance(state.store)
        assert scalar(state, 'SELECT status FROM objective_control WHERE objective_id=?', (state.objective,)) == 'accepted'
    assert scalar(state, 'SELECT sum(units) FROM prepared_validation_steps') == 4


@pytest.mark.parametrize('change', ['generation', 'receipt', 'evidence', 'lineage'])
def test_retained_target_and_lineage_changes_between_preflight_and_transaction_reject(running, monkeypatch, change):
    import hashlib
    ready(running, monkeypatch)
    def mutate(_token):
        with running.store._write() as conn:
            if change == 'evidence':
                content = 'A different rehashed source inspection artifact'
                conn.execute('UPDATE evidence SET content=?,sha256=? WHERE objective_id=?',
                             (content, hashlib.sha256(content.encode()).hexdigest(), running.objective))
            elif change == 'lineage':
                conn.execute("INSERT OR IGNORE INTO workspaces VALUES ('foreign-workspace',?)", (running.objective,))
                workspace = conn.execute('SELECT id FROM workspaces WHERE objective_id=?', (running.objective,)).fetchone()[0]
                path = 'root0/apps/desktop/src/main.test.tsx'
                conn.execute('INSERT INTO workspace_revisions VALUES (?,?,?,?,?,?,?)',
                    (running.objective, path, 1, workspace, 'unreviewed', hashlib.sha256(b'unreviewed').hexdigest(), 0))
                conn.execute('INSERT INTO workspace_heads VALUES (?,?,1)', (running.objective, path))
            else:
                table = 'prepared_validation_sets' if change == 'generation' else 'prepared_validation_results'
                for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)).fetchall():
                    conn.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
                if change == 'generation':
                    from eidolon_cli.organization_project_validation import canonical, digest
                    row = conn.execute('SELECT id,envelope FROM prepared_validation_sets').fetchone()
                    envelope = json.loads(row['envelope'])
                    envelope['generation'] += 1
                    conn.execute('UPDATE prepared_validation_sets SET generation=?,envelope=?,envelope_sha256=? WHERE id=?',
                                 (envelope['generation'], canonical(envelope), digest(envelope), row['id']))
                else:
                    conn.execute("UPDATE prepared_validation_results SET receipt_sha256=?", ('f' * 64,))
    after_preflight(monkeypatch, mutate)
    with pytest.raises(ValueError):
        running.store.finish(running.claim, {})
    assert scalar(running, "SELECT status FROM requests WHERE id=?", (running.claim['id'],)) == 'running'
    assert scalar(running, "SELECT count(*) FROM requests WHERE type='request.test_review'") == 0
    assert scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2

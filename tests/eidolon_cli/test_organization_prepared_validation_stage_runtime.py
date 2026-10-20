"""Synthetic host orchestration over real authority, source and SQLite; never native proof."""
from copy import deepcopy
import hashlib
import json
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_prepared_validation_setup as setup
from eidolon_cli import organization_prepared_validation_stage as stage
from eidolon_cli import organization_prepared_validation_ledger as ledger
from eidolon_cli import organization_project_workspace_node as runtime
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_prepared_validation_events import retained_runtime_events
from eidolon_cli.organization_prepared_validation_review_scope import review_scope_artifact
from eidolon_cli.organization_project_validation import canonical, digest
from eidolon_cli.organization_project_workspace_resources import container_labels, container_name
from eidolon_cli.organization_projects import bind_task_project
from eidolon_cli.organization_store import OrganizationStore
from tools.organization_file_read import organization_file_read_scope
from tests.eidolon_cli.test_organization_prepared_validation_setup import (
    confirmed, folder_profile, owner, prepared,  # noqa: F401
)
from tests.eidolon_cli.test_organization_project_workspace_receipt import events_for, fixture

pytestmark = pytest.mark.linux_only
READ_PATH = 'apps/desktop/src/main.test.tsx'


def leased(store, objective, kind, agent, *, task=None, payload=None):
    with store._write() as conn:
        identifier = store._request(conn, objective, kind, 'general', 3, task, payload)
        store._stamp_claim_policy(conn, identifier)
        conn.execute("UPDATE requests SET status='running',agent_id=?,token=?,lease=?,attempts=1,execution_count=1 WHERE id=?",
                     (agent, 'lease-' + identifier, time.time() + 3600, identifier))
        return dict(conn.execute('SELECT * FROM requests WHERE id=?', (identifier,)).fetchone())


@pytest.fixture
def running(owner):
    return setup_running(owner)


def setup_running(owner, *, read_path=READ_PATH, clear_execution_only=True,
                  acceptance_criteria=None, full_planning=False):
    """Create real reviewed authority; callers supply an isolated owner/source fixture."""
    configuration = {'capabilities': ['work.inspect'], 'tool_grants': ['read_file', 'run_tests'],
        'max_project_runs': 12, 'max_tool_result_chars': 20000,
        'roster': [{'id': 'inspector', 'name': 'Inspector', 'team': 'general',
                    'capabilities': ['work.inspect'], 'tool_grants': ['read_file', 'run_tests']}]}
    (owner.home / 'config.yaml').write_text(canonical({'organization': configuration}))
    store = OrganizationStore(owner.path, OrganizationSettings.from_config({'organization': configuration}))
    view = setup.prepared_validation_setup(owner.home, owner.identity['organizationId'])
    owner.selection = deepcopy(owner.selection)
    if clear_execution_only:
        owner.selection['source']['executionOnlyHashes'] = {}
    owner.preview_params = {**{key: view[value] for key, value in (
        ('expectedOrganizationId', 'organizationId'), ('expectedOwnerId', 'ownerId'),
        ('expectedGeneration', 'policyGeneration'), ('sourceRevision', 'sourceRevision'),
        ('expectedSidecarRevision', 'sidecarRevision'))}, 'selection': owner.selection}
    saved = setup.save_prepared_validation(owner.home, confirmed(owner))
    refs = setup.prepared_validation_setup(owner.home, owner.identity['organizationId'])['registrationRefs']
    with store._write() as conn:
        objective = store._create_objective(conn, 'Synthetic prepared host fixture', idempotency_key='stage',
            project_ids=['notes'], prepared_validations=refs, delivery_mode='managed_artifact',
            acceptance_criteria=acceptance_criteria)
        if not full_planning:
            conn.execute("INSERT INTO tasks(id,objective_id,title,description,type,team,priority,status,dependencies,author_id) "
                         "VALUES ('inspection',?,'Inspect source','Read exact selected source','work.inspect','general',3,'completed','[]','inspector')", (objective,))
            conn.execute('INSERT INTO objective_task_rounds VALUES (?,?,0)', ('inspection', objective))
            bind_task_project(conn, 'inspection', {'projectId': 'notes'}, store.settings)
        conn.execute("UPDATE staff_state SET active=1 WHERE agent_id='inspector'")
    if full_planning:
        from tests.organization_package_helpers import decompose
        decompose(store)
        plan = store.claim_next(recoverable=True)
        assert plan is not None and plan['type'] == 'request.plan'
        assert store.finish(plan, {'workers': 1, 'tasks': [{'title': 'Inspect selected source',
            'description': 'Read the complete selected source before prepared validation.',
            'type': 'work.inspect', 'team': 'general', 'agentId': 'inspector', 'projectId': 'notes', 'dependsOn': []}]})
        inspection = store.claim_next(recoverable=True)
        assert inspection is not None and inspection['type'] == 'work.inspect' and inspection['agent_id'] == 'inspector'
    else:
        inspection = leased(store, objective, 'work.inspect', 'inspector', task='inspection')
    arguments = {'path': 'root0/' + read_path, 'offset': 1, 'limit': 2000}
    receipt = store.record_tool_start(inspection, 'complete-source-read', 'read_file', arguments)
    with organization_file_read_scope(store.settings.read_roots, store.settings.max_tool_result_chars) as scope:
        observed = scope.read_file(**arguments)
    assert json.loads(observed)['success'] is True
    store.record_tool_finish(inspection, receipt['id'], observed, 'completed')
    content = 'Independently inspected the exact selected fixture source.'
    if full_planning:
        assert store.finish(inspection, {'summary': 'Exact source inspection', 'deliverable': content})
        review = store.claim_next(recoverable=True)
        assert review is not None and review['type'] == 'request.review' and review['agent_id'] != inspection['agent_id']
        assert store.finish(review, {'approved': True, 'summary': 'Exact independent source review',
                                    'evidenceIds': json.loads(review['payload'])['evidenceIds']})
        claim = store.claim_next(recoverable=True)
        assert claim is not None and claim['type'] == 'request.project_test'
        return SimpleNamespace(store=store, owner=owner, objective=objective, claim=claim,
                               cancel=threading.Event(), saved=saved, source_receipt=receipt['id'])
    with store._write() as conn:
        conn.execute("UPDATE requests SET status='completed' WHERE id=?", (inspection['id'],))
        conn.execute('INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?)',
            ('source-evidence', objective, 'inspection', inspection['id'], content,
             hashlib.sha256(content.encode()).hexdigest(), 'Exact source inspection', time.time()))
        conn.execute('INSERT INTO evidence_tools VALUES (?,?)', ('source-evidence', receipt['id']))
    review = leased(store, objective, 'request.review', 'reviewer', task='inspection',
                    payload={'evidenceIds': ['source-evidence']})
    with store._write() as conn:
        conn.execute("UPDATE requests SET status='completed' WHERE id=?", (review['id'],))
        conn.execute('INSERT INTO reviews VALUES (?,?,?,?,?,?,?)',
            (review['id'], 'inspection', 'reviewer', 1, 'Exact independent source review', '["source-evidence"]', time.time()))
    claim = leased(store, objective, 'request.project_test', 'control:project',
        payload={'projectId': 'notes', 'round': 0, 'executionContract': 'prepared-validation-v1'})
    return SimpleNamespace(store=store, owner=owner, objective=objective, claim=claim,
                           cancel=threading.Event(), saved=saved, source_receipt=receipt['id'])


def scalar(running, query, parameters=()):
    with running.store._connect() as conn:
        return conn.execute(query, parameters).fetchone()[0]


def synthetic_receipt(source, profile, step_id, options):
    step = next(item for item in profile['steps'] if item['id'] == step_id)
    value, _ = fixture(step['recipe'])
    resource = deepcopy(options['runtime_identity'])
    parent = options['staging_parent'].stat()
    value.update(resourceIdentity=resource, containerLabels=container_labels(resource),
        containerName=container_name(resource), snapshotSha256=source.snapshot_sha256,
        profileSha256=digest(profile), bundleSha256=profile['bundleSha256'], stepId=step_id,
        stepDefinitionSha256=digest(step), recipe=step['recipe'], checkKind=step['checkKind'],
        dependencyRootIdentity=dict(zip(('device', 'inode'), options['expected_dependency_identity'])),
        stagingIdentity={'path': str(options['staging_parent'] / container_name(resource)),
            'parentDevice': parent.st_dev, 'parentInode': parent.st_ino, 'device': parent.st_dev, 'inode': 100},
        containerId=hashlib.sha256(resource['runId'].encode()).hexdigest())
    value['runtime']['image'] = options['runtime_image']
    paths = runtime._selected_sources(source, step) if step['recipe'] == 'workspace_typescript' else step['testFiles']
    paths = ['/source/' + source.root_alias + '/' + path for path in paths]
    value['outcome'].update(configuration=step['config'], selectedFiles=paths, reportedFiles=paths.copy())
    if step['recipe'] == 'workspace_typescript':
        value['outcome']['sourceCount'] = len(paths)
    else:
        value['outcome']['modules'] = [{'path': path, 'passed': 2, 'skipped': 0, 'failed': 0, 'state': 'passed'} for path in paths]
        value['outcome']['testCount'] = 2 * len(paths)
    return value


def host(monkeypatch, running, intervene=None):
    calls = []
    def run(source, directory, manifest, toolchain, profile, step_id, **options):
        assert scalar(running, 'SELECT count(*) FROM prepared_validation_steps') == 2
        assert options['allow_local_docker'] is True
        assert options['check_authorized']() is True
        receipt = synthetic_receipt(source, profile, step_id, options)
        calls.append(receipt)
        if intervene is not None:
            replacement = intervene(receipt, options, len(calls))
            if replacement is not None:
                return replacement
        for event in events_for(receipt):
            assert options['record_event'](event) is True
        return receipt
    monkeypatch.setattr(runtime, 'run_prepared_workspace_step', run)
    monkeypatch.setattr(runtime, '_docker', lambda *a, **k: pytest.fail('Synthetic stage must never access Docker'))
    return calls


def test_real_authority_capture_reserves_pair_then_runs_exact_mandatory_order(running, monkeypatch):
    calls = host(monkeypatch, running)
    assert stage.run_prepared_project_stage(running.store, running.claim, running.cancel) == {}
    assert [value['recipe'] for value in calls] == ['workspace_typescript', 'workspace_vitest_ui']
    assert calls[0]['snapshotSha256'] == calls[1]['snapshotSha256']
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_aggregates') == 1
    with running.store._connect() as conn:
        assert [len(retained_runtime_events(conn, value['resourceIdentity']['runId'])) for value in calls] == [7, 7]
        scope = review_scope_artifact(conn, conn.execute('SELECT id FROM prepared_validation_review_scopes').fetchone()[0])
        assert json.loads(scope['content'])['reviewedSourceEvidence'].keys() == {'source-evidence'}
    with pytest.raises(ValueError, match='already reserved'):
        stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
    assert len(calls) == 2
    with running.store._connect() as conn:
        with pytest.raises(ValueError, match='transaction'):
            ledger.retain_prepared_aggregate_claim(conn, calls[0]['resourceIdentity']['setId'])


def test_aggregate_helper_does_not_commit_its_callers_failed_transaction(running, monkeypatch):
    retain = ledger.retain_prepared_aggregate_claim
    def abort(conn, set_id):
        assert conn.in_transaction
        result = retain(conn, set_id)
        assert result['runtimeReceiptsVerified'] is False
        assert conn.execute('SELECT count(*) FROM prepared_validation_aggregates').fetchone()[0] == 1
        raise RuntimeError('Synthetic aggregate transaction abort')
    monkeypatch.setattr(ledger, 'retain_prepared_aggregate_claim', abort)
    calls = host(monkeypatch, running)
    with pytest.raises(RuntimeError, match='aggregate transaction abort'):
        stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
    assert len(calls) == 2
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_results') == 2
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_aggregates') == 0


def test_first_failure_stays_charged_and_never_starts_second_step(running, monkeypatch):
    def fail(receipt, _options, _number):
        receipt['status'] = 'failed'
    calls = host(monkeypatch, running, fail)
    assert stage.run_prepared_project_stage(running.store, running.claim, running.cancel) == {}
    assert len(calls) == 1
    assert scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_results') == 1
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_aggregates') == 0


@pytest.mark.parametrize('loss', ['cancel', 'lease', 'staff'])
def test_successful_pair_loses_authority_before_aggregate_retention(running, monkeypatch, loss):
    def expire(receipt, options, number):
        if number != 2:
            return None
        for event in events_for(receipt):
            options['record_event'](event)
        if loss == 'cancel':
            running.cancel.set()
        else:
            with running.store._write() as conn:
                if loss == 'lease':
                    conn.execute('UPDATE requests SET lease=0 WHERE id=?', (running.claim['id'],))
                else:
                    conn.execute("UPDATE staff_state SET active=0 WHERE agent_id='inspector'")
        return receipt
    calls = host(monkeypatch, running, expire)
    with pytest.raises(ValueError):
        stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
    assert len(calls) == 2
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_results') == 2
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_aggregates') == 0


def test_interrupted_dispatch_retains_target_and_never_replays(running, monkeypatch):
    def crash(receipt, options, _number):
        for event in events_for(receipt)[:4]:
            assert options['record_event'](event) is True
        raise RuntimeError('Synthetic host crash after observing exact resource')
    calls = host(monkeypatch, running, crash)
    with pytest.raises(RuntimeError, match='Synthetic host crash'):
        stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_resources') == 1
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_results') == 0
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_aggregates') == 0
    with pytest.raises(ValueError, match='already reserved'):
        stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
    assert len(calls) == 1
    assert scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2


def test_returned_result_cannot_replace_exact_durable_terminal(running, monkeypatch):
    def mismatch(receipt, options, _number):
        for event in events_for(receipt):
            options['record_event'](event)
        return {**receipt, 'stderr': 'a different returned observation'}
    calls = host(monkeypatch, running, mismatch)
    with pytest.raises(ValueError, match='differs from its durable'):
        stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
    assert len(calls) == 1


def test_live_source_is_recaptured_before_second_mandatory_step(running, monkeypatch):
    def change(_receipt, _options, number):
        if number == 1:
            (running.owner.root / 'apps/desktop/tsconfig.json').write_text('{"changed":true}')
    calls = host(monkeypatch, running, change)
    with pytest.raises(ValueError, match='changed before a mandatory step'):
        stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
    assert len(calls) == 1
    assert scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_results') == 1
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_aggregates') == 0


def test_stale_independently_reviewed_read_cannot_authorize_new_source_bytes(running, monkeypatch):
    (running.owner.root / READ_PATH).write_text('changed after independent inspection')
    calls = host(monkeypatch, running)
    with pytest.raises(ValueError, match='current complete text observation'):
        stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
    assert calls == []
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_sets') == 0


def test_scope_retention_failure_rolls_back_both_units_and_all_source_rows(running, monkeypatch):
    with running.store._connect(write=True) as conn:
        conn.execute("CREATE TRIGGER fail_review_scope BEFORE INSERT ON prepared_validation_review_scopes "
                     "BEGIN SELECT RAISE(ABORT,'synthetic scope persistence failure'); END")
    calls = host(monkeypatch, running)
    with pytest.raises(sqlite3.IntegrityError, match='scope persistence failure'):
        stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
    assert calls == []
    for suffix in ('sets', 'steps', 'snapshots', 'files', 'source_proofs', 'review_scopes'):
        assert scalar(running, 'SELECT count(*) FROM prepared_validation_' + suffix) == 0


@pytest.mark.parametrize('loss', ['cancel', 'lease', 'registration'])
def test_lost_authority_blocks_start_but_retains_exact_cleanup_and_terminal(running, monkeypatch, loss):
    def lose(receipt, options, _number):
        records = events_for(receipt)
        for event in records[:4]:
            options['record_event'](event)
        if loss == 'cancel':
            running.cancel.set()
        elif loss == 'lease':
            with running.store._write() as conn:
                conn.execute('UPDATE requests SET lease=0 WHERE id=?', (running.claim['id'],))
        else:
            from tests.eidolon_cli.test_organization_prepared_validation_setup import revoke_params
            setup.revoke_prepared_validation(running.owner.home, revoke_params(running.owner, running.saved))
        with pytest.raises((ValueError, RuntimeError)):
            options['record_event'](records[4])
        receipt['status'] = 'failed'
        for event in events_for(receipt)[5:]:
            assert options['record_event'](event) is True
        return receipt
    calls = host(monkeypatch, running, lose)
    assert stage.run_prepared_project_stage(running.store, running.claim, running.cancel) == {}
    assert len(calls) == 1
    assert scalar(running, "SELECT count(*) FROM prepared_validation_runtime_events WHERE kind='container_start_planned'") == 0
    assert scalar(running, "SELECT count(*) FROM prepared_validation_runtime_events WHERE kind='cleanup_observed'") == 1
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_results') == 1


def test_step_sink_cannot_settle_another_real_reserved_run(running, monkeypatch):
    previous = []
    def swap(receipt, options, number):
        if number == 1:
            previous.append(events_for(receipt)[5])
        else:
            with pytest.raises(ValueError, match='private step invocation'):
                options['record_event'](previous[0])
    calls = host(monkeypatch, running, swap)
    stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
    assert len(calls) == 2
    assert scalar(running, "SELECT count(*) FROM prepared_validation_runtime_events WHERE kind='cleanup_observed'") == 2


def test_running_checkpoint_rejects_deactivated_source_contributor(running, monkeypatch):
    def deactivate(receipt, options, _number):
        for event in events_for(receipt)[:5]:
            options['record_event'](event)
        with running.store._write() as conn:
            conn.execute("UPDATE staff_state SET active=0 WHERE agent_id='inspector'")
        with pytest.raises(ValueError, match='author|grant|staff'):
            options['check_authorized'](force=True)
        receipt['status'] = 'failed'
        for event in events_for(receipt)[5:]:
            options['record_event'](event)
        return receipt
    host(monkeypatch, running, deactivate)
    stage.run_prepared_project_stage(running.store, running.claim, running.cancel)


@pytest.mark.parametrize('mutation', ['set', 'profile', 'source', 'body', 'file_hash', 'disclosure'])
def test_rehashed_review_scope_still_requires_exact_reserved_identity(running, monkeypatch, mutation):
    host(monkeypatch, running)
    stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
    with running.store._write() as conn:
        row = conn.execute('SELECT * FROM prepared_validation_review_scopes').fetchone()
        data = json.loads(row['content'])
        if mutation == 'set': data['setId'] = 'another-set'
        if mutation == 'profile': data['profileSha256'] = 'a' * 64
        if mutation == 'source': data['source']['snapshotSha256'] = 'b' * 64
        if mutation == 'body': data['testAndConfigurationFiles'][0]['content'] += 'changed'
        if mutation == 'file_hash': data['testAndConfigurationFiles'][0]['sha256'] = 'c' * 64
        if mutation == 'disclosure': data['executionOnlyFilesDisclosed'] = True
        conn.execute('DROP TRIGGER prepared_review_scope_no_update')
        conn.execute('UPDATE prepared_validation_review_scopes SET content=?,sha256=? WHERE id=?',
                     (canonical(data), digest(data), row['id']))
        with pytest.raises(ValueError):
            review_scope_artifact(conn, row['id'])


@pytest.mark.parametrize('loss', ['cancel', 'lease', 'deadline', 'staff'])
def test_initial_authority_loss_prevents_reservation_and_host_call(running, monkeypatch, loss):
    if loss == 'cancel':
        running.cancel.set()
    else:
        with running.store._write() as conn:
            if loss == 'lease': conn.execute('UPDATE requests SET lease=0 WHERE id=?', (running.claim['id'],))
            if loss == 'deadline': conn.execute('UPDATE objective_budgets SET deadline=0 WHERE objective_id=?', (running.objective,))
            if loss == 'staff': conn.execute("UPDATE staff_state SET active=0 WHERE agent_id='inspector'")
    calls = host(monkeypatch, running)
    with pytest.raises(ValueError):
        stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
    assert calls == []
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_sets') == 0


def test_deadline_expiry_during_real_source_capture_stops_at_next_checkpoint(running, monkeypatch):
    capture = stage.capture_effective_project_snapshot
    def expire(*args, **kwargs):
        with running.store._write() as conn:
            conn.execute('UPDATE objective_budgets SET deadline=0 WHERE objective_id=?', (running.objective,))
        return capture(*args, **kwargs)
    monkeypatch.setattr(stage, 'capture_effective_project_snapshot', expire)
    calls = host(monkeypatch, running)
    with pytest.raises(ValueError, match='deadline expired'):
        stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
    assert calls == []
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_sets') == 0


def test_scope_disclosure_limit_rolls_back_reservation_instead_of_truncating(running, monkeypatch):
    (running.owner.root / 'apps/desktop/tsconfig.json').write_text(' ' * 22000)
    calls = host(monkeypatch, running)
    with pytest.raises(ValueError, match='context limit'):
        stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
    assert calls == []
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_sets') == 0
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_review_scopes') == 0


def test_review_scope_cannot_substitute_unreviewed_same_objective_evidence(running, monkeypatch):
    host(monkeypatch, running)
    stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
    unrelated = leased(running.store, running.objective, 'work.inspect', 'inspector', task='inspection')
    content = 'A different artifact without an independent review or tool receipt.'
    sha256 = hashlib.sha256(content.encode()).hexdigest()
    with running.store._write() as conn:
        conn.execute('INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?)',
            ('unreviewed', running.objective, 'inspection', unrelated['id'], content,
             sha256, 'Unreviewed alternate artifact', time.time()))
        row = conn.execute('SELECT * FROM prepared_validation_review_scopes').fetchone()
        data = json.loads(row['content'])
        data['reviewedSourceEvidence'] = {'unreviewed': sha256}
        conn.execute('DROP TRIGGER prepared_review_scope_no_update')
        conn.execute('UPDATE prepared_validation_review_scopes SET content=?,sha256=? WHERE id=?',
                     (canonical(data), digest(data), row['id']))
        with pytest.raises(ValueError):
            review_scope_artifact(conn, row['id'])


def test_objective_admission_requires_room_for_complete_pair(running):
    refs = setup.prepared_validation_setup(running.owner.home, running.owner.identity['organizationId'])['registrationRefs']
    with pytest.raises(ValueError, match='reserve both'):
        with running.store._write() as conn:
            running.store._create_objective(conn, 'Insufficient prepared pair budget', idempotency_key='insufficient-pair',
                project_ids=['notes'], prepared_validations=refs, delivery_mode='managed_artifact', project_run_limit=1)
    assert scalar(running, 'SELECT count(*) FROM objectives') == 1
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_steps') == 0


@pytest.mark.parametrize('mutation', ['partial', 'forged_hash', 'same_author'])
def test_source_review_requires_current_complete_independent_observation(running, monkeypatch, mutation):
    with running.store._write() as conn:
        if mutation == 'same_author':
            conn.execute("UPDATE reviews SET agent_id='inspector'")
        else:
            row = conn.execute('SELECT result FROM tool_receipts WHERE id=?', (running.source_receipt,)).fetchone()
            result = json.loads(row[0])
            if mutation == 'partial': result['truncated'] = True
            else: result['sourceSha256'] = 'b' * 64
            encoded = canonical(result)
            conn.execute('UPDATE tool_receipts SET result=?,result_sha256=? WHERE id=?',
                (encoded, hashlib.sha256(encoded.encode()).hexdigest(), running.source_receipt))
    calls = host(monkeypatch, running)
    with pytest.raises(ValueError):
        stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
    assert calls == []
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_sets') == 0

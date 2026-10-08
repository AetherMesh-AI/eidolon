"""Replanning retains exact multi-project diagnostics without reusing stale proof."""
from tests.organization_package_helpers import claim_after_decomposition
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from eidolon_cli import organization_executor as executor
from eidolon_cli import organization_project_runner as runner
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_acceptance import _decision
from tests.eidolon_cli.test_organization_multi_project_execution import configured, reviewed_inspections
from tests.eidolon_cli.test_organization_project_security import _simulated_terminal_result


@pytest.fixture
def planner_provider(tmp_path, monkeypatch):
    """Capture the real agent's local HTTP wire, without any live model calls."""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'data': [{'id': 'replan-fixture'}]}).encode())

        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'id': 'local-replan', 'object': 'chat.completion', 'created': 1,
                'model': 'replan-fixture', 'choices': [{'index': 0,
                    'message': {'role': 'assistant', 'content': json.dumps({
                        'intervention': 'Fixture stops after inspecting complete historical evidence.'})},
                    'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 30, 'completion_tokens': 20, 'total_tokens': 50},
            }).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    home = tmp_path / 'profile'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('OPENAI_API_KEY', 'local-fixture-only')
    (home / 'config.yaml').write_text(json.dumps({
        'model': {'provider': 'custom', 'default': 'replan-fixture',
                  'base_url': f'http://127.0.0.1:{server.server_port}/v1',
                  'api_mode': 'chat_completions', 'streaming': False, 'context_length': 128000},
        'agent': {'environment_probe': False}, 'compression': {'enabled': False},
    }))
    try:
        yield received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def assert_planner_evidence(store, claim, expected, received):
    context = store.context(claim)
    evidence = {item['id']: item for item in context['evidence']}
    payload = json.loads(claim['payload'])
    assert set(expected) <= evidence.keys(), 'Replan dropped retained project diagnostics'
    for identifier, original in expected.items():
        assert evidence[identifier]['content'] == original['content']
        assert evidence[identifier]['sha256'] == payload['evidenceHashes'][identifier] == original['sha256']
    assert context['toolPolicy']['tools'] == []
    assert all(item['execution'] is None for item in context['objective']['projectExecution']['projects'])
    result = executor.execute(claim, context, threading.Event())
    assert 'intervention' in result, result
    assert received
    wire = received[-1]
    assert not wire.get('tools') and not wire.get('functions')
    prompt = next(message['content'] for message in wire['messages']
                  if message['role'] == 'user' and 'Submitted context:\n' in message['content'])
    submitted = json.loads(prompt.split('Submitted context:\n', 1)[1])['context']
    projected = {item['id']: item for item in submitted['evidence']}
    for identifier, original in expected.items():
        reference = projected[identifier]['content']['bodySha256']
        assert submitted['evidenceBodies'][reference] == original['content']
    assert submitted['projectExecutionHistory'] == context['projectExecutionHistory']
    return context, result


@pytest.mark.linux_only
def test_owner_replan_and_amendment_retain_failed_and_unknown_projects_at_provider_wire(
        tmp_path, monkeypatch, planner_provider):
    store, objective = configured(tmp_path)
    claim = reviewed_inspections(store, objective)
    failed_project = json.loads(claim['payload'])['projectId']
    failure = 'Protocol mismatch α → β\nExpected schema v2; received v1.\nExact failing assertion: 2 != 1\n'

    def failed(files, grant, cancel):
        return {**_simulated_terminal_result(files), 'status': 'failed', 'exitCode': 1,
                'reason': 'protocol assertion failed', 'stderr': failure}

    monkeypatch.setattr(runner, 'run_project_tests', failed)
    store.run_project_stage(claim, threading.Event())
    store.finish(claim, {})
    gate = next(row for row in store.snapshot()['requests'] if row['type'] == 'request.project_failed')
    failed_id = gate['evidenceIds'][0]
    expected = {failed_id: store.evidence(failed_id)}
    assert json.loads(expected[failed_id]['content'])['execution']['stderr'] == failure

    other = store.claim_next()
    assert other['type'] == 'request.project_test'
    unknown_project = json.loads(other['payload'])['projectId']
    assert unknown_project != failed_project

    def interrupted(*_):
        raise RuntimeError('Process outcome was not observed')

    monkeypatch.setattr(runner, 'run_project_tests', interrupted)
    with pytest.raises(RuntimeError, match='not observed'):
        store.run_project_stage(other, threading.Event())
    store.fail(other, 'Project execution interrupted with an unknown outcome')
    with store._connect() as conn:
        unknown_id = conn.execute('SELECT id FROM project_run_starts WHERE request_id=?', (other['id'],)).fetchone()[0]

    assert store.resolve(gate['id'], 'request_replan', 'Repair the protocol mismatch.', idempotency_key='repair')
    # A second amendment with no intervening execution must not erase the
    # original failed run or misrepresent the other project's interrupted start.
    for round_number in (1, 2):
        store = OrganizationStore(store.path)
        plan = claim_after_decomposition(store)
        assert plan['type'] == 'request.plan'
        context, result = assert_planner_evidence(store, plan, expected, planner_provider)
        assert context['objective']['round'] == round_number
        history = {entry['projectId']: entry for entry in context['projectExecutionHistory']}
        assert history[failed_project]['runId'] == failed_id
        assert history[failed_project]['status'] == 'failed'
        assert history[failed_project]['evidenceIds'] == [failed_id]
        assert history[unknown_project]['runId'] == unknown_id
        assert history[unknown_project]['status'] == 'unknown'
        assert history[unknown_project]['evidenceIds'] == []
        assert all(entry['round'] == 0 for entry in history.values())
        assert unknown_id not in json.loads(plan['payload'])['evidenceIds']
        with store._connect() as conn:
            assert conn.execute('SELECT 1 FROM project_run_results WHERE run_id=?', (unknown_id,)).fetchone() is None
            for project_id in (failed_project, unknown_project):
                with pytest.raises(ValueError, match='have not been executed'):
                    store.verify_project_tests(conn, objective['id'], project_id)
        store.finish(plan, result)
        if round_number == 1:
            assert store.resolve(plan['id'], 'amend_scope', 'Diagnose the retained mismatch in both repositories.',
                                 idempotency_key='amend')


@pytest.mark.linux_only
@pytest.mark.parametrize('reject_at', ['test_review', 'acceptance', 'partial_source'])
def test_replan_retains_each_project_run_and_source_receipt_without_accepting_stale_proof(
        tmp_path, monkeypatch, planner_provider, reject_at):
    store, objective = configured(tmp_path, source=True)
    claim = reviewed_inspections(store, objective)
    monkeypatch.setattr(runner, 'run_project_tests', lambda files, grant, cancel: {
        **_simulated_terminal_result(files), 'stdout': f"Observed exact project root: {grant['root']}\n"})
    expected, reviewed, source_projects = {}, [], {}
    while True:
        kind = claim['type']
        payload = json.loads(claim['payload'])
        if kind in {'request.project_test', 'request.source_integrate'}:
            if kind == 'request.source_integrate' and reject_at == 'partial_source' and source_projects:
                assert store.finish(claim, {'intervention': 'Second source stage stopped before publication.'})
                assert store.resolve(claim['id'], 'request_replan', 'Replan the incomplete cross-project integration.',
                                     idempotency_key='partial-source-replan')
                break
            store.run_project_stage(claim, threading.Event())
            store.finish(claim, {})
            if kind == 'request.source_integrate':
                identifier = 'project_source_' + claim['id']
                expected[identifier] = store.evidence(identifier)
                source_projects[payload['projectId']] = identifier
        elif kind == 'request.test_review':
            identifier = payload['evidenceIds'][0]
            expected[identifier] = store.evidence(identifier)
            reviewed.append(payload['projectId'])
            reject = reject_at == 'test_review' and len(reviewed) == 2
            store.finish(claim, {'approved': not reject, 'evidenceIds': [identifier],
                                'summary': 'Cross-project protocol coverage is missing.' if reject else 'Exact project snapshot reviewed.'})
            if reject:
                break
        elif kind == 'request.integrate':
            store.finish(claim, {'summary': 'Retained both exact source branches.',
                                 'deliverable': 'Both independently reviewed project snapshots are in their retained source branches.'})
        else:
            assert kind == 'request.accept' and reject_at == 'acceptance'
            store.finish(claim, _decision(store, claim, False))
            break
        claim = store.claim_next()
        assert claim is not None

    assert set(reviewed) == {'alpha', 'beta'}
    store = OrganizationStore(store.path)
    plan = claim_after_decomposition(store)
    assert plan['type'] == 'request.plan'
    context, _ = assert_planner_evidence(store, plan, expected, planner_provider)
    history = {entry['projectId']: entry for entry in context['projectExecutionHistory']}
    assert set(history) == {'alpha', 'beta'}
    assert {identifier for entry in history.values() for identifier in entry['evidenceIds']} == set(expected)
    with store._connect() as conn:
        assert {row[0] for row in conn.execute('SELECT request_id FROM project_source_receipts')} == {
            identifier.removeprefix('project_source_') for identifier in source_projects.values()}
        if reject_at == 'partial_source':
            assert len(source_projects) == 1 and payload['projectId'] not in source_projects
            assert conn.execute('SELECT 1 FROM project_source_receipts WHERE request_id=?', (claim['id'],)).fetchone() is None
        for project_id, entry in history.items():
            run = json.loads(expected[entry['runId']]['content'])
            assert run['projectId'] == project_id
            assert run['execution']['stdout'] == f"Observed exact project root: {run['grant']['execution']['root']}\n"
            assert entry['round'] == 0
            assert entry['status'] == 'passed'
            assert entry['review']['approved'] == (reject_at != 'test_review' or project_id != reviewed[-1])
            with pytest.raises(ValueError, match='latest current'):
                store._verify_project_run(conn, entry['runId'])
            assert not store.verified_source_integration(conn, objective['id'], project_id)
            assert entry['evidenceIds'] == [entry['runId'], *([source_projects[project_id]] if project_id in source_projects else [])]
            if project_id in source_projects:
                source_id = source_projects[project_id]
                receipt = json.loads(expected[source_id]['content'])
                assert receipt['rootAlias'] == run['grant']['execution']['root']

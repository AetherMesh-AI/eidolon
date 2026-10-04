"""RPC → profile runtime → localhost provider → retained final acceptance."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time

import pytest

from eidolon_cli import config, organization_service as services
import tui_gateway.server as gateway


def _rpc(method, **params):
    response = gateway.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
    assert 'error' not in response, response
    return response['result']


def _wait(check):
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError('The real organization flow did not settle')


@pytest.fixture
def local_provider(tmp_path, monkeypatch):
    services.stop_services()
    monkeypatch.setattr(services, '_services', {})
    received, errors = [], []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"owner-loop-test"}]}')

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                assert not body.get('tools')
                prompt = next(item['content'] for item in body['messages'] if item['role'] == 'user' and 'Submitted context:\n' in item.get('content', ''))
                submitted = json.loads(prompt.split('Submitted context:\n', 1)[1])
                request, context = submitted['request'], submitted['context']
                kind, objective = request['type'], context['objective']
                received.append((kind, context))
                conflicts = objective['title'] == 'Reconcile conflicting analyses'
                ids = [item['id'] for item in context.get('evidence', [])]
                if kind == 'request.plan':
                    if not conflicts and not context.get('ownerInputs'):
                        output = {'intervention': 'Which launch date should the final brief use?'}
                    else:
                        output = {'tasks': [
                            {'title': 'Operations analysis', 'description': 'Analyze the supplied launch details.', 'type': 'work.analyze', 'team': 'general'},
                            *([{'title': 'Marketing analysis', 'description': 'Analyze the supplied launch details.', 'type': 'work.analyze', 'team': 'general', 'dependsOn': [0]}] if conflicts else [])]}
                elif kind == 'work.analyze':
                    conclusion = 'Launch Monday.' if context['task']['title'] == 'Operations analysis' else 'Launch Friday.'
                    if not conflicts:
                        assert context['ownerInputs'][0]['text'] == 'Launch on Monday, October 12.'
                        conclusion = 'Launch on Monday, October 12.'
                    output = {'summary': 'Launch analysis completed', 'deliverable': conclusion}
                elif kind == 'request.review':
                    output = {'approved': True, 'summary': 'The individual analysis is supported by supplied task data.', 'evidenceIds': ids}
                elif kind == 'request.integrate':
                    output = {'summary': 'Integrated launch brief', 'deliverable': 'Launch Monday. Launch Friday.' if conflicts else 'Final launch brief: launch Monday, October 12. All teams should use this date.'}
                elif kind == 'request.accept':
                    assert context['evidence'][-1]['kind'] == 'integrated_deliverable'
                    assert len(context['evidence']) == (3 if conflicts else 2)
                    output = {'approved': not conflicts, 'summary': 'The final outcome has contradictory dates.' if conflicts else 'The integrated launch brief meets the complete objective.',
                              'evidenceIds': ids, 'conflicts': ['Monday and Friday contradict one another.'] if conflicts else [],
                              'criteriaResults': [{'criterion': item, 'satisfied': not conflicts, 'evidenceIds': ids,
                                                  'reason': 'Resolve inconsistent dates.' if conflicts else 'The exact final brief uses the clarified date.'}
                                                 for item in objective['acceptanceCriteria']]}
                else:
                    raise AssertionError('Unexpected model stage ' + kind)
            except Exception as exc:
                errors.append(repr(exc))
                output = {'intervention': 'Provider fixture failed its invariant'}
            response = {'id': 'owner-loop', 'object': 'chat.completion', 'created': 1, 'model': 'owner-loop-test',
                        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': json.dumps(output)}, 'finish_reason': 'stop'}],
                        'usage': {'prompt_tokens': 30, 'completion_tokens': 20, 'total_tokens': 50}}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

        def log_message(self, *_):
            pass

    provider = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    home = tmp_path / 'profile'
    home.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    (home / '.env').write_text('OPENAI_API_KEY=localhost-only\n')
    (home / 'config.yaml').write_text(json.dumps({
        'model': {'provider': 'custom', 'default': 'owner-loop-test', 'base_url': f'http://127.0.0.1:{provider.server_port}/v1',
                  'api_mode': 'chat_completions', 'streaming': False, 'context_length': 128000},
        'agent': {'environment_probe': False}, 'compression': {'enabled': False},
        'organization': {'max_inflight': 1, 'max_replans': 1}}))
    config._LOAD_CONFIG_CACHE.clear()
    try:
        yield received, errors
    finally:
        assert services.stop_services()
        provider.shutdown()
        provider.server_close()
        thread.join(3)
        config._LOAD_CONFIG_CACHE.clear()


def test_real_rpc_clarification_resumes_once_and_exec_accepts_actual_final_text(local_provider):
    received, errors = local_provider
    created = _rpc('organization.create', title='Write launch brief', description='Choose the actual launch date from owner input.',
                   acceptanceCriteria=['State one exact owner-confirmed launch date'], idempotencyKey='clarify')
    store = services.get_service().store
    _wait(lambda: store.snapshot()['objectives'][0]['status'] == 'needs_input')
    pending = next(item for item in store.snapshot()['requests'] if item['status'] == 'pending_intervention')
    payload = {'id': pending['id'], 'action': 'provide_input', 'text': 'Launch on Monday, October 12.', 'idempotencyKey': 'launch-date'}
    _rpc('organization.resolve', **payload)
    _rpc('organization.resolve', **payload)
    _wait(lambda: store.snapshot()['objectives'][0]['status'] == 'completed')
    snapshot = _rpc('organization.snapshot')
    objective = snapshot['objectives'][0]
    artifact = _rpc('organization.evidence', id=objective['acceptance']['deliverableId'])
    assert artifact['content'] == objective['result'] and 'October 12' in objective['result']
    assert len(objective['ownerResolutions']) == 1 and not errors
    assert len([kind for kind, _ in received if kind == 'request.plan']) == 2
    assert len([kind for kind, _ in received if kind == 'request.accept']) == 1
    assert objective['usage']['usageComplete'] and objective['usage']['outputTokens'] == len(received) * 20
    assert objective['usage']['stages'] == len(received)
    assert services.stop_services()
    before = len(received)
    reopened = _rpc('organization.snapshot')['objectives'][0]
    assert reopened['id'] == created['objective']['id'] and reopened['status'] == 'completed'
    assert len(received) == before


def test_real_provider_individual_approvals_cannot_complete_conflicting_objective(local_provider):
    received, errors = local_provider
    _rpc('organization.create', title='Reconcile conflicting analyses', acceptanceCriteria=['One consistent launch date'], idempotencyKey='conflicts')
    store = services.get_service().store
    _wait(lambda: store.snapshot()['objectives'][0]['status'] == 'needs_input')
    snapshot = store.snapshot()
    assert not errors
    objective = snapshot['objectives'][0]
    assert objective['acceptance']['round'] == objective['acceptance']['maxReplans'] == 1
    assert objective['acceptance']['status'] == 'blocked' and objective['result'] is None
    assert len([kind for kind, _ in received if kind == 'request.accept']) == 2
    assert all(task['review'] == 'approved' for task in snapshot['tasks'])
    assert sum(task['historical'] for task in snapshot['tasks']) == 2
    final = _rpc('organization.evidence', id=objective['acceptance']['deliverableId'])
    assert 'Monday' in final['content'] and 'Friday' in final['content']
    pending = next(item for item in snapshot['requests'] if item['status'] == 'pending_intervention')
    assert pending['allowedResolutions'] == []


@pytest.mark.parametrize('native_os', [pytest.param('linux', marks=pytest.mark.linux_only),
                                      pytest.param('macos', marks=pytest.mark.macos_only)])
def test_managed_project_proof_is_rpc_retrievable_and_bound_into_final_acceptance(tmp_path, monkeypatch, native_os):
    from eidolon_cli.organization_executor import _prompt, _parse_output, OrganizationExecutionError
    from eidolon_cli.organization_service import OrganizationService
    from eidolon_cli.organization_store import OrganizationStore
    from tests.eidolon_cli.test_organization_acceptance import _decision
    from tests.eidolon_cli.test_organization_project_workflow import _setup, project_proposal, apply_validate

    services.stop_services()
    store, source, objective = _setup(tmp_path)
    with store._write() as conn:
        conn.execute("UPDATE objective_control SET delivery_mode='managed_artifact' WHERE objective_id=?", (objective['id'],))
    evidence_id = project_proposal(store, source)
    apply_validate(store)
    service = OrganizationService(store, home=tmp_path, settings=store.settings)
    monkeypatch.setattr(services, '_services', {services.state_path(): service})
    try:
        snapshot = store.snapshot()['objectives'][0]
        proof = snapshot['projectValidation']
        assert proof['round'] == snapshot['acceptance']['round'] == 0
        assert proof['status'] == 'passed' and proof['notExecuted'] == ['project_commands', 'functional_tests']
        artifact = _rpc('organization.evidence', id=proof['id'])
        assert artifact['kind'] == 'project_validation'
        assert artifact['sha256'] == proof['resultSha256']
        assert artifact['projectValidation']['filesCount'] == 2
        assert {item['evidenceId'] for item in artifact['projectValidation']['manifest']} == {evidence_id}
        integrate = store.claim_next()
        assert integrate['type'] == 'request.integrate'
        context = store.context(integrate)
        assert context['objective']['projectValidation'] == artifact['projectValidation']
        assert {item['id'] for item in context['evidence']} == {evidence_id, proof['id']}
        assert json.loads(integrate['payload'])['evidenceHashes'][proof['id']] == artifact['sha256']
        store.finish(integrate, {'summary': 'Managed project deliverable',
                                 'deliverable': 'Both managed files have the reviewed changes. Content checks passed; project commands and functional tests were not executed.'})
        acceptance = store.claim_next()
        assert acceptance['type'] == 'request.accept'
        context = store.context(acceptance)
        ids = {item['id'] for item in context['evidence']}
        assert proof['id'] in ids and len(ids) == 3
        prompt = _prompt(acceptance, context, 'request.accept')
        submitted = json.loads(prompt.split('Submitted context:\n', 1)[1])
        assert submitted['context']['objective']['projectValidation']['id'] == proof['id']
        value = _decision(store, acceptance)
        with pytest.raises(OrganizationExecutionError, match='exactly'):
            _parse_output(json.dumps({**value, 'evidenceIds': [ident for ident in value['evidenceIds'] if ident != proof['id']]}), 'request.accept', context)
        assert store.finish(acceptance, _parse_output(json.dumps(value), 'request.accept', context))
        reopened = OrganizationStore(store.path)
        final = reopened.snapshot()['objectives'][0]
        assert final['status'] == 'completed' and final['projectValidation']['id'] == proof['id']
        assert _rpc('organization.evidence', id=proof['id']) == artifact
        assert b'old value' in source.read_bytes()
        assert not any(row['type'] == 'request.merge' for row in reopened.snapshot()['requests'])
    finally:
        assert service.stop()

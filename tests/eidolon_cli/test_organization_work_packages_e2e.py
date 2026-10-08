"""Executive-to-Managers-to-workers completion through real SQLite and loopback HTTP."""
from collections import Counter
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_service import OrganizationService
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_work_packages import CRITERIA, package_output, roster


def test_real_provider_overlaps_manager_plans_and_accepts_reviewed_package_dependencies(tmp_path, monkeypatch):
    from eidolon_cli import config

    home = tmp_path / 'profile'
    home.mkdir()
    calls, errors, seen_clarifications, dependency_sources = [], [], [], []
    planning = {name: threading.Event() for name in 'ab'}
    overlapping = threading.Event()
    answer = 'Prepare the combined finding for external readers, with exact source attribution.'
    sources = {f'writer-{name}': f'Exact independently authored finding {name}: café 中文.' for name in 'ab'}
    final = 'Both independent findings support one combined recommendation for external readers.'

    def body_text(context, value):
        return context['evidenceBodies'][value['bodySha256']] if isinstance(value, dict) else value

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"package-loopback-fixture"}]}')

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            try:
                assert not body.get('tools')
                assert body['max_tokens'] == 8000
                prompt = next(row['content'] for row in body['messages'] if row['role'] == 'user')
                submitted = json.loads(prompt.split('Submitted context:\n', 1)[1])
                kind, context = submitted['request']['type'], submitted['context']
                actor = context['agent']['id']
                calls.append((kind, actor))
                assert context['objective']['planningMode'] == 'executive_packages'
                assert context['objective']['acceptanceCriteria'] == CRITERIA
                if context['objectiveClarifications']:
                    clarification = context['objectiveClarifications'][0]
                    assert body_text(context, clarification['response']['text']) == answer
                    assert clarification['response']['responderId'] == 'owner'
                    seen_clarifications.append((kind, actor))
                if kind == 'request.decompose':
                    assert actor == 'chief'
                    if not context['objectiveClarifications']:
                        output = {'requests': [{'type': 'request.question', 'team': 'owner-only',
                                                'requestedOutcome': 'Who will read the combined finding?'}]}
                    else:
                        output = package_output()
                elif kind == 'request.plan':
                    assert context['objectiveClarifications']
                    package = context['workPackage']
                    assert package['managerId'] == actor
                    assert len(context['workPackages']) == 3
                    name = actor.rsplit('-', 1)[-1]
                    if name in planning:
                        planning[name].set()
                        other = 'b' if name == 'a' else 'a'
                        assert planning[other].wait(15), 'Independent Manager plans were serialized'
                        overlapping.set()
                    # Scoped context is authoritative; models need not echo package IDs.
                    output = {'tasks': [{'title': f'Finding {name}', 'description': f'Prepare the scoped finding {name}',
                                         'type': 'work.draft', 'team': 'general', 'agentId': f'writer-{name}',
                                         'managerId': actor, 'dependsOn': []}]}
                elif kind == 'work.draft':
                    assert context['objectiveClarifications']
                    package = context['workPackage']
                    assert context['task']['workPackageId'] == package['id']
                    if actor == 'writer-c':
                        assert overlapping.is_set()
                        assert {row['workPackageId'] for row in context['dependencies']} == set(package['dependencyIds'])
                        for row in context['dependencies']:
                            exact = body_text(context, row['deliverable'])
                            assert hashlib.sha256(exact.encode()).hexdigest() == row['sha256']
                            dependency_sources.append(exact)
                        assert set(dependency_sources) == set(sources.values())
                        assert all(row['status'] == 'completed' for row in context['workPackages']
                                   if row['id'] in package['dependencyIds'])
                        output = {'summary': 'Both findings combined', 'deliverable': final}
                    else:
                        assert not context['dependencies']
                        output = {'summary': f'{actor} authored a finding', 'deliverable': sources[actor]}
                elif kind == 'request.review':
                    evidence = context['evidence'][0]
                    exact = body_text(context, evidence['content'])
                    assert exact in [*sources.values(), final]
                    assert hashlib.sha256(exact.encode()).hexdigest() == evidence['sha256']
                    output = {'approved': True, 'summary': 'Exact scoped source independently reviewed',
                              'evidenceIds': [row['id'] for row in context['evidence']]}
                elif kind == 'request.integrate':
                    assert actor == 'coordinator'
                    assert all(row['status'] == 'completed' for row in context['workPackages'])
                    assert len(context['evidence']) == 3
                    output = {'summary': 'One coherent objective result', 'deliverable': final}
                elif kind == 'request.accept':
                    assert actor == 'chief'
                    candidate = next(row for row in context['evidence'] if row.get('kind') == 'integrated_deliverable')
                    assert body_text(context, candidate['content']) == final
                    ids = [row['id'] for row in context['evidence']]
                    assert len(ids) == 4
                    output = {'approved': True, 'summary': 'All root criteria satisfied by the exact combined result',
                              'evidenceIds': ids, 'conflicts': [], 'criteriaResults': [
                                  {'criterion': criterion, 'satisfied': True, 'evidenceIds': ids,
                                   'reason': 'The independently reviewed findings support the integrated result'}
                                  for criterion in context['objective']['acceptanceCriteria']]}
                else:
                    raise AssertionError(f'Unexpected provider stage: {kind}')
            except Exception as exc:
                errors.append(repr(exc))
                output = {'intervention': 'Loopback package assertion failed'}
            response = {'id': 'package-local-only', 'object': 'chat.completion', 'created': 1,
                        'model': 'package-loopback-fixture', 'choices': [{'index': 0, 'finish_reason': 'stop',
                        'message': {'role': 'assistant', 'content': json.dumps(output)}}],
                        'usage': {'prompt_tokens': 31, 'completion_tokens': 17, 'total_tokens': 48}}
            encoded = json.dumps(response).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cfg = {'model': {'provider': 'custom', 'default': 'package-loopback-fixture',
                     'base_url': f'http://127.0.0.1:{server.server_port}/v1',
                     'api_mode': 'chat_completions', 'streaming': False, 'context_length': 128000},
           'agent': {'environment_probe': False}, 'compression': {'enabled': False},
           'organization': {'roster': roster(), 'max_inflight': 4, 'max_workers': 3,
                            'max_tasks': 3, 'max_replans': 0, 'timeout_seconds': 180}}
    (home / 'config.yaml').write_text(json.dumps(cfg), encoding='utf-8')
    (home / '.env').write_text('OPENAI_API_KEY=local-fixture-only\n', encoding='utf-8')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    config._LOAD_CONFIG_CACHE.clear()
    store = OrganizationStore(home / 'organization' / 'state.db', OrganizationSettings.from_config(cfg))
    objective = store.create_objective('Combine independent findings', idempotency_key='http-packages',
        acceptance_criteria=CRITERIA, manager_id='coordinator', executive_id='chief')
    service = OrganizationService(store, home=home, poll_seconds=0.01)
    answered = False
    try:
        service.start()
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            snapshot = store.snapshot()
            if errors or snapshot['objectives'][0]['status'] == 'completed':
                break
            pending = [row for row in snapshot['requests'] if row['status'] == 'pending_intervention']
            if pending:
                if not answered and len(pending) == 1 and pending[0]['type'] == 'request.question':
                    assert service.respond(pending[0]['id'], text=answer, decision='answered', idempotency_key='clarify')
                    answered = True
                else:
                    break
            time.sleep(0.02)
        assert not errors, errors
        assert answered and overlapping.is_set()
        assert snapshot['objectives'][0]['status'] == 'completed', snapshot['requests']
        assert service.stop()
        reopened = OrganizationStore(store.path)
        result = reopened.snapshot()['objectives'][0]
        assert result['id'] == objective['id'] and result['result'] == final
        assert result['planningMode'] == 'executive_packages'
        assert all(row['status'] == 'completed' for row in result['workPackages'])
        assert result['usage']['modelCalls'] == len(calls)
        assert result['usage']['reservedTokens'] > 0
        assert Counter(kind for kind, _ in calls) == Counter({
            'request.decompose': 2, 'request.plan': 3, 'work.draft': 3,
            'request.review': 3, 'request.integrate': 1, 'request.accept': 1})
        assert set(seen_clarifications) == set(calls)
        for request in snapshot['requests']:
            if request['type'] in {'request.decompose', 'request.plan', 'request.integrate', 'request.accept'}:
                audit = reopened.execution_audit(request['id'])
                assert audit['contexts'] and audit['modelCalls']
        assert reopened.claim_next() is None
    finally:
        service.stop()
        server.shutdown()
        server.server_close()
        thread.join(3)
        config._LOAD_CONFIG_CACHE.clear()

"""Large retained evidence through real SQLite, scheduler, AIAgent and loopback HTTP."""
from tests.organization_package_helpers import claim_after_decomposition
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_service import OrganizationService
from eidolon_cli.organization_store import OrganizationStore


@pytest.mark.parametrize('reject_last_source', [False, True])
def test_large_reviewed_artifacts_recover_and_exact_negative_proof_blocks_acceptance(tmp_path, monkeypatch, reject_last_source):
    from eidolon_cli import config
    from eidolon_cli.organization_evidence import CONTEXT_RESERVE_TOKENS, wire_input_bound

    home = tmp_path / 'profile'
    home.mkdir()
    errors, received, seen_chunks = [], [], []
    candidate = 'Recommendation covering all six components: ' + ', '.join(str(index) for index in range(6))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"organization-completion-fixture"}]}')

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append(body)
            try:
                assert not body.get('tools')
                assert body.get('max_tokens') == 8000
                assert wire_input_bound(body) + 8000 + CONTEXT_RESERVE_TOKENS <= 128000
                prompt = next(row['content'] for row in body['messages'] if row['role'] == 'user')
                submitted = json.loads(prompt.split('Submitted context:\n', 1)[1])
                kind, context = submitted['request']['type'], submitted['context']
                assert kind in {'request.integrate', 'request.accept'}
                if 'sourceChunk' in submitted:
                    chunk, proof = submitted['sourceChunk'], submitted['sourceRange']
                    assert hashlib.sha256(chunk.encode()).hexdigest() == proof['chunkSha256']
                    assert len(chunk.encode()) == proof['end'] - proof['start']
                    seen_chunks.append((kind, chunk))
                    negative = reject_last_source and kind == 'request.accept' and 'Unique ending 5' in chunk
                    if kind == 'request.accept':
                        assert context['integratedCandidate']['content'] == candidate
                    output = {'approved': not negative, 'findings': 'Checked exact source ' + proof['sourceKey'],
                              'conflicts': ['Source 5 contradicts the integrated recommendation.'] if negative else []}
                elif kind == 'request.integrate':
                    assert context['evidenceReadPasses']
                    output = {'summary': 'All six components integrated', 'deliverable': candidate}
                else:
                    assert not reject_last_source, 'A negative read must bypass the optimistic final model'
                    assert context['integratedCandidate']['content'] == candidate
                    ids = [item['id'] for item in context['evidence']]
                    output = {'approved': True, 'summary': 'Complete exact independent coverage checked.',
                              'evidenceIds': ids, 'conflicts': [], 'criteriaResults': [
                                  {'criterion': criterion, 'satisfied': True, 'evidenceIds': ids,
                                   'reason': 'All original source ranges and complete integrated candidate were compared.'}
                                  for criterion in context['objective']['acceptanceCriteria']]}
            except Exception as exc:
                errors.append(repr(exc))
                output = {'intervention': 'Loopback fixture assertion failed'}
            response = {'id': 'local-only-completion', 'object': 'chat.completion', 'created': 1,
                        'model': 'organization-completion-fixture', 'choices': [
                            {'index': 0, 'message': {'role': 'assistant', 'content': json.dumps(output)}, 'finish_reason': 'stop'}]}
            # Deliberately omit usage: successful execution must retain unknown
            # observed usage and all conservative reservations on restart.
            data = json.dumps(response).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cfg = {'model': {'provider': 'custom', 'default': 'organization-completion-fixture',
                     'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'api_mode': 'chat_completions',
                     'streaming': False, 'context_length': 128000},
           'agent': {'environment_probe': False}, 'compression': {'enabled': False},
           'organization': {'max_replans': 0, 'max_inflight': 1, 'timeout_seconds': 180}}
    (home / 'config.yaml').write_text(json.dumps(cfg), encoding='utf-8')
    (home / '.env').write_text('OPENAI_API_KEY=local-fixture-only\n', encoding='utf-8')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    config._LOAD_CONFIG_CACHE.clear()
    store = OrganizationStore(home / 'organization' / 'state.db', OrganizationSettings.from_config(cfg))
    objective = store.create_objective('Integrate six reviewed components', idempotency_key='large-objective',
                                       acceptance_criteria=['One supported integrated recommendation'])
    plan = claim_after_decomposition(store)
    store.finish(plan, {'tasks': [{'title': f'Component {index}', 'description': f'Prepare component {index}',
                                  'type': 'work.draft', 'dependsOn': []} for index in range(6)]})
    retained = []
    while len(retained) < 6:
        claim = store.claim_next()
        if claim['type'] == 'request.hire':
            store.finish(claim, {})
        elif claim['type'] == 'work.draft':
            task = store.context(claim)['task']
            index = int(task['title'].split()[-1])
            content = f'Unique opening {index}\n' + chr(65 + index) * 27500 + f'\nUnique ending {index}'
            store.finish(claim, {'summary': f'Component {index}', 'deliverable': content})
        else:
            assert claim['type'] == 'request.review'
            evidence = store.context(claim)['evidence'][0]
            retained.append(evidence)
            store.finish(claim, {'approved': True, 'summary': 'Exact individual component reviewed', 'evidenceIds': [evidence['id']]})
    assert sum(len(row['content']) for row in retained) > 165000
    service = OrganizationService(store, home=home, poll_seconds=0.02)
    try:
        service.start()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            snapshot = store.snapshot()
            if snapshot['objectives'][0]['status'] in {'completed', 'needs_input'}:
                break
            time.sleep(0.05)
        assert not errors, errors
        assert snapshot['objectives'][0]['status'] == ('needs_input' if reject_last_source else 'completed'), snapshot['requests']
        assert service.stop()
        reopened = OrganizationStore(store.path)
        outcome = reopened.snapshot()['objectives'][0]
        assert outcome['usage']['usageComplete'] is False
        assert outcome['usage']['modelCalls'] == len(received)
        assert outcome['usage']['reservedTokens'] > 0
        for stage in ('request.integrate', 'request.accept'):
            claim = next(row for row in snapshot['requests'] if row['type'] == stage and row['attempts'])
            audit = reopened.execution_audit(claim['id'])
            assert audit['contexts'][0]['report']['mode'] == 'hierarchical'
            assert audit['contexts'][0]['report']['fullEvidence'] is False
            passes = [row['report'] for row in audit['evidencePasses']]
            # Validation against original store context is covered before finish;
            # this read proves exact audit records persist across real restart.
            assert len(passes) >= 6 and len(audit['modelCalls']) >= len(passes)
            assert audit['contexts'][0]['report']['passCount'] == len(passes)
            for original in retained:
                assert any(original['content'] == chunk for kind, chunk in seen_chunks if kind == stage)
                assert reopened.evidence(original['id'])['content'] == original['content']
            if stage == 'request.accept' and reject_last_source:
                assert any(not row['approved'] and row['conflicts'] for row in passes)
        assert not reopened.claim_next()
    finally:
        service.stop()
        server.shutdown()
        server.server_close()
        thread.join(3)
        config._LOAD_CONFIG_CACHE.clear()

"""Real loopback HTTP proves selected identities, effort and durable attempt links."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time

import pytest

from eidolon_cli import config
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_service import OrganizationService
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_model_routing import route, configuration


@pytest.mark.parametrize('first_content,finish_reason,expected_sends', [
    ('not JSON', 'stop', 2), ('{}', 'stop', 1), ('not JSON', 'length', 1),
    ('AUTH', 'stop', 1), ('UNKNOWN', 'stop', 1),
    ('CANCEL', 'stop', 0), ('STALE', 'stop', 0), ('BUDGET', 'stop', 0),
    ('OBSERVATION_FAIL', 'stop', 1), ('REASONING', 'stop', 0), ('CAP', 'stop', 0), ('ENDPOINT', 'stop', 0),
])
def test_actual_http_fresh_fallback_only_for_complete_invalid_json(tmp_path, monkeypatch, first_content, finish_reason, expected_sends):
    received, endpoints = [], []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            endpoints.append(('GET', self.path))
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"data":[]}')

        def do_POST(self):
            endpoints.append(('POST', self.path))
            wire = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append(wire)
            if first_content == 'AUTH':
                self.send_response(401)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"Fixture credential rejection","type":"authentication_error"}}')
                return
            if first_content == 'UNKNOWN':
                self.close_connection = True
                return
            valid = json.dumps({'workPackages': [{'title': 'Inspect facts', 'description': 'Analyze exact supplied facts',
                'managerId': 'manager', 'criterionIndexes': [0], 'projectIds': [], 'dependsOn': [], 'maxTasks': 1}], 'requiredChecks': []})
            response = {'id': 'loopback-result', 'object': 'chat.completion', 'created': 1, 'model': wire['model'],
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': ('{}' if first_content == 'OBSERVATION_FAIL' else first_content) if len(received) == 1 else valid},
                             'finish_reason': finish_reason if len(received) == 1 else 'stop'}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}
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
    home = tmp_path / 'profile'
    home.mkdir()
    routing = configuration(routes=[route('first'), route('second')], fallbacks=1)
    cfg = {'model': {'provider': 'named-local', 'default': 'legacy-model', 'context_length': 32768},
        'providers': {'named-local': {'name': 'Named Local', 'api': f'http://127.0.0.1:{server.server_port}/v1',
            'api_mode': 'chat_completions', 'key_env': 'LOOPBACK_KEY'}},
        'agent': {'environment_probe': False, 'reasoning_effort': 'high', 'reasoning_overrides': {'first': 'ultra'}},
        'compression': {'enabled': False},
        'organization': {'max_output_tokens': 512, 'max_context_tokens': 32768, 'max_inflight': 1,
            'max_model_calls': 2, 'max_cost_usd': '1', **routing,
            'model_costs': [{'provider': p, 'model': m, 'input_usd_per_million': cost, 'output_usd_per_million': cost}
                            for p, m, cost in [('named-local', 'first', '2'), ('named-local', 'second', '4'),
                                               ('custom', 'first', '0'), ('custom', 'second', '0')]]}}
    if first_content == 'BUDGET':
        cfg['organization'].update(max_model_calls=1, completion_reserve_model_calls=1)
    (home / 'config.yaml').write_text(json.dumps(cfg), encoding='utf-8')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('LOOPBACK_KEY', 'loopback-fixture-only')
    config._LOAD_CONFIG_CACHE.clear()
    store = OrganizationStore(home / 'organization' / 'state.db', OrganizationSettings.from_config(cfg))
    store.create_objective('Retain exact fact: emeralds are green.', idempotency_key='routing')
    service = OrganizationService(store, home=home, poll_seconds=0.02)
    measurements = []
    persist = store.record_model_observation
    def capture_observation(claim, call_id, **values):
        measurements.append((call_id, values))
        if first_content == 'OBSERVATION_FAIL':
            raise OSError('Fixture observation write failure')
        return persist(claim, call_id, **values)
    monkeypatch.setattr(store, 'record_model_observation', capture_observation)
    from eidolon_cli.organization_model_routing_executor import RoutedBoundary
    if first_content in {'CANCEL', 'STALE'}:
        original = RoutedBoundary._create_request_openai_client
        def block_before_boundary(agent, **kwargs):
            client = original(agent, **kwargs)
            if first_content == 'CANCEL':
                agent._organization_cancel.set()
            else:
                from dataclasses import replace
                store.reload_configuration(replace(store.settings, max_output_tokens=513))
            return client
        monkeypatch.setattr(RoutedBoundary, '_create_request_openai_client', block_before_boundary)
    if first_content in {'REASONING', 'CAP', 'ENDPOINT'}:
        original = RoutedBoundary._interruptible_api_call
        def override_wire(agent, options):
            options = dict(options)
            if first_content == 'REASONING':
                options['reasoning_effort'] = 'high'
            elif first_content == 'CAP':
                options['max_tokens'] = 256
            else:
                options['extra_query'] = {'tenant': 'unexpected'}
            return original(agent, options)
        monkeypatch.setattr(RoutedBoundary, '_interruptible_api_call', override_wire)
    try:
        service.start()
        deadline = time.monotonic() + 25
        terminal = None
        while time.monotonic() < deadline:
            terminal = next(row for row in store.snapshot()['requests'] if row['type'] == 'request.decompose')
            if terminal['status'] in {'completed', 'pending_intervention'}:
                break
            time.sleep(0.03)
        assert terminal['status'] in {'completed', 'pending_intervention'}, terminal
        assert len(received) == expected_sends, terminal
        assert endpoints == [('POST', '/v1/chat/completions')] * expected_sends
        assert all(w['reasoning_effort'] == 'low' for w in received)
        assert all(w.get('max_tokens', w.get('max_completion_tokens')) == 512 for w in received)
        assert [w['model'] for w in received] == ['first', 'second'][:expected_sends]
        assert all('emeralds are green' in json.dumps(w['messages']) for w in received)
        audit = store.execution_audit(terminal['id'])
        assert len(audit['modelCalls']) == len(audit['modelDispatches']) == expected_sends
        assert len({row['attemptToken'] for row in audit['modelDispatches']}) == expected_sends
        for dispatch, call in zip(audit['modelDispatches'], audit['modelCalls']):
            assert call['dispatchId'] == dispatch['id']
            assert call['provider'] == dispatch['selectedProvider'] == 'named-local'
            assert dispatch['adapterProvider'] == 'custom'
            assert call['reserved_cost_usd'] != '0.000000000'
        assert len(audit['modelRouteFailures']) == (1 if expected_sends == 2 else 0)
        assert terminal['attempts'] == (2 if expected_sends == 2 else 1)
        assert '127.0.0.1' not in json.dumps(audit)
        from eidolon_cli.organization_routing_observations import routing_observations
        measured = routing_observations(store.path)['observations']
        assert len(measured) == expected_sends
        assert len(measurements) == expected_sends
        assert {call for call, _ in measurements} == {row['callId'] for row in measured}
        for _, values in measurements:
            assert values['elapsed_seconds'] >= 0
            assert values['outcome'] == ('error' if first_content in {'AUTH', 'UNKNOWN'} else 'response')
            assert (values['input_tokens'], values['output_tokens']) == ((None, None) if first_content in {'AUTH', 'UNKNOWN'} else (10, 5))
        for row in measured:
            assert row['actualCostUsd'] is None
            if first_content == 'OBSERVATION_FAIL':
                assert row['latencySeconds'] is None
                continue
            if row['transportOutcome'] == 'unobserved':
                # Another organization writer can win the fail-fast diagnostic
                # transaction. Transport measurements above still prove collection.
                assert row['latencySeconds'] is None
                assert row['providerReportedUsage'] is None
                continue
            assert row['latencySeconds'] >= 0
            assert row['physicalRetries'] == 0
            if first_content in {'AUTH', 'UNKNOWN'}:
                assert row['transportOutcome'] == 'error'
                assert row['providerReportedUsage'] == {'inputTokens': None, 'outputTokens': None}
            else:
                assert row['transportOutcome'] == 'response'
                assert row['providerReportedUsage'] == {'inputTokens': 10, 'outputTokens': 5}

    finally:
        assert service.stop(timeout=10)
        server.shutdown()
        server.server_close()
        thread.join(3)
        config._LOAD_CONFIG_CACHE.clear()

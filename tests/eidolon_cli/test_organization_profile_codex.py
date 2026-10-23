"""Real scheduler/shared agent, profile-owned synthetic auth, in-memory HTTP only."""
import json
import os

import httpx
import pytest

from eidolon_cli import config
from tests.eidolon_cli.test_organization_isolated_admission import origin, workspace, claim_work, execute  # noqa: F401
from tests.eidolon_cli.test_profile_owned_codex import auth


def response(items):
    events = [{'type': 'response.output_item.done', 'output_index': index, 'item': item}
              for index, item in enumerate(items)]
    events.append({'type': 'response.completed', 'response': {'id': 'synthetic-response',
        'status': 'completed', 'output': items,
        'usage': {'input_tokens': 31, 'output_tokens': 21, 'total_tokens': 52}}})
    return httpx.Response(200, headers={'content-type': 'text/event-stream'},
        content=''.join('data: ' + json.dumps(event) + '\n\n' for event in events).encode())


def message(value):
    return {'id': 'synthetic-message', 'type': 'message', 'role': 'assistant', 'status': 'completed',
            'content': [{'type': 'output_text', 'text': json.dumps(value), 'annotations': []}]}


@pytest.mark.skipif(os.name != 'posix', reason='Source work requires existing POSIX descriptors')
@pytest.mark.parametrize("refresh_between_rounds", [False, True])
def test_shared_codex_tool_conversation_and_independent_review(workspace, monkeypatch, refresh_between_rounds):
    w = workspace
    cfg = json.loads((w.home / 'config.yaml').read_text())
    cfg['model'].update(provider='openai-codex', default='gpt-5.4')
    (w.home / 'config.yaml').write_text(json.dumps(cfg))
    config._LOAD_CONFIG_CACHE.clear()
    import time
    from types import SimpleNamespace
    from tests.eidolon_cli.test_profile_owned_codex import token
    from eidolon_cli import profile_codex_auth, auth_codex
    now = time.time()
    tokens = auth(w.home, token(expiry=now + 120))
    refreshed = []
    def refresh(access, secret, **kwargs):
        nonlocal key
        assert access == key and kwargs['private_transport'] is True
        key = token(expiry=now + 3600)
        refreshed.append(True)
        return {'access_token': key, 'refresh_token': 'synthetic-rotated'}
    monkeypatch.setattr(auth_codex, 'refresh_codex_oauth_pure', refresh)
    key = tokens['providers']['openai-codex']['tokens']['access_token']
    _, claim = claim_work(w, 'work.inspect')
    calls = []
    def http(transport, request):
        assert request.url.host == 'chatgpt.com' and request.url.path == '/backend-api/codex/responses'
        assert request.headers['authorization'] == 'Bearer ' + key
        assert request.headers['chatgpt-account-id'] == 'synthetic-account'
        body = json.loads(request.read())
        assert 'max_output_tokens' not in body
        calls.append(body)
        if len(calls) == 1:
            if refresh_between_rounds:
                monkeypatch.setattr(profile_codex_auth, 'time', SimpleNamespace(time=lambda: now + 150, monotonic=time.monotonic))
            assert [tool['name'] for tool in body['tools']] == ['read_file']
            return response([{'id': 'synthetic-call', 'type': 'function_call', 'call_id': 'read-1',
                'name': 'read_file', 'arguments': '{"path":"root0/notes.txt"}', 'status': 'completed'}])
        if len(calls) == 2:
            assert len(w.store.request_tool_receipts(claim)) == 1
            assert any(item.get('type') == 'function_call_output' for item in body['input'])
            return response([message({'summary': 'Read source', 'deliverable': 'Before is present.',
                'memory': {'facts': ['The source contains Before.']}})])
        assert len(calls) == 3 and not body.get('tools')
        with w.store._connect() as conn:
            evidence = conn.execute('SELECT id FROM evidence WHERE request_id=?', (claim['id'],)).fetchone()[0]
        assert evidence in json.dumps(body)
        return response([message({'approved': True, 'summary': 'Checked retained source evidence.',
                                  'evidenceIds': [evidence]})])
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', http)
    record = execute(w, claim)
    assert 'intervention' not in record.result, record.result
    assert len(calls) == 2
    assert w.store.finish(claim, record.result)
    review = w.store.claim_next()
    assert review['type'] == 'request.review' and review['agent_id'] != claim['agent_id']
    checked = execute(w, review)
    assert checked.result.get('approved') is True, checked.result
    assert w.store.finish(review, checked.result)
    assert len(calls) == 3
    assert len(refreshed) == int(refresh_between_rounds)
    with w.store._connect() as conn:
        rows = conn.execute('SELECT output_contract FROM organization_model_call_contracts').fetchall()
        assert [row[0] for row in rows] == ['subscription-reservation'] * 3
        observations = conn.execute('SELECT input_tokens,output_tokens,outcome FROM organization_route_observations').fetchall()
        assert [tuple(row) for row in observations] == [(31, 21, 'response')] * 3


@pytest.mark.skipif(os.name != 'posix', reason='Source work requires existing POSIX descriptors')
@pytest.mark.parametrize('change', ['auth_revoked', 'auth_changed', 'cancel', 'grant_revoked', 'route_changed', 'budget'])
def test_changed_authority_blocks_second_physical_send(workspace, monkeypatch, change):
    from eidolon_cli.organization_service import OrganizationService, _Running
    w = workspace
    cfg = json.loads((w.home / 'config.yaml').read_text())
    cfg['model'].update(provider='openai-codex', default='gpt-5.4')
    (w.home / 'config.yaml').write_text(json.dumps(cfg))
    config._LOAD_CONFIG_CACHE.clear()
    auth(w.home)
    _, claim = claim_work(w, 'work.inspect')
    record = _Running(claim)
    calls = []
    original_finish = w.store.record_tool_finish
    def finish(*args, **kwargs):
        result = original_finish(*args, **kwargs)
        if change == 'auth_revoked': (w.home / 'auth.json').unlink()
        elif change == 'auth_changed':
            from tests.eidolon_cli.test_profile_owned_codex import token
            auth(w.home, token('foreign-account'))
        elif change == 'cancel': record.cancel.set()
        elif change == 'route_changed':
            cfg['model']['default'] = 'different-model'
            (w.home / 'config.yaml').write_text(json.dumps(cfg))
            config._LOAD_CONFIG_CACHE.clear()
        elif change == 'grant_revoked':
            with w.store._write() as conn:
                conn.execute("UPDATE agents SET tool_grants='[]' WHERE id=?", (claim['agent_id'],))
        elif change == 'budget':
            with w.store._write() as conn:
                conn.execute('UPDATE objective_budgets SET max_model_calls=1 WHERE objective_id=?', (claim['objective_id'],))
        return result
    monkeypatch.setattr(w.store, 'record_tool_finish', finish)
    def http(transport, request):
        assert request.url.host == 'chatgpt.com'
        calls.append(json.loads(request.read()))
        assert len(calls) == 1, 'Changed authority must stop subsequent physical sends'
        return response([{'id': 'synthetic-call', 'type': 'function_call', 'call_id': 'read-1',
            'name': 'read_file', 'arguments': '{"path":"root0/notes.txt"}', 'status': 'completed'}])
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', http)
    OrganizationService(w.store, home=w.home, settings=w.settings)._work(record)
    assert record.error is None, record.error
    assert 'intervention' in record.result, record.result
    assert len(calls) == 1
    with w.store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM organization_model_calls').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM evidence WHERE request_id=?', (claim['id'],)).fetchone()[0] == 0

@pytest.mark.skipif(os.name != 'posix', reason='Source work requires existing POSIX descriptors')
@pytest.mark.parametrize('change', ['none', 'cancel', 'revoke', 'budget'])
def test_stream_reconnect_is_separately_admitted(workspace, monkeypatch, change):
    from eidolon_cli.organization_service import OrganizationService, _Running
    w = workspace
    cfg = json.loads((w.home / 'config.yaml').read_text())
    cfg['model'].update(provider='openai-codex', default='gpt-5.4')
    (w.home / 'config.yaml').write_text(json.dumps(cfg))
    config._LOAD_CONFIG_CACHE.clear()
    auth(w.home)
    _, claim = claim_work(w, 'work.inspect')
    record = _Running(claim)
    calls = []
    class InterruptedStream(httpx.SyncByteStream):
        def __iter__(self):
            if change == 'cancel': record.cancel.set()
            elif change == 'revoke': (w.home / 'auth.json').unlink()
            elif change == 'budget':
                with w.store._write() as conn:
                    conn.execute('UPDATE objective_budgets SET max_model_calls=1 WHERE objective_id=?', (claim['objective_id'],))
            raise httpx.ReadTimeout('Synthetic interrupted stream')
            yield b''
    def http(transport, request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=InterruptedStream())
        assert change == 'none' and len(calls) == 2
        return response([message({'summary': 'Synthetic reconnect', 'deliverable': 'No source evidence claimed.'})])
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', http)
    OrganizationService(w.store, home=w.home, settings=w.settings)._work(record)
    assert record.error is None, record.error
    assert len(calls) == (2 if change == 'none' else 1)
    with w.store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM organization_model_calls').fetchone()[0] == len(calls)
        rows = conn.execute('SELECT input_tokens,output_tokens,outcome FROM organization_route_observations ORDER BY created').fetchall()
        assert tuple(rows[0]) == (None, None, 'error')
        assert len(rows) == len(calls)
        if change == 'none': assert tuple(rows[1]) == (31, 21, 'response')

@pytest.mark.skipif(os.name != 'posix', reason='Source work requires existing POSIX descriptors')
def test_subscription_cannot_claim_configured_monetary_ceiling(workspace, monkeypatch):
    from dataclasses import replace
    from eidolon_cli.organization_store import OrganizationStore
    w = workspace
    cfg = json.loads((w.home / 'config.yaml').read_text())
    cfg['model'].update(provider='openai-codex', default='gpt-5.4')
    (w.home / 'config.yaml').write_text(json.dumps(cfg))
    config._LOAD_CONFIG_CACHE.clear()
    auth(w.home)
    w.settings = replace(w.settings, max_cost_usd='1')
    w.store = OrganizationStore(w.store.path, w.settings)
    _, claim = claim_work(w, 'work.inspect')
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda *a: pytest.fail('No enforceable monetary ceiling; no send'))
    record = execute(w, claim)
    assert 'intervention' in record.result
    with w.store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM organization_model_calls').fetchone()[0] == 0


def test_subscription_usage_is_not_clamped_or_fabricated(workspace):
    import sqlite3
    from eidolon_cli.organization_budget import budget_view
    from eidolon_cli.organization_routing_observations import record_observation
    w = workspace
    cfg = json.loads((w.home / 'config.yaml').read_text())
    cfg['model'].update(provider='openai-codex', default='gpt-5.4')
    (w.home / 'config.yaml').write_text(json.dumps(cfg))
    config._LOAD_CONFIG_CACHE.clear()
    auth(w.home)
    _, claim = claim_work(w, 'work.inspect')
    call = w.store.reserve_model_call(claim, provider='openai-codex', model='gpt-5.4', input_limit=100, output_limit=100)
    with w.store._write() as conn:
        record_observation(conn, claim, call, input_tokens=50, output_tokens=900, elapsed_seconds=1, outcome='response')
        view = budget_view(conn, claim['objective_id'], w.settings)
        assert view['reservedTokens'] == 200
        assert view['configuredCostReservedUsd'] is None
        assert view['subscriptionBudget'] == {'version': 1, 'calls': 1, 'outputContract': 'reservation-only', 'providerOutputCeiling': False, 'reportedInputTokens': 50, 'reportedOutputTokens': 900, 'usageComplete': True}
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute("UPDATE organization_model_call_contracts SET output_contract='provider-cap'")
    w.store.reserve_model_call(claim, provider='openai-codex', model='gpt-5.4', input_limit=100, output_limit=100)
    with w.store._write() as conn:
        # A completed logical stage cannot erase an unknown physical attempt.
        conn.execute('UPDATE objective_usage SET input_tokens=50,output_tokens=900,completed=1 WHERE objective_id=?', (claim['objective_id'],))
        view = budget_view(conn, claim['objective_id'], w.settings)
        assert view['subscriptionBudget']['usageComplete'] is False
        assert view['subscriptionBudget']['reportedOutputTokens'] == 900
        from eidolon_cli.organization_acceptance import usage_view
        control = conn.execute('SELECT * FROM objective_control WHERE objective_id=?', (claim['objective_id'],)).fetchone()
        assert usage_view(conn, control, w.settings)['usageComplete'] is False
        assert usage_view(conn, control, w.settings)['perCallOutputLimit'] is None

@pytest.mark.skipif(os.name != 'posix', reason='Source work requires existing POSIX descriptors')
def test_subscription_preserves_stage_start_pacing_latch(workspace, monkeypatch):
    from dataclasses import replace
    from eidolon_cli.organization_store import OrganizationStore
    from eidolon_cli.organization_service import OrganizationService, _Running
    from tests.eidolon_cli.test_organization_completion_reserve import backend_request
    from tests.eidolon_cli.test_organization_pacing import observe
    w = workspace
    cfg = json.loads((w.home / 'config.yaml').read_text())
    cfg['model'].update(provider='openai-codex', default='gpt-5.4')
    (w.home / 'config.yaml').write_text(json.dumps(cfg))
    config._LOAD_CONFIG_CACHE.clear()
    auth(w.home)
    w.settings = replace(w.settings, adaptive_pacing_enabled=True)
    w.store = OrganizationStore(w.store.path, w.settings)
    objective, claim = claim_work(w, 'work.inspect')
    backend_request(w.store, objective, 'request.plan')
    winner = w.store.claim_next()
    observe(w.store, objective, 5)
    w.store.reserve_model_call(winner, provider='openai-codex', model='gpt-5.4', input_limit=100, output_limit=100)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda *a: pytest.fail('Pacing deferral must precede send'))
    record = _Running(claim)
    OrganizationService(w.store, home=w.home, settings=w.settings)._work(record)
    assert record.error == str(record.pacing_wait)
    assert record.pacing_wait is not None
    with w.store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM organization_model_calls WHERE request_id=?', (claim['id'],)).fetchone()[0] == 0

@pytest.mark.skipif(os.name != 'posix', reason='Source work requires existing POSIX descriptors')
@pytest.mark.parametrize('header', ['authorization', 'ChatGPT-Account-ID'])
def test_late_request_header_override_never_reaches_transport(workspace, monkeypatch, header):
    from agent import codex_runtime
    w = workspace
    cfg = json.loads((w.home / 'config.yaml').read_text())
    cfg['model'].update(provider='openai-codex', default='gpt-5.4')
    (w.home / 'config.yaml').write_text(json.dumps(cfg))
    config._LOAD_CONFIG_CACHE.clear()
    auth(w.home)
    _, claim = claim_work(w, 'work.inspect')
    original = codex_runtime._sanitize_consumer_codex_request
    def override(agent, options):
        return {**original(agent, options), 'extra_headers': {header: 'foreign'}}
    monkeypatch.setattr(codex_runtime, '_sanitize_consumer_codex_request', override)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda *a: pytest.fail('Foreign header must not reach transport'))
    record = execute(w, claim)
    assert 'intervention' in record.result
    with w.store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM organization_model_calls').fetchone()[0] == 0

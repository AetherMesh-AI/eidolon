"""Pacing race denials must survive runtime exception handling without replay."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

from eidolon_cli import config, organization_executor as executor
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_pacing import OrganizationPacingDeferred
from eidolon_cli.organization_service import OrganizationService, _Running
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_completion_reserve import reserve, usage, work_claim
from tests.eidolon_cli.test_organization_pacing import observe


def test_service_requeues_a_latched_zero_call_denial_even_if_executor_swallows_it(tmp_path):
    settings = OrganizationSettings(adaptive_pacing_enabled=True)
    store2 = OrganizationStore(tmp_path / 'second' / 'organization' / 'state.db', settings)
    objective2 = store2.create_objective('Race before first call', idempotency_key='objective')
    race = store2.claim_next()
    calls = []

    def swallow(request, context, cancel):
        # A provider/core wrapper may swallow the denial, and even retry it.
        for _ in range(2):
            try:
                context['reserveModelCall'](provider='local', model='fixture', input_limit=100, output_limit=100)
            except OrganizationPacingDeferred:
                calls.append('deferred')
        return {'intervention': 'Generic runtime interception'}

    service = OrganizationService(store2, home=tmp_path / 'second', executor=swallow)
    # Lower the advisory while another backend-owned stage wins the start slot.
    from tests.eidolon_cli.test_organization_completion_reserve import backend_request
    backend_request(store2, objective2, 'request.plan')
    winner = store2.claim_next()
    observe(store2, objective2, 5)
    reserve(store2, winner)
    record = _Running(race)
    record.thread = threading.Thread(target=service._work, args=(record,))
    service._running[race['id']] = record
    record.thread.start()
    record.thread.join(10)
    assert not record.thread.is_alive()
    service._maintain()
    assert calls == ['deferred', 'deferred']
    with store2._connect() as conn:
        row = conn.execute('SELECT * FROM requests WHERE id=?', (race['id'],)).fetchone()
        assert row['status'] == 'queued' and row['attempts'] == 0
        assert conn.execute('SELECT count(*) FROM objective_usage WHERE request_id=?', (race['id'],)).fetchone()[0] == 0
    assert usage(store2, objective2)['modelCalls'] == 1


def test_real_executor_final_pacing_gate_sends_zero_posts_and_service_keeps_context_receipt(tmp_path, monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"pacing-fixture","context_length":128000}]}')

        def do_POST(self):
            received.append(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(500)
            self.end_headers()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    home = tmp_path / 'profile'
    home.mkdir()
    cfg = {'model': {'provider': 'custom', 'default': 'pacing-fixture',
                     'base_url': f'http://127.0.0.1:{server.server_port}/v1',
                     'api_mode': 'chat_completions', 'streaming': False, 'context_length': 128000},
           'agent': {'environment_probe': False}, 'compression': {'enabled': False},
           'organization': {'adaptive_pacing_enabled': True}}
    (home / 'config.yaml').write_text(json.dumps(cfg))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('OPENAI_API_KEY', 'local-loopback-fixture-only')
    config._LOAD_CONFIG_CACHE.clear()
    store = OrganizationStore(home / 'organization' / 'state.db', OrganizationSettings.from_config(cfg))
    objective = store.create_objective('Keep exact pending work', idempotency_key='fixture')
    try:
        work = work_claim(store)
        from tests.eidolon_cli.test_organization_completion_reserve import backend_request
        backend_request(store, objective, 'request.plan')
        winner = store.claim_next()
        assert winner['type'] == 'request.plan'
        observe(store, objective, 5)
        reserve(store, winner)
        original = store.reserve_model_call
        attempts = []

        def deny(claim, **values):
            attempts.append(claim['id'])
            return original(claim, **values)

        monkeypatch.setattr(store, 'reserve_model_call', deny)
        service = OrganizationService(store, home=home, executor=executor.execute)
        record = _Running(work)
        record.thread = threading.Thread(target=service._work, args=(record,))
        service._running[work['id']] = record
        record.thread.start()
        record.thread.join(30)
        assert not record.thread.is_alive()
        assert record.pacing_wait is not None
        service._maintain()
        assert received == []
        assert attempts == [work['id']]
        with store._connect() as conn:
            row = conn.execute('SELECT * FROM requests WHERE id=?', (work['id'],)).fetchone()
            assert row['status'] == 'queued' and row['attempts'] == 0
            assert conn.execute('SELECT count(*) FROM organization_context_receipts WHERE request_id=?', (work['id'],)).fetchone()[0] == 1
        assert usage(store, objective)['modelCalls'] == 1
        monkeypatch.setattr(store, 'reserve_model_call', original)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        config._LOAD_CONFIG_CACHE.clear()

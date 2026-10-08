"""Scoped staffing preserves real executive and package-plan continuations."""


import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore



@pytest.fixture
def wire(tmp_path, monkeypatch):
    """Exercise real AIAgent, guarded wire and ledger without external calls."""
    from eidolon_cli import config
    from eidolon_cli.organization_executor import execute

    state = {'output': {}, 'contexts': [], 'models': []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"staffing-fixture"}]}')

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            prompt = next(row['content'] for row in body['messages'] if row['role'] == 'user')
            state['contexts'].append(json.loads(prompt.split('Submitted context:\n', 1)[1]))
            state['models'].append(body['model'])
            response = {'id': 'staffing-loopback', 'object': 'chat.completion', 'created': 1,
                'model': 'staffing-fixture', 'choices': [{'index': 0, 'message': {
                    'role': 'assistant', 'content': json.dumps(state['output'])}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18}}
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
    home = tmp_path / 'profile'
    home.mkdir()
    cfg = {'model': {'provider': 'custom', 'default': 'staffing-fixture',
        'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'api_mode': 'chat_completions',
        'streaming': False, 'context_length': 128000},
        'agent': {'environment_probe': False}, 'compression': {'enabled': False}}
    (home / 'config.yaml').write_text(json.dumps(cfg), encoding='utf-8')
    (home / '.env').write_text('OPENAI_API_KEY=local-fixture-only\n', encoding='utf-8')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    config._LOAD_CONFIG_CACHE.clear()

    def finish(store, claim, output):
        state['output'] = output
        result = execute(claim, store.context(claim), threading.Event())
        assert 'intervention' not in result, result
        return store.finish(claim, result)

    try:
        yield finish, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        config._LOAD_CONFIG_CACHE.clear()


def _setup(tmp_path, stage, *, global_staffer=False, authorized=True, max_members=16):
    roster = [
        {'id': 'chief', 'name': 'Chief', 'role': 'Executive', 'team': 'general'},
        {'id': 'lead', 'name': 'Lead', 'role': 'Manager', 'team': 'red', 'manager_id': 'chief'},
        {'id': 'staffer', 'name': 'Staffer', 'role': 'Manager', 'team': 'people' if global_staffer else 'red',
         'manager_id': 'chief', 'capabilities': ['request.hire'],
         'authority': ['staff.manage'] if authorized else [],
         'managed_teams': ['*'] if global_staffer else [],
         'provider': 'custom', 'model': 'staffing-fixture'},
    ]
    settings = OrganizationSettings.from_config({'organization': {
        'roster': roster, 'max_members': max_members, 'max_workers': 1, 'max_inflight': 1}})
    store = OrganizationStore(tmp_path / 'organization.db', settings)
    objective = store.create_objective('Produce the red specialist brief', idempotency_key='goal',
        acceptance_criteria=['Explain the red finding'], manager_id='lead', executive_id='chief', priority='normal')
    package = {'workPackages': [{'title': 'Red brief', 'description': 'Explain the red finding',
        'managerId': 'lead', 'criterionIndexes': [0], 'projectIds': [], 'dependsOn': [], 'maxTasks': 1}]}
    claim = store.claim_next()
    assert claim['type'] == 'request.decompose'
    if stage == 'plan':
        assert store.finish(claim, package)
        claim = store.claim_next()
        assert claim['type'] == 'request.plan'
    member = {'id': 'specialist', 'name': 'Red specialist', 'manager_id': 'lead',
        'capabilities': ['work.draft'], 'responsibilities': ['Own red finding briefs'],
        'scope': 'Red findings', 'provider': 'custom', 'model': 'staffing-fixture'}
    request = {'type': 'request.hire', 'requestedOutcome': 'Fill the lasting red brief responsibility',
               'managementProposal': {'members': [member]}}
    if stage == 'decompose':
        request['team'] = 'red'
    return store, settings, objective, package, claim, request


@pytest.mark.parametrize('stage', ['decompose', 'plan'])
@pytest.mark.parametrize('global_staffer', [False, True])
def test_scoped_hire_resumes_exact_planning_context_and_reuses_persistent_specialist(tmp_path, wire, stage, global_staffer):
    finish, sent = wire
    store, seed, objective, package, paused, request = _setup(tmp_path, stage, global_staffer=global_staffer)
    before = store.context(paused)
    offered = next(staff for staff in before['staffing'] if staff['id'] == 'staffer')
    assert (offered['provider'], offered['model']) == ('custom', 'staffing-fixture')
    with store._connect() as conn:
        budget = dict(conn.execute('SELECT * FROM objective_control WHERE objective_id=?', (objective['id'],)).fetchone())
        ceilings = dict(conn.execute('SELECT * FROM objective_budgets WHERE objective_id=?', (objective['id'],)).fetchone())
    assert finish(store, paused, {'requests': [request]})
    submitted = sent['contexts'][-1]['context']
    assert next(staff for staff in submitted['staffing'] if staff['id'] == 'staffer')['model'] == 'staffing-fixture'
    assert not store.finish(paused, {'requests': [request]})
    store = OrganizationStore(store.path, seed)
    hire = store.claim_next()
    assert hire['type'] == 'request.hire' and hire['agent_id'] == 'staffer'
    assert hire['team'] == 'red' and hire['priority'] == paused['priority']
    contract = store.context(hire)['requestContract']
    assert contract['parentRequestId'] == paused['id'] and contract['requesterId'] == paused['agent_id']
    assert contract['managementProposal']['members'][0]['team'] == 'red'
    assert store.finish(hire, {})
    assert not store.finish(hire, {})
    store = OrganizationStore(store.path, seed)
    resumed = store.claim_next()
    assert resumed['id'] == paused['id'] and resumed['agent_id'] == paused['agent_id']
    after = store.context(resumed)
    assert after['workPackage'] == before['workPackage']
    assert after['objective']['acceptanceCriteria'] == before['objective']['acceptanceCriteria']
    assert after['requestResponses'][0]['response']['decision'] == 'approved'
    with pytest.raises(ValueError, match='already raised the same request'):
        store.finish(resumed, {'requests': [request]})
    assert store.heartbeat(resumed)
    member = next(row for row in store.configuration_snapshot()['roster'] if row['id'] == 'specialist')
    assert member['team'] == 'red' and member['manager_id'] == 'lead'
    assert member['responsibilities'] == ['Own red finding briefs']
    if stage == 'decompose':
        assert finish(store, resumed, package)
        resumed = store.claim_next()
    plan = {'tasks': [{'title': 'Red brief', 'description': 'Explain the red finding',
        'type': 'work.draft', 'team': 'red', 'agentId': 'specialist', 'managerId': 'lead'}]}
    assert finish(store, resumed, plan)
    work = store.claim_next()
    assert work['type'] == 'work.draft' and work['agent_id'] == 'specialist'
    assert finish(store, work, {'summary': 'Red finding explained', 'deliverable': 'The red finding brief.'})
    assert sent['models'] and set(sent['models']) == {'staffing-fixture'}
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM organization_management_receipts').fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM requests WHERE type='request.hire'").fetchone()[0] == 1
        current = dict(conn.execute('SELECT * FROM objective_control WHERE objective_id=?', (objective['id'],)).fetchone())
        for key in ('max_stages', 'max_replans', 'round'):
            assert current[key] == budget[key]
        usage = conn.execute('SELECT * FROM objective_usage WHERE request_id=?', (paused['id'],)).fetchall()
        assert dict(conn.execute('SELECT * FROM objective_budgets WHERE objective_id=?', (objective['id'],)).fetchone()) == ceilings
        assert len(usage) == 2
        assert sum(row['input_tokens'] or 0 for row in usage) == 22
    # A second objective routes to the same lasting identity without a new hire.
    store.create_objective('Another red brief', idempotency_key='next',
        acceptance_criteria=['Explain the red finding'], manager_id='lead', executive_id='chief', priority='high')
    another = store.claim_next()
    assert another['type'] == 'request.decompose' and store.finish(another, package)
    another = store.claim_next()
    assert another['type'] == 'request.plan' and store.finish(another, plan)
    another = store.claim_next()
    assert another['type'] == 'work.draft' and another['agent_id'] == 'specialist'


@pytest.mark.parametrize('blocked', ['no_authority', 'member_limit', 'cross_team', 'unknown_model'])
def test_unhandled_or_disallowed_hire_remains_visible_and_denial_preserves_plan(tmp_path, blocked):
    store, seed, _, _, paused, request = _setup(tmp_path, 'plan',
        authorized=blocked != 'no_authority', max_members=3 if blocked == 'member_limit' else 16)
    member = request['managementProposal']['members'][0]
    if blocked == 'cross_team':
        member['team'] = 'blue'
    elif blocked == 'unknown_model':
        member['model'] = 'not-configured'
    assert store.finish(paused, {'requests': [request]})
    hire = store.claim_next()
    if blocked != 'no_authority':
        assert hire['type'] == 'request.hire'
        reason = {'member_limit': 'max_members', 'cross_team': 'managed teams',
                  'unknown_model': 'provider and model pair'}[blocked]
        if blocked == 'cross_team':
            assert store.context(hire)['requestContract']['managementProposal']['members'][0]['team'] == 'blue'
        with pytest.raises(ValueError, match=reason):
            store.finish(hire, {})
        assert store.finish(hire, {'intervention': 'The requested member exceeds the approved staffing policy: ' + reason})
    else:
        assert hire is None
    store = OrganizationStore(store.path, seed)
    pending = next(row for row in store.snapshot()['requests'] if row['type'] == 'request.hire')
    assert pending['status'] == 'pending_intervention' and pending['reason']
    assert pending['parentRequestId'] == paused['id']
    assert store.claim_next() is None
    assert store.respond(pending['id'], 'Keep the current roster; revise the package.', 'denied', idempotency_key='deny')
    store = OrganizationStore(store.path, seed)
    resumed = store.claim_next()
    assert resumed['id'] == paused['id']
    context = store.context(resumed)
    assert context['workPackage']['planRequestId'] == paused['id']
    assert context['requestResponses'][0]['response']['decision'] == 'denied'
    assert all(member['id'] != 'specialist' for member in store.configuration_snapshot()['roster'])
    with store._connect() as conn:
        assert not conn.execute('SELECT 1 FROM organization_management_receipts').fetchone()

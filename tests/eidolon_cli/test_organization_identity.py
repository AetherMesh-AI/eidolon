"""Persistent organization identity, assignment and memory contracts using SQLite."""
from tests.organization_package_helpers import claim_after_decomposition
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import sqlite3
import time

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_identity import agent_context_view, agent_identity_view, task_assignment_view
from eidolon_cli.organization_store import OrganizationStore, _SCHEMA


def _memory(store, agent='worker-1'):
    with store._connect() as conn:
        return agent_context_view(conn, agent)


def _identity(store, agent='worker-1'):
    with store._connect() as conn:
        return agent_identity_view(conn, agent)


def _work_claim(store, key, **task_fields):
    obj = store.create_objective('Prepare ' + key, idempotency_key=key)
    plan = claim_after_decomposition(store)
    assert plan['type'] == 'request.plan'
    store.finish(plan, {'tasks': [{'title': key, 'description': 'Use the supplied source material.',
                                 'type': 'work.draft', **task_fields}], 'workers': 2})
    claim = store.claim_next()
    while claim and claim['type'] == 'request.hire':
        assert store.finish(claim, {})
        claim = store.claim_next()
    assert claim['type'] == 'work.draft'
    return obj, claim


def _complete(store):
    while claim := store.claim_next():
        context = store.context(claim)
        evidence = [item['id'] for item in context['evidence']]
        results = {
            'request.hire': {},
            'request.review': {'approved': True, 'summary': 'The scoped source material is covered.', 'evidenceIds': evidence},
            'request.integrate': {'summary': 'Integrated source-grounded outcome.', 'deliverable': 'The complete integrated analysis.'},
            'request.accept': {'approved': True, 'summary': 'Integrated outcome meets all criteria.',
                               'evidenceIds': evidence, 'conflicts': [], 'criteriaResults': [
                                   {'criterion': criterion, 'satisfied': True, 'evidenceIds': evidence,
                                    'reason': 'Covered in the exact reviewed output.'}
                                   for criterion in context['objective']['acceptanceCriteria']]},
        }
        assert store.finish(claim, results[claim['type']])
    assert all(obj['status'] == 'completed' for obj in store.snapshot()['objectives'])


def test_legacy_migration_retains_authors_requests_responsibilities_and_is_idempotent(tmp_path):
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA)
        conn.executemany('INSERT INTO agents VALUES (?,?,?,?,?,?)', [
            ('owner', 'Owner', 'Owner', None, 'general', '[]'),
            ('executive', 'Executive', 'Executive', 'owner', 'general', '["request.accept"]'),
            ('director', 'Director', 'Director', 'executive', 'general', '["request.hire"]'),
            ('manager', 'Manager', 'Manager', 'director', 'general', '["request.plan","request.integrate"]'),
            ('worker-1', 'Historical specialist', 'Employee', 'manager', 'general', '["work.draft"]'),
        ])
        conn.execute('INSERT INTO objectives VALUES (?,?,?,?,?,?,?,0)',
                     ('old-objective', 'old-key', 'old-hash', 'Old objective', 'Retain old work', 3, 100))
        conn.execute("INSERT INTO tasks(id,objective_id,title,description,type,team,priority,status,dependencies,author_id,result) "
                     "VALUES ('old-task','old-objective','Old task','Old work','work.draft','general',3,'completed','[]','worker-1','Old summary')")
        conn.executemany("INSERT INTO requests(id,objective_id,task_id,type,team,priority,status,created,agent_id,payload) "
                         "VALUES (?,'old-objective',?,?,'general',3,'completed',100,?,?)", [
                             ('old-hire', None, 'request.hire', 'director', '{"workers":1}'),
                             ('old-work', 'old-task', 'work.draft', 'worker-1', '{}')])
        conn.execute('INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?)',
                     ('old-evidence', 'old-objective', 'old-task', 'old-work', 'Retained bytes',
                      hashlib.sha256(b'Retained bytes').hexdigest(), 'Retained findings', 101))
    store = OrganizationStore(path)
    ids = {agent['id']: _identity(store, agent['id'])['identityId'] for agent in store.snapshot()['agents']}
    for _ in range(3):
        reopened = OrganizationStore(path)
        with reopened._connect() as conn:
            roles = {row['id']: dict(row) for row in conn.execute('SELECT * FROM agents')}
            assert roles['director']['role'] == roles['manager']['role'] == 'Manager'
            assert roles['manager']['manager_id'] == 'executive'
            assert roles['worker-1']['role'] == 'Worker'
            assert json.loads(roles['director']['accepts']) == ['request.hire']
            assert conn.execute("SELECT agent_id FROM requests WHERE id='old-hire'").fetchone()[0] == 'director'
            assert conn.execute("SELECT payload FROM requests WHERE id='old-hire'").fetchone()[0] == '{"workers":1}'
            assert conn.execute("SELECT author_id FROM tasks WHERE id='old-task'").fetchone()[0] == 'worker-1'
            assert conn.execute("SELECT count(*) FROM agent_history WHERE request_id IN ('old-hire','old-work')").fetchone()[0] == 2
            assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        assert {agent: _identity(reopened, agent)['identityId'] for agent in ids} == ids
        history = _memory(reopened)['recentHistory']
        assert history[0]['requestId'] == 'old-work'
        assert history[0]['summary'] == 'Retained findings'
        assert history[0]['evidenceIds'] == ['old-evidence']
        assert history[0]['createdAt'] == '1970-01-01T00:01:41+00:00'
        assert _memory(reopened, 'director')['recentHistory'][0]['createdAt'] == '1970-01-01T00:01:40+00:00'
        assert _identity(reopened)['responsibilities'] == ['work.draft']


def test_consecutive_objectives_restart_and_other_workers_keep_exact_identity_context(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    first_identity = _identity(store)
    first, claim = _work_claim(store, 'first-goal', agentId='worker-1')
    assert store.context(claim)['agentContext']['memory']['facts'] == []
    assert store.finish(claim, {'summary': 'Compared alternatives against the supplied budget.',
                                'deliverable': 'Option A fits the supplied budget.',
                                'memory': {'facts': ['Budget is defined in the project brief.'],
                                           'lessons': ['Compare lifecycle costs, not only upfront costs.']}})
    _complete(store)
    store = OrganizationStore(store.path)
    assert _identity(store) == first_identity
    second, claim = _work_claim(store, 'second-goal', agentId='worker-1')
    context = store.context(claim)
    assert context['agent']['identityId'] == first_identity['identityId']
    assert context['agentContext']['memory']['facts'] == ['Budget is defined in the project brief.']
    assert context['agentContext']['recentHistory'][0]['objectiveId'] == first['id']
    assert context['agentContext']['recentHistory'][0]['requestId'] != claim['id']
    assert store.finish(claim, {'summary': 'Updated cost comparison.', 'deliverable': 'Full updated comparison.',
                                'memory': {'decisions': ['Separate recurring costs in the comparison.']}})
    _complete(store)
    _, other = _work_claim(store, 'isolated-goal', agentId='worker-2')
    other_context = store.context(other)
    assert other_context['agent']['identityId'] != first_identity['identityId']
    assert all(not values for values in other_context['agentContext']['memory'].values())
    assert other_context['agentContext']['recentHistory'] == []
    assert 'Budget is defined' not in json.dumps(other_context)
    worker_memory = _memory(store)
    assert worker_memory['revision'] == 2
    assert [item['objectiveId'] for item in worker_memory['recentHistory']] == [second['id'], first['id']]
    assert worker_memory['memory']['facts'] and worker_memory['memory']['decisions']


@pytest.mark.parametrize('invalidation', ['cancel', 'expire', 'changed-policy'])
def test_stale_claim_cannot_mutate_memory_or_history(tmp_path, invalidation):
    store = OrganizationStore(tmp_path / 'state.db')
    objective, claim = _work_claim(store, 'stale')
    before = _memory(store)
    if invalidation == 'cancel':
        store.cancel(objective['id'])
    elif invalidation == 'expire':
        with store._write() as conn:
            conn.execute('UPDATE requests SET lease=? WHERE id=?', (time.time() - 1, claim['id']))
    else:
        OrganizationStore(store.path, replace(store.settings, capabilities=('work.analyze',)))
    assert store.finish(claim, {'summary': 'Stale', 'deliverable': 'Must not persist',
                                'memory': {'facts': ['Poisoned stale context']}}) is False
    assert _memory(store) == before
    with store._connect() as conn:
        assert conn.execute('SELECT 1 FROM evidence WHERE request_id=?', (claim['id'],)).fetchone() is None


@pytest.mark.parametrize('memory', [None, [], {'transcript': ['raw messages']}, {'facts': 'not a list'},
                                   {'facts': ['']}, {'facts': ['x' * 1001]}, {'facts': ['item'] * 25}])
def test_invalid_memory_rolls_back_work_evidence_usage_and_review_queue(tmp_path, memory):
    store = OrganizationStore(tmp_path / 'state.db')
    _, claim = _work_claim(store, 'invalid-memory')
    before = _memory(store)
    with pytest.raises(ValueError, match='Agent memory'):
        store.finish(claim, {'summary': 'Otherwise valid', 'deliverable': 'Complete output', 'memory': memory})
    assert _memory(store) == before
    with store._connect() as conn:
        assert conn.execute('SELECT 1 FROM evidence WHERE request_id=?', (claim['id'],)).fetchone() is None
        assert conn.execute('SELECT status FROM requests WHERE id=?', (claim['id'],)).fetchone()[0] == 'running'
        assert conn.execute('SELECT completed FROM objective_usage WHERE request_id=?', (claim['id'],)).fetchone()[0] == 0
        assert conn.execute("SELECT 1 FROM requests WHERE task_id=? AND type='request.review'", (claim['task_id'],)).fetchone() is None


def test_invalid_result_and_intervention_do_not_save_proposed_memory(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    _, claim = _work_claim(store, 'bad-result')
    with pytest.raises(ValueError, match='Deliverable'):
        store.finish(claim, {'summary': 'Incomplete', 'memory': {'facts': ['Cannot retain']}})
    assert _memory(store)['revision'] == 0
    assert store.finish(claim, {'intervention': 'Need more source data.', 'memory': {'facts': ['Cannot retain']}})
    assert _memory(store)['revision'] == 0
    assert _memory(store)['recentHistory'] == []


def test_competing_finishes_commit_one_history_and_memory_revision(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    _, claim = _work_claim(store, 'concurrent')
    reopened = [OrganizationStore(store.path) for _ in range(4)]
    def finish(index):
        return reopened[index].finish(claim, {'summary': f'Output {index}', 'deliverable': 'Full retained text',
                                              'memory': {'facts': [f'Completed result {index}']}})
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(finish, range(4)))
    assert outcomes.count(True) == 1
    context = _memory(store)
    assert context['revision'] == 1 and len(context['recentHistory']) == 1
    assert len(context['memory']['facts']) == 1
    assert context['memory']['facts'] == [f'Completed result {outcomes.index(True)}']


def _domain_settings():
    return OrganizationSettings.from_config({'organization': {'roster': [
        {'id': 'engineering', 'name': 'Engineering manager', 'role': 'Manager', 'team': 'engineering'},
        {'id': 'research', 'name': 'Research manager', 'role': 'Manager', 'team': 'research'},
        {'id': 'engineer', 'name': 'Engineer', 'team': 'engineering', 'manager_id': 'engineering',
         'capabilities': ['work.draft'], 'responsibilities': ['Develop source-grounded proposals.'],
         'purpose': 'Turn engineering requirements into concrete proposals.'},
        {'id': 'researcher', 'name': 'Researcher', 'team': 'research', 'manager_id': 'research',
         'capabilities': ['work.draft']},
    ]}})


def test_cross_manager_assignment_preserves_authority_and_exact_worker(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db', _domain_settings())
    store.create_objective('Coordinate domains', idempotency_key='domains', manager_id='engineering')
    planner = claim_after_decomposition(store)
    assert planner['agent_id'] == 'engineering'
    assert store.finish(planner, {'tasks': [
        {'title': 'Research inputs', 'description': 'Develop research inputs.', 'type': 'work.draft',
         'team': 'research', 'managerId': 'research', 'agentId': 'researcher'}], 'workers': 1})
    hire = store.claim_next()
    assert hire['type'] == 'request.hire'
    assert store.finish(hire, {})
    work = store.claim_next()
    assert work['agent_id'] == 'researcher'
    context = store.context(work)
    assert context['agent']['managerId'] == 'research'
    with store._connect() as conn:
        assignment = task_assignment_view(conn, work['task_id'])
    assert assignment == {'agentId': 'researcher', 'managingAgentId': 'research', 'assignedById': 'engineering'}
    assert context['toolPolicy']['tools'] == []


@pytest.mark.parametrize('assignment', [
    {'agentId': 'missing'}, {'agentId': 'research'},
    {'agentId': 'engineer', 'managerId': 'research'},
    {'agentId': 'researcher'}, {'managerId': 'owner'},
])
def test_invalid_assignments_roll_back_entire_plan(tmp_path, assignment):
    store = OrganizationStore(tmp_path / 'state.db', _domain_settings())
    store.create_objective('Scoped work', idempotency_key='invalid-assignment')
    claim = claim_after_decomposition(store)
    with pytest.raises(ValueError, match='Task'):
        store.finish(claim, {'tasks': [{'title': 'Plan', 'description': 'Engineering work',
                                       'type': 'work.draft', 'team': 'engineering', **assignment}]})
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM tasks').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM task_assignments').fetchone()[0] == 0
    assert _memory(store, claim['agent_id'])['revision'] == 0


def test_retired_agent_keeps_identity_context_and_last_responsibilities(tmp_path):
    settings = _domain_settings()
    store = OrganizationStore(tmp_path / 'state.db', settings)
    original = _identity(store, 'engineer')
    reopened = OrganizationStore(store.path, replace(settings, roster=()))
    assert _identity(reopened, 'engineer') == original
    restored = OrganizationStore(store.path, settings)
    assert _identity(restored, 'engineer') == original
    assert _memory(restored, 'engineer')['revision'] == 0


def test_unpinned_scope_prefers_planners_existing_reports_over_new_same_team_manager(tmp_path):
    settings = OrganizationSettings.from_config({'organization': {'roster': [
        {'id': 'new-manager', 'name': 'New manager', 'role': 'Manager', 'team': 'general'},
        {'id': 'existing-worker', 'name': 'Existing worker', 'capabilities': ['work.draft']},
        {'id': 'new-worker', 'name': 'New worker', 'manager_id': 'new-manager', 'capabilities': ['work.draft']},
    ]}})
    store = OrganizationStore(tmp_path / 'state.db', settings)
    store.create_objective('Keep appropriate scope', idempotency_key='scope')
    planner = claim_after_decomposition(store)
    assert planner['agent_id'] == 'manager'
    store.finish(planner, {'tasks': [{'title': 'Draft', 'description': 'Work with the established team.', 'type': 'work.draft'}]})
    hire = store.claim_next()
    assert hire['type'] == 'request.hire'
    store.finish(hire, {})
    worker = store.claim_next()
    assert worker['agent_id'] == 'existing-worker'
    assert store.context(worker)['task']['managingAgentId'] == 'manager'


@pytest.mark.parametrize('assignment', [{'manager_id': 'director'}, {'manager_id': 'reviewer'},
                                      {'executive_id': 'manager'}, {'manager_id': 'missing'}])
def test_objective_rejects_wrong_or_incomplete_leader_roles_atomically(tmp_path, assignment):
    store = OrganizationStore(tmp_path / 'state.db')
    with pytest.raises(ValueError, match='Objective'):
        store.create_objective('Invalid ownership', idempotency_key='invalid-owner', **assignment)
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM objectives').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM objective_assignments').fetchone()[0] == 0


def test_memory_working_set_is_bounded_without_losing_prior_stage_history(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    _, first = _work_claim(store, 'memory-first')
    facts = [f'{index}: ' + ('x' * 995) for index in range(8)]
    store.finish(first, {'summary': 'First bounded memory', 'deliverable': 'Complete first result',
                         'memory': {'facts': facts}})
    _complete(store)
    _, second = _work_claim(store, 'memory-second')
    store.finish(second, {'summary': 'Second bounded memory', 'deliverable': 'Complete second result',
                          'memory': {'facts': [facts[-1], 'New concise observation.']}})
    context = _memory(store)
    assert sum(map(len, context['memory']['facts'])) <= 4000
    assert context['memory']['facts'][-2:] == [facts[-1], 'New concise observation.']
    assert len(context['memory']['facts']) == len(set(context['memory']['facts']))
    assert {item['requestId'] for item in context['recentHistory']} == {first['id'], second['id']}

"""Persistent organization identity across migration, restart and retirement."""
import hashlib
import json
import sqlite3
from dataclasses import replace

from eidolon_cli.organization_store import _SCHEMA, OrganizationStore
from tests.organization_identity_helpers import (
    _complete,
    _domain_settings,
    _identity,
    _memory,
    _work_claim,
)


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


def test_retired_agent_keeps_identity_context_and_last_responsibilities(tmp_path):
    settings = _domain_settings()
    store = OrganizationStore(tmp_path / 'state.db', settings)
    original = _identity(store, 'engineer')
    reopened = OrganizationStore(store.path, replace(settings, roster=()))
    assert _identity(reopened, 'engineer') == original
    restored = OrganizationStore(store.path, settings)
    assert _identity(restored, 'engineer') == original
    assert _memory(restored, 'engineer')['revision'] == 0


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

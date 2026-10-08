"""Real SQLite archive boundaries, repeat/restart safety and retained references."""
from tests.organization_package_helpers import claim_after_decomposition
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from eidolon_cli.organization_store import OrganizationStore


@pytest.fixture
def store(tmp_path):
    return OrganizationStore(tmp_path / 'organization.db')


def create(store, key='objective'):
    return store.create_objective('Decision brief ' + key, idempotency_key=key)


def archive(store, objective, key='archive', revision=0):
    return store.set_objective_archived(objective['id'], True, expected_revision=revision, idempotency_key=key)


def test_reversible_archive_retains_identity_requests_audit_and_duplicate_receipts(store):
    obj = create(store)
    claim = claim_after_decomposition(store)
    store.cancel(obj['id'])
    before = store.history_objective(obj['id'])
    archived = archive(store, obj)
    assert archived['archived'] and archived['revision'] == 1
    assert store.finish(claim, {'tasks': []}) is False
    restarted = OrganizationStore(store.path)
    assert archive(restarted, obj) == archived
    restored = restarted.set_objective_archived(obj['id'], False, expected_revision=1, idempotency_key='restore')
    assert restored['revision'] == 2
    # Delayed duplicate archive cannot undo the newer restore.
    assert archive(restarted, obj) == archived
    assert restarted.history(state='current')['items'][0]['history']['revision'] == 2
    assert restarted.history(state='archived')['items'] == []
    after = restarted.history_objective(obj['id'])
    assert after['requests'] == before['requests']
    assert after['tasks'] == before['tasks']
    assert [a['id'] for a in after['agents']] == [a['id'] for a in before['agents']]
    assert restarted.claim_next() is None
    assert create(restarted)['id'] == obj['id']
    with pytest.raises(ValueError, match='changed since'):
        archive(restarted, obj, 'late-new-key')
    with pytest.raises(ValueError, match='different input'):
        restarted.set_objective_archived(obj['id'], False, expected_revision=1, idempotency_key='archive')
    with restarted._connect() as conn:
        assert conn.execute('SELECT count(*) FROM history_transitions').fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute('DELETE FROM history_transitions')


@pytest.mark.parametrize('phase', ['planning', 'running', 'pending_intervention', 'reviewing'])
def test_unsettled_objective_never_archives(store, phase):
    obj = create(store)
    if phase != 'planning':
        claim = claim_after_decomposition(store)
        if phase == 'pending_intervention':
            store.fail(claim, 'Needs owner input')
        elif phase == 'reviewing':
            store.finish(claim, {'tasks': [{'title': 'Write', 'description': 'Write brief', 'type': 'work.draft',
                                          'team': 'general', 'dependsOn': []}]})
            work = store.claim_next()
            store.finish(work, {'summary': 'Drafted', 'deliverable': 'A real draft for independent review.'})
    with pytest.raises(ValueError, match='Only completed or cancelled'):
        archive(store, obj)
    assert store.history(state='archived')['items'] == []


def test_archive_preserves_cancelled_dag_and_blocks_live_external_dependencies(store):
    obj = create(store)
    claim = claim_after_decomposition(store)
    store.finish(claim, {'tasks': [
        {'title': 'First', 'description': 'First brief', 'type': 'work.draft', 'team': 'general', 'dependsOn': []},
        {'title': 'Second', 'description': 'Second brief', 'type': 'work.draft', 'team': 'general', 'dependsOn': [0]},
    ]})
    store.cancel(obj['id'])
    tasks = store.history_objective(obj['id'])['tasks']
    other = create(store, 'other')
    other_claim = claim_after_decomposition(store)
    store.finish(other_claim, {'tasks': [{'title': 'Other', 'description': 'Other brief', 'type': 'work.draft',
                                        'team': 'general', 'dependsOn': []}]})
    with store._write() as conn:
        conn.execute('UPDATE tasks SET dependencies=? WHERE objective_id=?', (json.dumps([tasks[0]['id']]), other['id']))
    with pytest.raises(ValueError, match='depending on'):
        archive(store, obj)
    store.cancel(other['id'])
    archive(store, obj)
    assert store.history_objective(obj['id'])['tasks'] == tasks
    with store._connect() as conn:
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('pending', ['request', 'tool', 'project', 'child'])
def test_cancelled_does_not_hide_unresolved_work_or_execution(store, pending):
    obj = create(store)
    claim = store.claim_next()
    store.cancel(obj['id'])
    with store._write() as conn:
        if pending == 'request':
            conn.execute("UPDATE requests SET status='pending_intervention' WHERE id=?", (claim['id'],))
        elif pending == 'tool':
            conn.execute("INSERT INTO tool_receipts VALUES ('receipt',?,1,'call','read_file','{}','unknown',NULL,NULL,1,NULL,'unconfirmed')", (claim['id'],))
        elif pending == 'project':
            conn.execute("INSERT INTO project_run_starts VALUES ('run',?,'token',?,0,'[]','sha','{}',NULL,1,1)", (claim['id'], obj['id']))
    if pending == 'child':
        other = create(store, 'other')
        with store._write() as conn:
            conn.execute('UPDATE request_contracts SET parent_request_id=? WHERE request_id IN '
                         '(SELECT id FROM requests WHERE objective_id=?)', (claim['id'], other['id']))
    with pytest.raises(ValueError, match='unresolved|unconfirmed|child'):
        archive(store, obj)
    assert store.history(state='current')['items']


def test_history_search_pagination_limits_and_profile_isolation(store, tmp_path, monkeypatch):
    ids = []
    for key in ['Alpha%', 'Beta', 'Gamma']:
        obj = create(store, key)
        store.cancel(obj['id'])
        archive(store, obj, key)
        ids.append(obj['id'])
    first = store.history(limit=2)
    second = store.history(limit=2, before=first['nextCursor'])
    assert [row['id'] for row in first['items'] + second['items']] == list(reversed(ids))
    assert second['nextCursor'] is None
    assert [row['id'] for row in store.history(query='%')['items']] == [ids[0]]
    assert store.history(query='alpha')['items'][0]['id'] == ids[0]
    other = OrganizationStore(tmp_path / 'other.db')
    with pytest.raises(ValueError, match='this profile'):
        other.history_objective(ids[0])
    with pytest.raises(ValueError, match='this profile'):
        other.set_objective_archived(ids[0], False, expected_revision=1, idempotency_key='steal')
    with pytest.raises(ValueError, match='this profile'):
        other.history(before=ids[0])
    for limit in [0, 51, True, '2']:
        with pytest.raises(ValueError):
            store.history(limit=limit)
    monkeypatch.setattr('eidolon_cli.organization_history.MAX_ARCHIVED_OBJECTIVES', 3)
    full = create(store, 'full')
    store.cancel(full['id'])
    with pytest.raises(ValueError, match='Archive capacity'):
        archive(store, full, 'full')
    monkeypatch.setattr('eidolon_cli.organization_history.MAX_CURRENT_OBJECTIVES', 1)
    with pytest.raises(ValueError, match='Current-objective capacity'):
        store.set_objective_archived(ids[0], False, expected_revision=1, idempotency_key='full-restore')
    assert store.history()['counts']['archived'] == 3


def test_concurrent_archive_has_one_receipt_and_transition_budget_is_durable(store, monkeypatch):
    obj = create(store)
    store.cancel(obj['id'])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: archive(OrganizationStore(store.path), obj), range(4)))
    assert all(result == results[0] for result in results)
    monkeypatch.setattr('eidolon_cli.organization_history.MAX_HISTORY_TRANSITIONS', 1)
    with pytest.raises(ValueError, match='audit limit'):
        store.set_objective_archived(obj['id'], False, expected_revision=1, idempotency_key='exhausted')
    assert archive(store, obj) == results[0]
    assert not store.history()['items'][0]['history']['canRestore']


def test_accepted_history_keeps_reviewed_bytes_dependencies_and_agent_context(store):
    obj = create(store)
    claim = claim_after_decomposition(store)
    store.finish(claim, {'tasks': [{'title': 'Draft', 'description': 'Prepare a brief', 'type': 'work.draft',
                                  'team': 'general', 'dependsOn': []}]})
    worker = store.claim_next()
    store.finish(worker, {'summary': 'Prepared brief', 'deliverable': 'The exact retained and reviewed brief.'})
    reviewer = store.claim_next()
    evidence = store.context(reviewer)['evidence']
    store.finish(reviewer, {'approved': True, 'summary': 'Reviewed exact brief', 'evidenceIds': [item['id'] for item in evidence]})
    integrator = store.claim_next()
    store.finish(integrator, {'summary': 'Integrated', 'deliverable': 'The complete integrated retained brief.'})
    accept = store.claim_next()
    context = store.context(accept)
    ids = [item['id'] for item in context['evidence']]
    store.finish(accept, {'approved': True, 'summary': 'The objective is satisfied.', 'evidenceIds': ids,
        'conflicts': [], 'criteriaResults': [{'criterion': criterion, 'satisfied': True, 'evidenceIds': ids,
        'reason': 'Exact content covers the criterion.'} for criterion in context['objective']['acceptanceCriteria']]})
    before = store.history_objective(obj['id'])
    assert before['objectives'][0]['status'] == 'completed'
    archive(store, obj)
    restarted = OrganizationStore(store.path)
    after = restarted.history_objective(obj['id'])
    assert after['objectives'][0]['result'] == before['objectives'][0]['result']
    assert after['tasks'] == before['tasks']
    assert after['agents'] == before['agents']
    assert restarted.evidence(evidence[0]['id'])['content'] == evidence[0]['content']
    assert restarted.evidence(evidence[0]['id'])['sha256'] == evidence[0]['sha256']
    assert restarted.finish(worker, {'summary': 'Late result', 'deliverable': 'Replace original'}) is False
    assert restarted.claim_next() is None


def test_archiving_releases_only_current_capacity_and_exact_old_ids_still_resolve(store, monkeypatch):
    monkeypatch.setattr('eidolon_cli.organization_history.MAX_CURRENT_OBJECTIVES', 2)
    first = create(store, 'first')
    store.cancel(first['id'])
    second = create(store, 'second')
    store.cancel(second['id'])
    with pytest.raises(ValueError, match='Current-objective capacity'):
        create(store, 'third')
    archive(store, first)
    third = create(store, 'third')
    assert {row['id'] for row in store.snapshot()['objectives']} == {second['id'], third['id']}
    assert store.history()['counts']['current'] == 2
    assert store.history()['counts']['archived'] == 1
    assert create(store, 'first')['id'] == first['id']
    assert create(store, 'first')['history']['archived']
    assert store.history_objective(first['id'])['objectives'][0]['status'] == 'cancelled'


def test_unconfirmed_cancelled_execution_stays_visible_beyond_settled_window(store):
    obj = create(store, 'unconfirmed')
    claim = store.claim_next()
    store.cancel(obj['id'])
    with store._write() as conn:
        conn.execute("INSERT INTO tool_receipts VALUES ('unknown',?,1,'call','read_file','{}','unknown',NULL,NULL,1,NULL,'unconfirmed')", (claim['id'],))
    for index in range(28):
        newer = create(store, str(index))
        store.cancel(newer['id'])
    snapshot = store.snapshot()
    retained = next(row for row in snapshot['objectives'] if row['id'] == obj['id'])
    assert not retained['history']['canArchive']
    assert 'unconfirmed' in retained['history']['blocker']
    assert len(snapshot['objectives']) == 26
    assert snapshot['runtime']['historyLimited']
    assert len(store.history(state='current', limit=50)['items']) == 29


def test_live_prerequisite_stays_visible_beyond_recent_settled_history(store):
    prerequisite = create(store, 'prerequisite')
    store.cancel(prerequisite['id'])
    for index in range(28):
        settled = create(store, 'settled-' + str(index))
        store.cancel(settled['id'])
    dependent = create(store, 'dependent')
    with store._write() as conn:
        parent = conn.execute('SELECT id FROM requests WHERE objective_id=?', (prerequisite['id'],)).fetchone()[0]
        conn.execute('UPDATE request_contracts SET parent_request_id=? WHERE request_id IN '
                     '(SELECT id FROM requests WHERE objective_id=?)', (parent, dependent['id']))
    visible = next(row for row in store.snapshot()['objectives'] if row['id'] == prerequisite['id'])
    assert not visible['history']['canArchive']
    assert 'child request' in visible['history']['blocker']


def test_archive_eligibility_query_work_does_not_grow_with_retained_task_history(store):
    """SQLite VM work bounds detect accidental archive scans without timing races."""
    from eidolon_cli.organization_history import (
        MAX_CURRENT_OBJECTIVES, MAX_ARCHIVED_OBJECTIVES, archive_blocker, live_history_references,
    )
    base = create(store)
    claim = claim_after_decomposition(store)
    store.finish(claim, {'tasks': [{'title': 'Task', 'description': 'Task detail', 'type': 'work.draft',
                                  'team': 'general', 'dependsOn': []}]})
    store.cancel(base['id'])
    with store._write() as conn:
        objective = list(conn.execute('SELECT * FROM objectives LIMIT 1').fetchone())
        control = list(conn.execute('SELECT * FROM objective_control LIMIT 1').fetchone())
        task = list(conn.execute('SELECT * FROM tasks LIMIT 1').fetchone())

        def insert_population(count, prefix, archived):
            objectives, controls, tasks, archives = [], [], [], []
            for index in range(count):
                ident = f'{prefix}-{index}'
                row = objective[:]
                row[0] = row[1] = ident
                objectives.append(row)
                row = control[:]
                row[0] = ident
                controls.append(row)
                for task_index in range(store.settings.max_tasks):
                    row = task[:]
                    row[0], row[1] = f'{ident}-{task_index}', ident
                    tasks.append(row)
                if archived:
                    archives.append((ident, 1, 1, 1))
            for table, template, rows in [('objectives', objective, objectives), ('objective_control', control, controls),
                                           ('tasks', task, tasks)]:
                conn.executemany(f'INSERT INTO {table} VALUES (' + ','.join('?' for _ in template) + ')', rows)
            conn.executemany('INSERT INTO objective_history VALUES (?,?,?,?)', archives)

        insert_population(MAX_CURRENT_OBJECTIVES - 1, 'current', False)
        current = [base['id']] + [f'current-{index}' for index in range(MAX_CURRENT_OBJECTIVES - 1)]
        # A realistic live DAG must not be rescanned per settled objective either.
        for index in range(100):
            ident = f'current-{index}'
            conn.execute('UPDATE objectives SET cancelled=0 WHERE id=?', (ident,))
            conn.execute("UPDATE objective_control SET status='pending' WHERE objective_id=?", (ident,))
            conn.execute("UPDATE tasks SET status='queued',dependencies=? WHERE objective_id=?",
                         (json.dumps([f'{ident}-0']), ident))
        settled = [ident for ident in current if ident not in {f'current-{index}' for index in range(100)}]

        def query_work(maximum=None):
            ticks = 0

            def progress():
                nonlocal ticks
                ticks += 1
                return int(maximum is not None and ticks > maximum)

            conn.set_progress_handler(progress, 1000)
            try:
                references = live_history_references(conn)
                for ident in settled:
                    assert archive_blocker(conn, ident, live_references=references) is None
            finally:
                conn.set_progress_handler(None, 0)
            return ticks

        baseline = query_work((MAX_CURRENT_OBJECTIVES + 100 * store.settings.max_tasks) // 2)
        insert_population(MAX_ARCHIVED_OBJECTIVES, 'archive', True)
        # Index depth may increase; eligibility must not scan retained task rows.
        try:
            after = query_work(baseline * 3 + 100)
        except sqlite3.OperationalError as error:
            pytest.fail(f'Eligibility work grew with archived history: {error}')
        assert after <= baseline * 3 + 100

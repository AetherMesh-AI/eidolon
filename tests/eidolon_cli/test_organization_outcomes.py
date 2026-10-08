"""Real ledger outcome lifecycle, offline writers and durable owner acknowledgements."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading

import pytest

from eidolon_cli.organization_store import OrganizationStore


@pytest.fixture
def store(tmp_path):
    return OrganizationStore(tmp_path / 'organization' / 'state.db')


def accepted(store, key='accepted'):
    objective = store.create_objective('A supported decision', idempotency_key=key, delivery_mode='managed_artifact')
    claim = store.claim_next()
    assert claim['type'] == 'request.plan'
    store.finish(claim, {'tasks': [{'title': 'Analyze', 'description': 'Compare options', 'type': 'work.analyze'}]})
    claim = store.claim_next()
    store.finish(claim, {'summary': 'Compared options', 'deliverable': 'Option A has the strongest support.'})
    claim = store.claim_next()
    ids = [item['id'] for item in store.context(claim)['evidence']]
    store.finish(claim, {'approved': True, 'summary': 'Supported task result', 'evidenceIds': ids})
    assert store.outcomes()['items'] == []
    claim = store.claim_next()
    assert claim['type'] == 'request.integrate'
    store.finish(claim, {'summary': 'Combined decision', 'deliverable': 'Choose A based on the supplied evidence.'})
    claim = store.claim_next()
    context = store.context(claim)
    ids = [item['id'] for item in context['evidence']]
    result = {'approved': True, 'summary': 'Complete objective accepted', 'evidenceIds': ids, 'conflicts': [],
              'criteriaResults': [{'criterion': criterion, 'satisfied': True, 'reason': 'Supported by exact evidence',
                                   'evidenceIds': ids} for criterion in context['objective']['acceptanceCriteria']]}
    store.finish(claim, result)
    return objective, claim, ids


def cancelled(store, key='cancelled'):
    objective = store.create_objective('Cancelled work', idempotency_key=key)
    store.cancel(objective['id'])
    return objective


def item(store):
    return store.outcomes()['items'][0]


def test_accepted_receipt_references_exact_retained_evidence_and_seen_is_only_metadata(store):
    objective, claim, ids = accepted(store)
    initial = store.snapshot()
    observed = item(store)
    assert initial['outcomes'] == store.outcomes()
    assert observed['status'] == 'accepted' and observed['objectiveId'] == objective['id']
    assert observed['acceptanceRequestId'] == claim['id']
    assert observed['evidenceIds'] == ids
    assert observed['deliverableId'] in ids
    assert store.evidence(observed['deliverableId'])['content'].startswith('Choose A')
    assert not observed['seen'] and observed['revision'] > 0
    store.mark_outcome_seen(objective['id'], observed['revision'])
    seen = store.snapshot()
    assert {k: v for k, v in initial.items() if k != 'outcomes'} == {k: v for k, v in seen.items() if k != 'outcomes'}
    assert seen['outcomes']['unread'] == 0 and seen['outcomes']['total'] == 1
    assert seen['outcomes']['generation'] != initial['outcomes']['generation']
    store.mark_outcome_seen(objective['id'], observed['revision'])
    assert OrganizationStore(store.path).snapshot() == seen
    assert store.claim_next() is None
    assert item(store) == {**observed, 'seen': True}


def test_cancel_archive_restore_preserves_receipt_generation_and_exact_detail(store):
    objective = cancelled(store)
    observed = item(store)
    assert observed['status'] == 'cancelled'
    assert observed['acceptanceRequestId'] is None and observed['deliverableId'] is None
    assert observed['evidenceIds'] == []
    store.mark_outcome_seen(objective['id'], observed['revision'])
    generation = store.outcomes()['generation']
    store.set_objective_archived(objective['id'], True, expected_revision=0, idempotency_key='archive')
    archived = item(store)
    assert archived == {**observed, 'seen': True, 'archived': True}
    assert store.outcomes()['generation'] != generation
    assert store.snapshot()['objectives'] == []
    exact = store.history_objective(objective['id'])
    assert exact['outcomes']['items'] == [archived] and exact['objectives'][0]['status'] == 'cancelled'
    restarted = OrganizationStore(store.path)
    assert item(restarted) == archived
    restarted.set_objective_archived(objective['id'], False, expected_revision=1, idempotency_key='restore')
    assert item(restarted) == {**observed, 'seen': True}
    assert restarted.claim_next() is None


def test_failure_and_intervention_never_become_success_and_seen_does_not_hide_blockers(store):
    objective = store.create_objective('Missing facts', idempotency_key='blocked')
    claim = store.claim_next()
    store.fail(claim, 'Need owner input')
    assert store.outcomes()['total'] == 0 and store.attention()['unread'] == 1
    # Even inconsistent terminal controls cannot mask retained unfinished work.
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE objective_control SET status='accepted' WHERE objective_id=?", (objective['id'],))
    assert store.outcomes()['total'] == 0 and store.attention()['unread'] == 1
    with pytest.raises(ValueError, match='refresh'):
        store.mark_outcome_seen(objective['id'], 1)
    store.cancel(objective['id'])
    assert item(store)['status'] == 'cancelled'


def test_offline_reopen_same_terminal_state_invalidates_old_ack(store):
    objective, _, _ = accepted(store)
    old = item(store)
    store.mark_outcome_seen(objective['id'], old['revision'])
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE objective_control SET status='blocked' WHERE objective_id=?", (objective['id'],))
    assert store.outcomes()['items'] == []
    with pytest.raises(ValueError, match='refresh'):
        store.mark_outcome_seen(objective['id'], old['revision'])
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE objective_control SET status='accepted' WHERE objective_id=?", (objective['id'],))
    current = item(OrganizationStore(store.path))
    assert current['revision'] > old['revision'] and not current['seen']
    assert current['createdAt'] == old['createdAt']
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM owner_outcomes').fetchone()[0] == 1
    with pytest.raises(ValueError, match='refresh'):
        store.mark_outcome_seen(objective['id'], old['revision'])


def test_material_changes_advance_generation_but_noop_and_bookkeeping_do_not(store):
    objective, claim, ids = accepted(store)
    old = item(store)
    store.mark_outcome_seen(objective['id'], old['revision'])
    seen = store.outcomes()
    with sqlite3.connect(store.path) as conn:
        conn.execute('UPDATE objective_control SET status=status,summary=summary WHERE objective_id=?', (objective['id'],))
        conn.execute('UPDATE requests SET lease=123,available=4 WHERE id=?', (claim['id'],))
    assert store.outcomes() == seen
    for statement, parameters in [
        ('UPDATE objective_control SET summary=? WHERE objective_id=?', ('Updated decision summary', objective['id'])),
        ('UPDATE objectives SET title=? WHERE id=?', ('Updated title', objective['id'])),
        ('UPDATE objective_deliverables SET content=? WHERE id=?', ('Changed persisted bytes', old['deliverableId'])),
        ('UPDATE evidence SET summary=? WHERE id=?', ('Changed source evidence', ids[0])),
        ('UPDATE objective_acceptances SET summary=? WHERE request_id=?', ('Updated review rationale', claim['id'])),
    ]:
        before = item(store)
        store.mark_outcome_seen(objective['id'], before['revision'])
        with sqlite3.connect(store.path) as conn:
            conn.execute(statement, parameters)
        after = item(store)
        assert after['revision'] > before['revision'] and not after['seen']
        with pytest.raises(ValueError, match='refresh'):
            store.mark_outcome_seen(objective['id'], before['revision'])
    # Actual cancellation overrides a previously accepted control, never inferred
    # from a failed request or from a control string that merely says cancelled.
    with sqlite3.connect(store.path) as conn:
        conn.execute('UPDATE objectives SET cancelled=1 WHERE id=?', (objective['id'],))
    assert item(store)['status'] == 'cancelled'


def test_migration_backfills_accepted_cancelled_and_legacy_once(store):
    accepted_objective, _, _ = accepted(store)
    cancelled_objective = cancelled(store)
    legacy = store.create_objective('Old reviewed result', idempotency_key='legacy')
    with store._write() as conn:
        conn.execute("UPDATE requests SET status='completed' WHERE objective_id=?", (legacy['id'],))
        conn.execute("UPDATE objective_control SET status='legacy_completed' WHERE objective_id=?", (legacy['id'],))
    original = store.snapshot()
    with sqlite3.connect(store.path) as conn:
        names = conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'outcome_%'").fetchall()
        for (name,) in names:
            conn.execute('DROP TRIGGER ' + name)
        conn.execute('DROP TABLE owner_outcomes')
        conn.execute('DROP TABLE owner_outcome_generation')
    migrated = OrganizationStore(store.path)
    rows = {row['objectiveId']: row for row in migrated.outcomes()['items']}
    assert rows[accepted_objective['id']]['status'] == 'accepted'
    assert rows[cancelled_objective['id']]['status'] == 'cancelled'
    assert rows[legacy['id']]['status'] == 'legacy_completed'
    assert rows[legacy['id']]['acceptanceRequestId'] is None and rows[legacy['id']]['evidenceIds'] == []
    assert migrated.snapshot()['requests'] == original['requests']
    for row in rows.values():
        migrated.mark_outcome_seen(row['objectiveId'], row['revision'])
    assert OrganizationStore(store.path).outcomes() == migrated.outcomes()
    assert migrated.outcomes()['unread'] == 0


def test_bounded_pages_counts_unread_and_offpage_generation(store):
    identifiers = [cancelled(store, str(index))['id'] for index in range(105)]
    first = store.snapshot()['outcomes']
    assert len(first['items']) == 100 and first['total'] == first['unread'] == 105
    tail = store.outcomes(before=first['nextCursor'])
    assert [row['objectiveId'] for row in first['items'] + tail['items']] == identifiers[::-1]
    assert tail['generation'] == first['generation']
    assert first['hasMore'] and not tail['hasMore'] and tail['nextCursor'] is None
    for row in first['items'] + tail['items']:
        store.mark_outcome_seen(row['objectiveId'], row['revision'])
    before = store.outcomes()
    with sqlite3.connect(store.path) as conn:
        conn.execute('UPDATE objectives SET title=? WHERE id=?', ('Older changed result', identifiers[0]))
    after = store.outcomes()
    assert after['items'] == before['items'] and after['total'] == before['total']
    assert after['generation'] != before['generation']
    unread = store.outcomes(unread_only=True, limit=1)
    assert unread['unread'] == 1 and unread['total'] == 105 and not unread['hasMore']
    assert unread['items'][0]['objectiveId'] == identifiers[0]
    assert store.history_objective(identifiers[0])['outcomes']['total'] == 1
    # A cursor still works after its outcome is reopened or archived.
    with sqlite3.connect(store.path) as conn:
        conn.execute('UPDATE objectives SET cancelled=0 WHERE id=?', (first['nextCursor'],))
    assert [r['objectiveId'] for r in store.outcomes(before=first['nextCursor'])['items']] == identifiers[-101::-1]


def test_concurrent_old_seen_cannot_hide_new_result_even_with_identical_clock(store):
    objective = cancelled(store)
    old = item(store)
    barrier = threading.Barrier(2)
    def acknowledge():
        barrier.wait()
        try:
            store.mark_outcome_seen(objective['id'], old['revision'])
        except ValueError as exc:
            assert 'refresh' in str(exc)
    def change():
        barrier.wait()
        with store._write() as conn:
            conn.execute('UPDATE objectives SET title=? WHERE id=?', ('New title', objective['id']))
            conn.execute('UPDATE owner_outcomes SET updated=created WHERE objective_id=?', (objective['id'],))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(acknowledge), pool.submit(change)]
        for future in futures:
            future.result()
    now = item(OrganizationStore(store.path))
    assert now['revision'] > old['revision'] and not now['seen']
    assert now['updatedAt'] == old['createdAt']


@pytest.mark.parametrize('revision', [None, True, False, 0, -1, 1.0, '1', 2**53, [], {}])
def test_invalid_revisions_are_rejected(store, revision):
    objective = cancelled(store)
    with pytest.raises(ValueError, match='revision'):
        store.mark_outcome_seen(objective['id'], revision)
    assert store.outcomes()['unread'] == 1


@pytest.mark.parametrize('params', [{'limit': True}, {'limit': 0}, {'limit': 101}, {'limit': '1'},
                                  {'unread_only': 1}, {'unread_only': None}, {'before': ''}, {'before': []}])
def test_invalid_pages_are_rejected(store, params):
    with pytest.raises(ValueError):
        store.outcomes(**params)


def test_profiles_and_unknown_ids_fail_closed(store, tmp_path):
    objective = cancelled(store)
    other = OrganizationStore(tmp_path / 'other-profile' / 'state.db')
    assert other.outcomes()['total'] == 0
    for objective_id in [objective['id'], 'missing', '', [], None]:
        with pytest.raises(ValueError):
            other.mark_outcome_seen(objective_id, item(store)['revision'])
    with pytest.raises(ValueError, match='this profile'):
        other.outcomes(before=objective['id'])
    assert store.outcomes()['unread'] == 1


def test_late_project_and_tool_receipts_make_cancelled_outcome_unread(store):
    """Cancellation can be observed before an in-flight execution writes back."""
    from eidolon_cli.organization_history import settlement_blocker
    objective = store.create_objective('Interrupted project run', idempotency_key='late')
    claim = store.claim_next()
    with store._write() as conn:
        conn.execute('INSERT INTO project_run_starts VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                     ('run-late', claim['id'], claim['token'], objective['id'], 0,
                      '[]', 'snapshot-hash', '{}', None, 1, 1))
        conn.execute('INSERT INTO tool_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                     ('tool-late', claim['id'], 1, 'call', 'read_file', '{}', 'running', None, None, 1, None, None))
    store.cancel(objective['id'])
    observed = item(store)
    assert observed['status'] == 'cancelled'
    store.mark_outcome_seen(objective['id'], observed['revision'])
    with store._connect() as conn:
        assert 'unconfirmed' in settlement_blocker(conn, objective['id']).lower()
    with sqlite3.connect(store.path) as conn:
        conn.execute('INSERT INTO project_run_results VALUES (?,?,?,?)',
                     ('run-late', '{"status":"cancelled"}', 'result-hash', 2))
    result = item(store)
    assert result['revision'] > observed['revision'] and not result['seen']
    assert result['status'] == 'cancelled' and result['acceptanceRequestId'] is None
    with pytest.raises(ValueError, match='refresh'):
        store.mark_outcome_seen(objective['id'], observed['revision'])
    store.mark_outcome_seen(objective['id'], result['revision'])
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE tool_receipts SET status='failed',result='Interrupted read',reason='Cancelled' WHERE id='tool-late'")
    finished = item(store)
    assert finished['revision'] > result['revision'] and not finished['seen']
    with store._connect() as conn:
        assert settlement_blocker(conn, objective['id']) is None
    assert store.claim_next() is None


def test_legacy_and_fresh_cancel_summary_are_strings(store):
    objective = cancelled(store)
    assert item(store)['summary'] == ''
    with store._write() as conn:
        conn.execute('UPDATE objectives SET cancelled=0 WHERE id=?', (objective['id'],))
        conn.execute("UPDATE objective_control SET status='legacy_completed',summary=NULL WHERE objective_id=?", (objective['id'],))
    assert item(store)['summary'] == '' and item(store)['status'] == 'legacy_completed'

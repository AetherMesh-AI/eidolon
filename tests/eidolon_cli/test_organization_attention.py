"""Real SQLite attention generations, offline writers, restart and owner races."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading

import pytest

from eidolon_cli.organization_store import OrganizationStore


@pytest.fixture
def store(tmp_path):
    return OrganizationStore(tmp_path / 'organization' / 'state.db')


def pending(store):
    objective = store.create_objective('A blocked brief', idempotency_key='create')
    claim = store.claim_next()
    assert store.fail(claim, 'Needs source facts')
    return objective, claim


def item(store):
    return store.attention()['items'][0]


def test_seen_is_durable_owner_metadata_and_never_resolves_or_restarts_work(store):
    _, claim = pending(store)
    initial = store.snapshot()
    attention = initial['attention']
    assert attention == store.attention()
    assert initial['requests'][0]['attentionRevision'] == attention['items'][0]['revision']
    assert attention['unread'] == attention['total'] == 1
    observed = item(store)
    assert observed['requestId'] == claim['id'] and not observed['seen']
    store.mark_attention_seen(claim['id'], observed['revision'])
    seen = store.snapshot()
    assert seen['requests'] == initial['requests']
    assert seen['objectives'] == initial['objectives']
    assert seen['activity'] == initial['activity']
    assert seen['attention']['unread'] == 0 and seen['attention']['total'] == 1
    assert item(store)['seen']
    assert item(store)['updatedAt'] == observed['updatedAt']
    # Lost acknowledgements are safe to repeat; no append-only seen events.
    store.mark_attention_seen(claim['id'], observed['revision'])
    restarted = OrganizationStore(store.path)
    assert restarted.snapshot() == seen
    assert restarted.claim_next() is None
    with restarted._connect() as conn:
        assert conn.execute('SELECT count(*) FROM owner_attention').fetchone()[0] == 1


def test_same_blocker_reopening_while_offline_is_a_new_unseen_generation(store):
    _, claim = pending(store)
    first = item(store)
    store.mark_attention_seen(claim['id'], first['revision'])
    # No viewer or event subscription participates in the retry/failure cycle.
    backend = OrganizationStore(store.path)
    assert backend.retry(claim['id'], idempotency_key='retry')
    assert backend.attention()['total'] == 0
    reopened = backend.claim_next()
    assert backend.fail(reopened, 'Needs source facts')
    current = item(OrganizationStore(store.path))
    assert current['requestId'] == first['requestId']
    assert current['createdAt'] == first['createdAt']
    assert current['revision'] > first['revision'] and not current['seen']
    with pytest.raises(ValueError, match='refresh'):
        store.mark_attention_seen(claim['id'], first['revision'])
    assert item(store) == current


def test_independent_sql_writer_deduplicates_and_reopens_without_application_callbacks(store):
    _, claim = pending(store)
    observed = item(store)
    store.mark_attention_seen(claim['id'], observed['revision'])
    with sqlite3.connect(store.path) as conn:
        # Same reason/status and bookkeeping changes are not new owner content.
        conn.execute('UPDATE requests SET reason=reason,status=status,lease=123,available=4 WHERE id=?', (claim['id'],))
    assert item(store)['revision'] == observed['revision'] and item(store)['seen']
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE requests SET status='queued' WHERE id=?", (claim['id'],))
        conn.execute("UPDATE requests SET status='pending_intervention' WHERE id=?", (claim['id'],))
    assert item(store)['revision'] == observed['revision'] + 1
    assert not item(store)['seen']
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM owner_attention').fetchone()[0] == 1


def test_material_payload_reason_and_contract_changes_reopen_attention(store):
    _, claim = pending(store)
    with store._write() as conn:
        conn.execute('UPDATE requests SET payload=? WHERE id=?',
                     (json.dumps({'workers': 2, 'nested': {'a': [1, {'b': True}], 'empty': {}}}), claim['id']))
    observed = item(store)
    store.mark_attention_seen(claim['id'], observed['revision'])
    with sqlite3.connect(store.path) as conn:
        conn.execute('UPDATE requests SET payload=? WHERE id=?',
                     ('{"nested":{"empty":{},"a":[1,{"b":true}]},"workers":2}', claim['id']))
    assert item(store) == {**observed, 'seen': True}
    for query, value in [
        ('UPDATE requests SET reason=? WHERE id=?', 'Owner should provide a corrected source'),
        ('UPDATE requests SET payload=? WHERE id=?', '{"workers":3}'),
        ('UPDATE requests SET team=? WHERE id=?', 'research'),
        ('UPDATE request_contracts SET requested_outcome=? WHERE request_id=?', 'Read the corrected source'),
        ('UPDATE request_contracts SET required_authority=? WHERE request_id=?', 'human.permission'),
    ]:
        prior = item(store)
        store.mark_attention_seen(claim['id'], prior['revision'])
        with sqlite3.connect(store.path) as conn:
            conn.execute(query, (value, claim['id']))
        assert item(store)['revision'] == prior['revision'] + 1
        assert not item(store)['seen']
        with sqlite3.connect(store.path) as conn:
            conn.execute(query, (value, claim['id']))
        assert item(store)['revision'] == prior['revision'] + 1


def test_migration_seeds_legacy_pending_once_and_preserves_requests(store):
    _, claim = pending(store)
    original = store.snapshot()['requests']
    with sqlite3.connect(store.path) as conn:
        # A database from before attention existed, with retained pending work.
        names = conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'attention_%'").fetchall()
        for (name,) in names:
            conn.execute('DROP TRIGGER ' + name)
        conn.execute('DROP TABLE owner_attention')
    migrated = OrganizationStore(store.path)
    first = item(migrated)
    assert first['requestId'] == claim['id'] and not first['seen']
    assert migrated.snapshot()['requests'] == original
    migrated.mark_attention_seen(claim['id'], first['revision'])
    assert item(OrganizationStore(store.path)) == {**first, 'seen': True}


def test_stale_ack_racing_new_content_cannot_hide_latest_generation(store):
    _, claim = pending(store)
    old = item(store)
    second = OrganizationStore(store.path)
    barrier = threading.Barrier(2)

    def acknowledge():
        barrier.wait()
        try:
            store.mark_attention_seen(claim['id'], old['revision'])
        except ValueError as exc:
            assert 'refresh' in str(exc)

    def change():
        barrier.wait()
        with second._write() as conn:
            conn.execute('UPDATE requests SET reason=? WHERE id=?', ('A new blocker', claim['id']))
            # Generations, not wall clocks, determine whether an ack is stale.
            conn.execute('UPDATE owner_attention SET updated=created WHERE request_id=?', (claim['id'],))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(acknowledge), pool.submit(change)]
        for result in results:
            result.result()
    current = item(OrganizationStore(store.path))
    assert current['revision'] == old['revision'] + 1
    assert current['updatedAt'] == old['createdAt'] and not current['seen']


def test_cancel_archive_restore_and_resolved_ack_use_current_ledger_state(store):
    objective, claim = pending(store)
    observed = item(store)
    store.mark_attention_seen(claim['id'], observed['revision'])
    with pytest.raises(ValueError):
        store.set_objective_archived(objective['id'], True, expected_revision=0, idempotency_key='early')
    store.cancel(objective['id'])
    assert store.attention()['total'] == store.attention()['unread'] == 0
    with pytest.raises(ValueError, match='no longer pending'):
        store.mark_attention_seen(claim['id'], observed['revision'])
    store.set_objective_archived(objective['id'], True, expected_revision=0, idempotency_key='archive')
    assert store.attention()['items'] == []
    store.set_objective_archived(objective['id'], False, expected_revision=1, idempotency_key='restore')
    assert store.attention()['items'] == [] and store.claim_next() is None
    # Even legacy/corrupt archived pending rows cannot leak into current reads.
    store.set_objective_archived(objective['id'], True, expected_revision=2, idempotency_key='archive-again')
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE requests SET status='pending_intervention' WHERE id=?", (claim['id'],))
        revision = conn.execute('SELECT revision FROM owner_attention WHERE request_id=?', (claim['id'],)).fetchone()[0]
    assert store.snapshot()['attention']['total'] == 0
    with pytest.raises(ValueError, match='no longer pending'):
        store.mark_attention_seen(claim['id'], revision)


def test_keyset_pages_counts_and_unread_filter_find_old_changed_blockers(store):
    objective, claim = pending(store)
    identifiers = [claim['id']]
    with store._write() as conn:
        for _ in range(104):
            identifiers.append(store._request(conn, objective['id'], 'request.permission', 'general', 3))
        conn.execute("UPDATE requests SET status='pending_intervention',reason='Owner input' WHERE objective_id=?", (objective['id'],))
    first = store.snapshot()['attention']
    assert len(first['items']) == 100
    assert first['total'] == first['unread'] == len(identifiers) and first['hasMore']
    tail = store.attention(before=first['nextCursor'])
    assert [row['requestId'] for row in first['items'] + tail['items']] == identifiers[::-1]
    assert not tail['hasMore'] and tail['nextCursor'] is None
    for record in first['items'] + tail['items']:
        store.mark_attention_seen(record['requestId'], record['revision'])
    with sqlite3.connect(store.path) as conn:
        conn.execute('UPDATE requests SET reason=? WHERE id=?', ('Old request has new facts', identifiers[0]))
        conn.execute("UPDATE requests SET status='completed' WHERE id=?", (first['nextCursor'],))
    assert [row['requestId'] for row in store.attention(before=first['nextCursor'])['items']] == [row['requestId'] for row in tail['items']]
    unread = store.attention(unread_only=True, limit=1)
    assert unread['total'] == len(identifiers) - 1 and unread['unread'] == 1
    assert [row['requestId'] for row in unread['items']] == [identifiers[0]]
    assert unread['nextCursor'] is None and not unread['hasMore']
    snapshot = store.snapshot()
    assert set(identifiers) <= {row['id'] for row in snapshot['requests']}
    displayed = {row['id']: row for row in snapshot['requests']}
    for record in first['items'] + tail['items']:
        current_request = displayed[record['requestId']]
        if current_request['status'] == 'pending_intervention':
            assert current_request['attentionRevision'] >= record['revision']
        else:
            assert 'attentionRevision' not in current_request
    assert displayed[identifiers[0]]['attentionRevision'] == unread['items'][0]['revision']


@pytest.mark.parametrize('revision', [None, True, False, 0, -1, 1.0, '1', 2**53, [], {}])
def test_invalid_revisions_cannot_change_seen_state(store, revision):
    _, claim = pending(store)
    with pytest.raises(ValueError, match='revision'):
        store.mark_attention_seen(claim['id'], revision)
    assert store.attention()['unread'] == 1


@pytest.mark.parametrize('params', [{'limit': True}, {'limit': 0}, {'limit': 101}, {'limit': '1'},
                                  {'unread_only': 1}, {'unread_only': None}, {'before': ''}, {'before': []}])
def test_invalid_attention_pages_are_rejected(store, params):
    with pytest.raises(ValueError):
        store.attention(**params)


def test_profile_isolation_and_unknown_ids_fail_closed(store, tmp_path):
    _, claim = pending(store)
    foreign = OrganizationStore(tmp_path / 'foreign' / 'state.db')
    assert foreign.attention()['total'] == 0
    for request_id in [claim['id'], 'missing', '', [], None]:
        with pytest.raises(ValueError):
            foreign.mark_attention_seen(request_id, item(store)['revision'])
    with pytest.raises(ValueError, match='this profile'):
        foreign.attention(before=claim['id'])
    assert store.attention()['unread'] == 1


def test_exact_history_attention_is_scoped_and_reads_ignore_retained_settled_rows(store):
    objective, claim = pending(store)
    other = store.create_objective('Retained archive', idempotency_key='archive')
    with store._write() as conn:
        # Retained seen metadata must not make current polling walk history.
        for _ in range(1200):
            store._request(conn, other['id'], 'request.permission', 'general', 3)
        conn.execute("UPDATE requests SET status='pending_intervention' WHERE objective_id=?", (other['id'],))
    assert store.history_objective(objective['id'])['attention']['total'] == 1
    store.cancel(other['id'])
    store.set_objective_archived(other['id'], True, expected_revision=0, idempotency_key='archive')
    from eidolon_cli.organization_attention import attention_view
    with store._connect() as conn:
        ticks = []
        conn.set_progress_handler(lambda: ticks.append(True) or 0, 100)
        result = attention_view(conn)
        conn.set_progress_handler(None, 0)
    assert [row['requestId'] for row in result['items']] == [claim['id']]
    # Plenty of headroom for indexed lookup; a scan over retained rows exceeds
    # this bound by an order of magnitude. This checks actual DB work, not SQL text.
    assert len(ticks) < 20
    assert store.history_objective(other['id'])['attention']['items'] == []


def test_offline_reopen_exposes_a_new_generation_with_the_same_request_identity(store):
    _, claim = pending(store)
    original = store.snapshot()['requests'][0]
    # An independent ledger writer reopens the same blocker while the owner is
    # away. Neither the original creation timestamp nor text identifies this.
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE requests SET status='queued' WHERE id=?", (claim['id'],))
        conn.execute("UPDATE requests SET status='pending_intervention' WHERE id=?", (claim['id'],))
    reopened = OrganizationStore(store.path).snapshot()['requests'][0]
    assert {key: reopened[key] for key in ('id', 'status', 'reason', 'createdAt', 'attempts')} == {
        key: original[key] for key in ('id', 'status', 'reason', 'createdAt', 'attempts')}
    assert reopened.get('attentionRevision') != original.get('attentionRevision')

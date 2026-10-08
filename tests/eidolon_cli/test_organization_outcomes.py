"""Durable organization outcome lifecycle, migration and owner acknowledgements."""
import sqlite3

import pytest

from eidolon_cli.organization_store import OrganizationStore
from tests.organization_outcomes_helpers import (
    accepted,
    cancelled,
    item,
)
from tests.organization_outcomes_helpers import (
    store as store,
)


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


def test_legacy_and_fresh_cancel_summary_are_strings(store):
    objective = cancelled(store)
    assert item(store)['summary'] == ''
    with store._write() as conn:
        conn.execute('UPDATE objectives SET cancelled=0 WHERE id=?', (objective['id'],))
        conn.execute("UPDATE objective_control SET status='legacy_completed',summary=NULL WHERE objective_id=?", (objective['id'],))
    assert item(store)['summary'] == '' and item(store)['status'] == 'legacy_completed'

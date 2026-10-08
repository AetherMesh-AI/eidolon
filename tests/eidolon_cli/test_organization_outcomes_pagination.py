"""Organization outcome paging remains complete across off-page changes."""
import sqlite3

from tests.organization_outcomes_helpers import (
    cancelled,
)
from tests.organization_outcomes_helpers import (
    store as store,
)


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

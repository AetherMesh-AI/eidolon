"""Profile-local bounded aging and routine claim opportunities.

This is admission fairness, not execution-time/token allocation. P1 remains
strictly ahead of P2, so sustained P1 work can starve P2. No running work is
interrupted. The caller retains every authority, dependency and budget gate.
"""
import math

POLICY = 'routine-opportunity-v1'
SCHEDULING_SCHEMA = '''
CREATE TABLE IF NOT EXISTS organization_scheduler_state (
 id INTEGER PRIMARY KEY CHECK(id=1), policy TEXT NOT NULL,
 urgent_streak INTEGER NOT NULL CHECK(urgent_streak BETWEEN 0 AND 3));
INSERT OR IGNORE INTO organization_scheduler_state VALUES (1,'routine-opportunity-v1',0);
CREATE TABLE IF NOT EXISTS organization_scheduler_claims (
 request_id TEXT NOT NULL REFERENCES requests(id), token TEXT NOT NULL,
 policy TEXT NOT NULL, class TEXT NOT NULL CHECK(class IN ('urgent','routine')),
 base_priority INTEGER NOT NULL, score INTEGER NOT NULL, reason TEXT NOT NULL,
 created REAL NOT NULL, PRIMARY KEY(request_id,token));
CREATE TRIGGER IF NOT EXISTS organization_scheduler_claims_update_immutable
 BEFORE UPDATE ON organization_scheduler_claims BEGIN SELECT RAISE(ABORT,'Scheduling receipts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS organization_scheduler_claims_delete_immutable
 BEFORE DELETE ON organization_scheduler_claims BEGIN SELECT RAISE(ABORT,'Scheduling receipts are immutable'); END;
'''


def _state(conn):
    row = conn.execute('SELECT policy,urgent_streak FROM organization_scheduler_state WHERE id=1').fetchone()
    if row is None or row[0] != POLICY or type(row[1]) is not int or not 0 <= row[1] <= 3:
        raise ValueError('Scheduling policy state is unavailable')
    return row[1]


def _score(request, now):
    priority, created = request['priority'], request['created']
    if (type(priority) is not int or not 1 <= priority <= 5 or
            not isinstance(created, (int, float)) or not math.isfinite(created) or
            not isinstance(now, (int, float)) or not math.isfinite(now)):
        raise ValueError('Scheduling priority or time is invalid')
    return priority if priority >= 4 else min(3, priority + min(2, int(max(0, now-created) // 60)))


def order_candidates(conn, rows, *, now):
    routine_first = _state(conn) == 3
    def key(row):
        urgent = row['priority'] >= 4
        return (int(urgent == routine_first), -_score(row, now), row['created'], -row['priority'], row['id'])
    return sorted(rows, key=key)


def record_scheduling_claim(conn, request, token, *, now):
    if not conn.in_transaction:
        raise ValueError('Scheduling receipt requires the claim transaction')
    actual = conn.execute('SELECT status,token,priority,created FROM requests WHERE id=?', (request['id'],)).fetchone()
    if (actual is None or actual['status'] != 'running' or actual['token'] != token or
            actual['priority'] != request['priority'] or actual['created'] != request['created']):
        raise ValueError('Scheduling receipt requires the exact live claim')
    score = _score(request, now)
    category = 'urgent' if request['priority'] >= 4 else 'routine'
    old = conn.execute('SELECT * FROM organization_scheduler_claims WHERE request_id=? AND token=?', (request['id'], token)).fetchone()
    if old is not None:
        if old['policy'] != POLICY or old['class'] != category or old['base_priority'] != request['priority']:
            raise ValueError('Scheduling receipt conflicts with the claim')
        return
    streak = _state(conn)
    reason = 'routine_opportunity_due' if streak == 3 else 'urgent_preferred'
    conn.execute('INSERT INTO organization_scheduler_claims VALUES (?,?,?,?,?,?,?,?)',
                 (request['id'], token, POLICY, category, request['priority'], score, reason, now))
    conn.execute('UPDATE organization_scheduler_state SET urgent_streak=? WHERE id=1',
                 (min(3, streak+1) if category == 'urgent' else 0,))

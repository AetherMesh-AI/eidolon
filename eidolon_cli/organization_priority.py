"""Versioned owner scheduling preference; never execution or admission authority."""
import time

PRIORITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS objective_priority (
 objective_id TEXT PRIMARY KEY REFERENCES objectives(id), revision INTEGER NOT NULL CHECK(revision>0));
CREATE TABLE IF NOT EXISTS priority_changes (
 idempotency_key TEXT PRIMARY KEY, objective_id TEXT NOT NULL REFERENCES objectives(id),
 previous_priority INTEGER NOT NULL, priority INTEGER NOT NULL,
 expected_revision INTEGER NOT NULL, revision INTEGER NOT NULL,
 actor_id TEXT NOT NULL REFERENCES agents(id), created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS priority_change_objective ON priority_changes(objective_id,revision);
CREATE TRIGGER IF NOT EXISTS priority_change_immutable_update BEFORE UPDATE ON priority_changes
 BEGIN SELECT RAISE(ABORT,'Priority audit is immutable'); END;
CREATE TRIGGER IF NOT EXISTS priority_change_immutable_delete BEFORE DELETE ON priority_changes
 BEGIN SELECT RAISE(ABORT,'Priority audit is immutable'); END;
"""
# A stage can finish after an owner save using its old in-memory claim. New
# children and requeued stages adopt current priority in the same transaction.
# No trigger changes a running claim, lease, availability or dependency.
for _table in ('requests', 'tasks'):
    PRIORITY_SCHEMA += f"""
CREATE TRIGGER IF NOT EXISTS priority_{_table}_insert AFTER INSERT ON {_table}
 WHEN NEW.priority != (SELECT priority FROM objectives WHERE id=NEW.objective_id)
 BEGIN UPDATE {_table} SET priority=(SELECT priority FROM objectives WHERE id=NEW.objective_id) WHERE id=NEW.id; END;
"""
PRIORITY_SCHEMA += """
CREATE TRIGGER IF NOT EXISTS priority_request_requeue AFTER UPDATE OF status ON requests
 WHEN NEW.status NOT IN ('running','completed','cancelled')
 AND NEW.priority != (SELECT priority FROM objectives WHERE id=NEW.objective_id)
 BEGIN UPDATE requests SET priority=(SELECT priority FROM objectives WHERE id=NEW.objective_id) WHERE id=NEW.id; END;
"""


def priority_revision(conn, objective_id):
    row = conn.execute('SELECT revision FROM objective_priority WHERE objective_id=?', (objective_id,)).fetchone()
    return row['revision'] if row else 0


def _receipt(row):
    return {'objectiveId': row['objective_id'], 'idempotencyKey': row['idempotency_key'],
            'previousPriority': f"P{6-row['previous_priority']}", 'priority': f"P{6-row['priority']}",
            'revision': row['revision']}


class OrganizationPriorityStore:
    def change_objective_priority(self, objective_id, priority, *, expected_revision, idempotency_key):
        from eidolon_cli.organization_store import _text
        objective_id = _text(objective_id, 'Objective ID', 128)
        key = _text(idempotency_key, 'Idempotency key', 128)
        if priority not in ('P1', 'P2', 'P3', 'P4', 'P5'):
            raise ValueError('Priority must be P1–P5')
        if type(expected_revision) is not int or not 0 <= expected_revision < 100:
            raise ValueError('Priority revision must be an integer from 0 to 99')
        level = 6 - int(priority[1])
        with self._write() as conn:
            old = conn.execute('SELECT * FROM priority_changes WHERE idempotency_key=?', (key,)).fetchone()
            if old:
                if (old['objective_id'], old['priority'], old['expected_revision']) != (objective_id, level, expected_revision):
                    raise ValueError('Priority retry key belongs to a different change')
                return _receipt(old)
            row = conn.execute('SELECT o.*,c.status,coalesce(h.archived,0) AS archived FROM objectives o '
                               'JOIN objective_control c ON c.objective_id=o.id '
                               'LEFT JOIN objective_history h ON h.objective_id=o.id WHERE o.id=?', (objective_id,)).fetchone()
            if row is None:
                raise ValueError('Objective not found in this profile')
            if row['cancelled'] or row['archived'] or row['status'] in ('accepted', 'legacy_completed'):
                raise ValueError('Only nonterminal, unarchived objectives can change priority')
            if priority_revision(conn, objective_id) != expected_revision:
                raise ValueError('Priority changed; refresh and review the current value')
            if row['priority'] == level:
                raise ValueError('Choose a different priority')
            revision = expected_revision + 1
            conn.execute('UPDATE objectives SET priority=? WHERE id=?', (level, objective_id))
            conn.execute("UPDATE tasks SET priority=? WHERE objective_id=? AND status NOT IN ('completed','cancelled')", (level, objective_id))
            conn.execute("UPDATE requests SET priority=? WHERE objective_id=? AND status NOT IN ('running','completed','cancelled')", (level, objective_id))
            conn.execute('INSERT INTO objective_priority VALUES (?,?) ON CONFLICT(objective_id) DO UPDATE SET revision=excluded.revision', (objective_id, revision))
            conn.execute('INSERT INTO priority_changes VALUES (?,?,?,?,?,?,?,?)',
                         (key, objective_id, row['priority'], level, expected_revision, revision, 'owner', time.time()))
            self._event(conn, objective_id, f"Owner changed priority from P{6-row['priority']} to {priority} (revision {revision}). Running work continues; pending work uses the new priority.", 'priority', 'owner')
            return _receipt(conn.execute('SELECT * FROM priority_changes WHERE idempotency_key=?', (key,)).fetchone())

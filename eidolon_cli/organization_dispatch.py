"""Durable owner dispatch gate; already claimed stages retain existing authority."""
import time

DISPATCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS objective_dispatch (
 objective_id TEXT PRIMARY KEY REFERENCES objectives(id), paused INTEGER NOT NULL CHECK(paused IN (0,1)),
 revision INTEGER NOT NULL CHECK(revision>0));
CREATE TABLE IF NOT EXISTS dispatch_changes (
 idempotency_key TEXT PRIMARY KEY, objective_id TEXT NOT NULL REFERENCES objectives(id),
 paused INTEGER NOT NULL, expected_revision INTEGER NOT NULL, revision INTEGER NOT NULL,
 actor_id TEXT NOT NULL REFERENCES agents(id), created REAL NOT NULL);
CREATE TRIGGER IF NOT EXISTS dispatch_change_immutable_update BEFORE UPDATE ON dispatch_changes
 BEGIN SELECT RAISE(ABORT,'Dispatch audit is immutable'); END;
CREATE TRIGGER IF NOT EXISTS dispatch_change_immutable_delete BEFORE DELETE ON dispatch_changes
 BEGIN SELECT RAISE(ABORT,'Dispatch audit is immutable'); END;
CREATE TRIGGER IF NOT EXISTS request_requires_open_objective BEFORE UPDATE OF status ON requests
 WHEN NEW.status='running' AND OLD.status!='running' AND EXISTS (
 SELECT 1 FROM objective_dispatch WHERE objective_id=NEW.objective_id AND paused=1)
 BEGIN SELECT RAISE(ABORT,'Objective dispatch is paused'); END;
"""


def dispatch_view(conn, objective_id):
    row = conn.execute('SELECT * FROM objective_dispatch WHERE objective_id=?', (objective_id,)).fetchone()
    running = conn.execute("SELECT count(*) FROM requests WHERE objective_id=? AND status='running'", (objective_id,)).fetchone()[0]
    return {'paused': bool(row['paused']) if row else False, 'revision': row['revision'] if row else 0, 'runningCount': running}


def _receipt(row):
    return {'objectiveId': row['objective_id'], 'idempotencyKey': row['idempotency_key'],
            'paused': bool(row['paused']), 'revision': row['revision']}


class OrganizationDispatchStore:
    def set_objective_paused(self, objective_id, paused, *, expected_revision, idempotency_key):
        from eidolon_cli.organization_store import _text
        from eidolon_cli.organization_budget import budget_reason
        from eidolon_cli.organization_projects import validate_objective_projects
        objective_id = _text(objective_id, 'Objective ID', 128)
        key = _text(idempotency_key, 'Idempotency key', 128)
        if type(paused) is not bool or type(expected_revision) is not int or not 0 <= expected_revision < 100:
            raise ValueError('Dispatch change requires a boolean and revision from 0 to 99')
        with self._write() as conn:
            old = conn.execute('SELECT * FROM dispatch_changes WHERE idempotency_key=?', (key,)).fetchone()
            if old:
                if (old['objective_id'], bool(old['paused']), old['expected_revision']) != (objective_id, paused, expected_revision):
                    raise ValueError('Dispatch retry key belongs to a different change')
                return _receipt(old)
            row = conn.execute('SELECT o.cancelled,c.status,c.max_stages,coalesce(h.archived,0) AS archived FROM objectives o '
                               'JOIN objective_control c ON c.objective_id=o.id LEFT JOIN objective_history h ON h.objective_id=o.id WHERE o.id=?', (objective_id,)).fetchone()
            if row is None:
                raise ValueError('Objective not found in this profile')
            if row['cancelled'] or row['archived'] or row['status'] in ('accepted', 'legacy_completed'):
                raise ValueError('Only nonterminal, unarchived objectives can change dispatch')
            state = dispatch_view(conn, objective_id)
            if state['revision'] != expected_revision:
                raise ValueError('Dispatch changed; refresh and review the current state')
            if state['paused'] == paused:
                raise ValueError('Objective already has this dispatch state')
            if not paused:
                self._require_current_policy(conn)
                reason = budget_reason(conn, objective_id, self.settings)
                if reason:
                    raise ValueError(reason)
                stages = conn.execute('SELECT count(*) FROM objective_usage WHERE objective_id=?', (objective_id,)).fetchone()[0]
                if stages >= min(row['max_stages'], self.settings.max_stages):
                    raise ValueError('Objective stage budget exhausted')
                validate_objective_projects(conn, objective_id, self.settings)
                for request in conn.execute("SELECT * FROM requests WHERE objective_id=? AND status='queued'", (objective_id,)):
                    # Unmet dependencies stay queued; no assignment or permission is created here.
                    if self._dependencies_ready(conn, request) and self._coordination_ready(conn, request):
                        if not self._eligible(conn, request)[0]:
                            raise ValueError('Current authority cannot dispatch a ready request; review permissions and staffing')
                        if request['attempts'] >= self.settings.max_attempts:
                            raise ValueError('Attempt limit reached; resume cannot renew it')
            revision = expected_revision + 1
            conn.execute('INSERT INTO objective_dispatch VALUES (?,?,?) ON CONFLICT(objective_id) DO UPDATE SET paused=excluded.paused,revision=excluded.revision', (objective_id, int(paused), revision))
            conn.execute('INSERT INTO dispatch_changes VALUES (?,?,?,?,?,?,?)', (key, objective_id, int(paused), expected_revision, revision, 'owner', time.time()))
            self._event(conn, objective_id, 'Owner paused new dispatch; already claimed stages may continue.' if paused else 'Owner resumed dispatch under current authority and retained allowances.', 'dispatch', 'owner')
            return _receipt(conn.execute('SELECT * FROM dispatch_changes WHERE idempotency_key=?', (key,)).fetchone())

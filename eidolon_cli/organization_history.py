"""Owner-controlled, reversible history visibility without moving ledger records.

Archive is a projection boundary, never deletion, execution cancellation, or a
new permission. Original IDs, dependencies, artifacts, receipts and identities
stay in the same transactional ledger. Every transition has a bounded immutable
receipt, so retries and delayed clients cannot invert a later owner decision.
"""
from __future__ import annotations

import json
import time

MAX_CURRENT_OBJECTIVES = 1000
MAX_ARCHIVED_OBJECTIVES = 10000
MAX_HISTORY_TRANSITIONS = 100

HISTORY_SCHEMA = """
CREATE INDEX IF NOT EXISTS history_task_objective_status ON tasks(objective_id,status);
CREATE INDEX IF NOT EXISTS history_active_tasks ON tasks(objective_id)
 WHERE status NOT IN ('completed','cancelled');
CREATE INDEX IF NOT EXISTS history_active_requests ON requests(objective_id)
 WHERE status NOT IN ('completed','cancelled');
CREATE INDEX IF NOT EXISTS history_project_objective ON project_run_starts(objective_id);

CREATE TABLE IF NOT EXISTS objective_history (
 objective_id TEXT PRIMARY KEY REFERENCES objectives(id),
 archived INTEGER NOT NULL CHECK(archived IN (0,1)),
 revision INTEGER NOT NULL CHECK(revision>0), updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS objective_history_archived ON objective_history(archived,objective_id);
CREATE TABLE IF NOT EXISTS history_transitions (
 idempotency_key TEXT PRIMARY KEY,
 objective_id TEXT NOT NULL REFERENCES objectives(id),
 archived INTEGER NOT NULL CHECK(archived IN (0,1)),
 expected_revision INTEGER NOT NULL, revision INTEGER NOT NULL,
 actor_id TEXT NOT NULL REFERENCES agents(id), created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS history_transition_objective ON history_transitions(objective_id,revision);
CREATE TRIGGER IF NOT EXISTS history_transition_immutable_update
 BEFORE UPDATE ON history_transitions BEGIN SELECT RAISE(ABORT,'History audit is immutable'); END;
CREATE TRIGGER IF NOT EXISTS history_transition_immutable_delete
 BEFORE DELETE ON history_transitions BEGIN SELECT RAISE(ABORT,'History audit is immutable'); END;
"""


def history_counts(conn):
    archived = conn.execute('SELECT count(*) FROM objective_history WHERE archived=1').fetchone()[0]
    total = conn.execute('SELECT count(*) FROM objectives').fetchone()[0]
    return {'current': total - archived, 'archived': archived,
            'currentLimit': MAX_CURRENT_OBJECTIVES, 'archiveLimit': MAX_ARCHIVED_OBJECTIVES}


def settlement_blocker(conn, objective_id):
    """Indexed local settlement check, independent of the retained archive size."""
    row = conn.execute('SELECT o.cancelled,c.status FROM objectives o '
                       'JOIN objective_control c ON c.objective_id=o.id WHERE o.id=?', (objective_id,)).fetchone()
    if row is None:
        raise ValueError('Objective not found in this profile')
    if not row['cancelled'] and row['status'] not in {'accepted', 'legacy_completed'}:
        return 'Only completed or cancelled objectives can be archived.'
    for table in ('requests', 'tasks'):
        if conn.execute(f"SELECT 1 FROM {table} WHERE objective_id=? AND status NOT IN ('completed','cancelled') LIMIT 1",
                        (objective_id,)).fetchone():
            return 'Unfinished work or an unresolved intervention must remain visible.'
    if conn.execute("SELECT 1 FROM tool_receipts t JOIN requests r ON r.id=t.request_id "
                    "WHERE r.objective_id=? AND t.status IN ('running','unknown') LIMIT 1", (objective_id,)).fetchone():
        return 'An unconfirmed tool outcome must remain visible for inspection.'
    for run in conn.execute('SELECT r.record FROM project_run_starts s LEFT JOIN project_run_results r '
                            'ON r.run_id=s.id WHERE s.objective_id=?', (objective_id,)):
        if run['record'] is None or json.loads(run['record']).get('status') in {'running', 'unknown'}:
            return 'An unconfirmed project execution must remain visible for inspection.'
    return None


def live_history_references(conn):
    """Resolve only live links once per projection, never once per old objective."""
    references = {}
    queries = [
        ("SELECT DISTINCT target.objective_id FROM tasks active, json_each(active.dependencies) dependency "
         "JOIN tasks target ON target.id=dependency.value "
         "WHERE active.objective_id!=target.objective_id AND active.status NOT IN ('completed','cancelled')",
         'Another objective has unfinished work depending on this objective.'),
        ("SELECT DISTINCT parent.objective_id FROM requests active JOIN request_contracts c ON c.request_id=active.id "
         "JOIN requests parent ON parent.id=c.parent_request_id "
         "WHERE active.objective_id!=parent.objective_id AND active.status NOT IN ('completed','cancelled')",
         'An open child request still references this objective.'),
        ("SELECT DISTINCT target.objective_id FROM requests active JOIN request_contracts c ON c.request_id=active.id, "
         "json_each(c.dependencies) dependency JOIN requests target ON target.id=dependency.value "
         "WHERE active.objective_id!=target.objective_id AND active.status NOT IN ('completed','cancelled')",
         'An open request still depends on this objective.'),
    ]
    for query, reason in queries:
        for row in conn.execute(query):
            references.setdefault(row['objective_id'], reason)
    return references


def archive_blocker(conn, objective_id, *, live_references=None):
    """Fail closed on unfinished work, unknown tool outcomes and live references."""
    blocker = settlement_blocker(conn, objective_id)
    if blocker:
        return blocker
    # Original rows never move, including same-objective dependency records.
    # Live cross-objective prerequisites additionally remain in the current view.
    references = live_history_references(conn) if live_references is None else live_references
    return references.get(objective_id)


def history_view(conn, objective_id, *, live_references=None):
    from eidolon_cli.organization_store import _iso
    row = conn.execute('SELECT * FROM objective_history WHERE objective_id=?', (objective_id,)).fetchone()
    archived = bool(row and row['archived'])
    revision = row['revision'] if row else 0
    blocker = archive_blocker(conn, objective_id, live_references=live_references) if not archived else None
    if revision >= MAX_HISTORY_TRANSITIONS:
        blocker = 'This objective has reached its history-transition audit limit.'
    return {'archived': archived, 'revision': revision,
            'updatedAt': _iso(row['updated']) if row else None,
            'canArchive': not archived and blocker is None,
            'canRestore': archived and revision < MAX_HISTORY_TRANSITIONS,
            'blocker': blocker}


class OrganizationHistoryStore:
    def set_objective_archived(self, objective_id, archived, *, expected_revision, idempotency_key):
        from eidolon_cli.organization_store import _text, _iso
        objective_id = _text(objective_id, 'Objective ID', 128)
        key = _text(idempotency_key, 'History idempotency key', 128)
        if type(archived) is not bool:
            raise ValueError('archived must be a boolean')
        if type(expected_revision) is not int or not 0 <= expected_revision <= MAX_HISTORY_TRANSITIONS:
            raise ValueError('expectedRevision must be a bounded nonnegative integer')
        with self._write() as conn:
            old = conn.execute('SELECT * FROM history_transitions WHERE idempotency_key=?', (key,)).fetchone()
            if old:
                if (old['objective_id'], bool(old['archived']), old['expected_revision']) != (objective_id, archived, expected_revision):
                    raise ValueError('History idempotency key belongs to different input')
                return {'objectiveId': objective_id, 'archived': archived, 'revision': old['revision'], 'updatedAt': _iso(old['created'])}
            if conn.execute('SELECT 1 FROM objectives WHERE id=?', (objective_id,)).fetchone() is None:
                raise ValueError('Objective not found in this profile')
            state = conn.execute('SELECT * FROM objective_history WHERE objective_id=?', (objective_id,)).fetchone()
            revision = state['revision'] if state else 0
            if revision != expected_revision:
                raise ValueError('History changed since it was loaded; refresh before trying again')
            if revision >= MAX_HISTORY_TRANSITIONS:
                raise ValueError('This objective has reached its history-transition audit limit')
            if bool(state and state['archived']) == archived:
                raise ValueError('Objective is already in the requested history state')
            if archived:
                blocker = archive_blocker(conn, objective_id)
                if blocker:
                    raise ValueError(blocker)
            counts = history_counts(conn)
            if archived and counts['archived'] >= MAX_ARCHIVED_OBJECTIVES:
                raise ValueError('Archive capacity reached; existing history is retained and no data was deleted')
            if not archived and counts['current'] >= MAX_CURRENT_OBJECTIVES:
                raise ValueError('Current-objective capacity reached; archive settled work before restoring history')
            now = time.time()
            conn.execute('INSERT INTO objective_history VALUES (?,?,?,?) ON CONFLICT(objective_id) '
                         'DO UPDATE SET archived=excluded.archived,revision=excluded.revision,updated=excluded.updated',
                         (objective_id, int(archived), revision + 1, now))
            conn.execute('INSERT INTO history_transitions VALUES (?,?,?,?,?,?,?)',
                         (key, objective_id, int(archived), revision, revision + 1, 'owner', now))
            self._event(conn, objective_id, 'Owner archived settled history; all records remain retained.' if archived else
                        'Owner restored settled history to the current view; execution was not restarted.', 'history', 'owner')
            return {'objectiveId': objective_id, 'archived': archived, 'revision': revision + 1, 'updatedAt': _iso(now)}

    def history(self, *, query='', state='archived', before=None, limit=25):
        from eidolon_cli.organization_store import _iso, _text
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError('History search must be text of at most 200 characters')
        if not isinstance(state, str) or state not in {'all', 'archived', 'current'}:
            raise ValueError('History state must be all, archived, or current')
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('History page limit must be between 1 and 50')
        if before is not None:
            before = _text(before, 'History cursor', 128)
        with self._connect() as conn:
            # One read transaction pins the page, cursor and counts together.
            conn.execute('BEGIN')
            clauses, args = [], []
            if state != 'all':
                clauses.append('coalesce(h.archived,0)=?')
                args.append(int(state == 'archived'))
            if query:
                clauses.append('(instr(lower(o.title),lower(?))>0 OR instr(lower(o.description),lower(?))>0 OR o.id=?)')
                args.extend([query, query, query])
            if before:
                anchor = conn.execute('SELECT rowid FROM objectives WHERE id=?', (before,)).fetchone()
                if anchor is None:
                    raise ValueError('History cursor not found in this profile')
                clauses.append('o.rowid<?')
                args.append(anchor[0])
            where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
            rows = conn.execute('SELECT o.*,c.status AS acceptance_status FROM objectives o '
                                'LEFT JOIN objective_history h ON h.objective_id=o.id '
                                'JOIN objective_control c ON c.objective_id=o.id' + where +
                                ' ORDER BY o.rowid DESC LIMIT ?', (*args, limit + 1)).fetchall()
            live_references = live_history_references(conn)
            items = [{'id': row['id'], 'title': row['title'], 'createdAt': _iso(row['created']),
                      'status': 'cancelled' if row['cancelled'] else 'completed' if row['acceptance_status'] in
                      {'accepted', 'legacy_completed'} else 'open', 'history': history_view(conn, row['id'], live_references=live_references)}
                     for row in rows[:limit]]
            return {'items': items, 'nextCursor': items[-1]['id'] if len(rows) > limit else None,
                    'counts': history_counts(conn)}

    def history_objective(self, objective_id):
        from eidolon_cli.organization_store import _text
        from eidolon_cli.organization_snapshot import build_snapshot
        objective_id = _text(objective_id, 'Objective ID', 128)
        with self._connect() as conn:
            conn.execute('BEGIN')
            if not conn.execute('SELECT 1 FROM objectives WHERE id=?', (objective_id,)).fetchone():
                raise ValueError('Objective not found in this profile')
            return build_snapshot(conn, self.settings, objective_id=objective_id,
                                  resolution_options=self.allowed_owner_resolutions)

"""Durable owner acknowledgement of authoritative objective outcomes.

This is a projection, never another completion authority. One metadata row per
objective retains its generation even while reopened work has no current outcome.
Triggers cover offline/independent writers; archive changes visibility, not seen.
"""
from __future__ import annotations

import json

MAX_OUTCOME_PAGE = 100
MAX_OUTCOME_REVISION = 2**53 - 1
_NOW = "(julianday('now') - 2440587.5) * 86400.0"
_TERMINAL = "(o.cancelled=1 OR c.status IN ('accepted','legacy_completed'))"


def _touch(objective):
    # Existing rows are invalidated even when leaving a terminal state. Retaining
    # their generation prevents a stale acknowledgement matching after reopening.
    return f"""
    INSERT INTO owner_outcomes(objective_id,revision,seen_revision,created,updated)
    SELECT o.id,1,0,{_NOW},{_NOW} FROM objectives o
    JOIN objective_control c ON c.objective_id=o.id WHERE o.id={objective}
      AND ({_TERMINAL} OR EXISTS(SELECT 1 FROM owner_outcomes WHERE objective_id=o.id))
    ON CONFLICT(objective_id) DO UPDATE SET revision=revision+1,updated=excluded.updated;
    """


OUTCOME_SCHEMA = """
CREATE TABLE IF NOT EXISTS owner_outcomes (
 objective_id TEXT PRIMARY KEY REFERENCES objectives(id) ON DELETE CASCADE,
 revision INTEGER NOT NULL CHECK(revision>0),
 seen_revision INTEGER NOT NULL DEFAULT 0 CHECK(seen_revision>=0 AND seen_revision<=revision),
 created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS owner_outcome_generation (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL);
INSERT OR IGNORE INTO owner_outcome_generation VALUES(1,0);
"""
# A full-ledger epoch lets paged clients invalidate off-page changes without
# serializing retained outcomes into the polling snapshot.
for _table in ('owner_outcomes', 'objective_history'):
    for _action in ('INSERT', 'UPDATE', 'DELETE'):
        _condition = (' WHEN OLD.revision IS NOT NEW.revision OR OLD.seen_revision IS NOT NEW.seen_revision'
                      if _table == 'owner_outcomes' and _action == 'UPDATE' else '')
        OUTCOME_SCHEMA += f"""
        CREATE TRIGGER IF NOT EXISTS outcome_epoch_{_table}_{_action.lower()}
        AFTER {_action} ON {_table}{_condition}
        BEGIN UPDATE owner_outcome_generation SET revision=revision+1 WHERE singleton=1; END;
        """

# Only material owner-facing fields advance a generation. No-op writes, polling,
# leases, acknowledgement and archive/restore cannot manufacture unread outcomes.
for _table, _identity, _fields in [
    ('objective_control', 'objective_id', ('status', 'round', 'summary', 'deliverable_id', 'criteria', 'amended_scope', 'required_checks', 'delivery_mode')),
    ('objectives', 'id', ('cancelled', 'title', 'description')),
    ('objective_deliverables', 'objective_id', ('content', 'sha256', 'summary', 'evidence_ids', 'round', 'author_id')),
    ('objective_acceptances', 'objective_id', ('approved', 'summary', 'evidence_ids', 'criteria_results', 'conflicts', 'round', 'agent_id')),
    ('evidence', 'objective_id', ('content', 'sha256', 'summary')),
    ('requests', 'objective_id', ('status', 'reason', 'agent_id', 'type', 'payload')),
    ('tasks', 'objective_id', ('status',)),
]:
    _changed = ' OR '.join(f'OLD.{field} IS NOT NEW.{field}' for field in _fields)
    OUTCOME_SCHEMA += f"""
    CREATE TRIGGER IF NOT EXISTS outcome_{_table}_update
    AFTER UPDATE OF {','.join(_fields)} ON {_table} WHEN {_changed}
    BEGIN {_touch('NEW.' + _identity)} END;
    """
    if _table != 'objectives':
        OUTCOME_SCHEMA += f"""
        CREATE TRIGGER IF NOT EXISTS outcome_{_table}_insert AFTER INSERT ON {_table}
        BEGIN {_touch('NEW.' + _identity)} END;
        CREATE TRIGGER IF NOT EXISTS outcome_{_table}_delete AFTER DELETE ON {_table}
        BEGIN {_touch('OLD.' + _identity)} END;
        """

# Immutable execution/validation records can arrive after cancellation. Keep
# their retained evidence visible as a new outcome generation without granting
# execution authority or claiming cancellation settled every side effect.
for _table, _objective in [
    ('project_run_starts', 'NEW.objective_id'),
    ('project_run_results', '(SELECT objective_id FROM project_run_starts WHERE id=NEW.run_id)'),
    ('project_run_reviews', '(SELECT objective_id FROM project_run_starts WHERE id=NEW.run_id)'),
    ('project_source_receipts', '(SELECT objective_id FROM project_run_starts WHERE id=NEW.run_id)'),
    ('objective_project_validations', 'NEW.objective_id'),
    ('project_validation_receipts', '(SELECT objective_id FROM requests WHERE id=NEW.request_id)'),
    ('project_handoff_receipts', '(SELECT objective_id FROM requests WHERE id=NEW.request_id)'),
    ('edit_applications', '(SELECT objective_id FROM requests WHERE id=NEW.request_id)'),
    ('edit_reviews', '(SELECT objective_id FROM requests WHERE id=NEW.request_id)'),
]:
    OUTCOME_SCHEMA += f"""
    CREATE TRIGGER IF NOT EXISTS outcome_{_table}_insert AFTER INSERT ON {_table}
    BEGIN {_touch(_objective)} END;
    """
for _table, _lookup, _fields in [
    ('tool_receipts', '(SELECT objective_id FROM requests WHERE id={row}.request_id)',
     ('status', 'result', 'result_sha256', 'reason', 'arguments', 'tool_name')),
    ('evidence_tools', '(SELECT objective_id FROM evidence WHERE id={row}.evidence_id)',
     ('evidence_id', 'receipt_id')),
    ('reviews', '(SELECT objective_id FROM tasks WHERE id={row}.task_id)',
     ('approved', 'summary', 'evidence_ids', 'agent_id')),
]:
    for _action, _row in [('INSERT', 'NEW'), ('DELETE', 'OLD'), ('UPDATE', 'NEW')]:
        _condition = (' WHEN ' + ' OR '.join(f'OLD.{field} IS NOT NEW.{field}' for field in _fields)
                      if _action == 'UPDATE' else '')
        OUTCOME_SCHEMA += f"""
        CREATE TRIGGER IF NOT EXISTS outcome_{_table}_{_action.lower()}
        AFTER {_action} ON {_table}{_condition}
        BEGIN {_touch(_lookup.format(row=_row))} END;
        """

_CURRENT = f"""FROM owner_outcomes a JOIN objectives o ON o.id=a.objective_id
 JOIN objective_control c ON c.objective_id=o.id
 LEFT JOIN objective_history h ON h.objective_id=o.id
 WHERE {_TERMINAL} AND (o.cancelled=1 OR (
 NOT EXISTS(SELECT 1 FROM requests r WHERE r.objective_id=o.id AND r.status NOT IN ('completed','cancelled'))
 AND NOT EXISTS(SELECT 1 FROM tasks t WHERE t.objective_id=o.id AND t.status NOT IN ('completed','cancelled'))))"""


def outcomes_view(conn, *, limit=MAX_OUTCOME_PAGE, before=None, unread_only=False, objective_id=None):
    """Bounded keyset page over retained results, with complete profile counts."""
    from eidolon_cli.organization_store import _iso, _text
    if type(limit) is not int or not 1 <= limit <= MAX_OUTCOME_PAGE:
        raise ValueError(f'Outcome page limit must be between 1 and {MAX_OUTCOME_PAGE}')
    if type(unread_only) is not bool:
        raise ValueError('unreadOnly must be a boolean')
    clauses, args = [], []
    if before is not None:
        before = _text(before, 'Outcome cursor', 128)
        anchor = conn.execute('SELECT rowid FROM objectives WHERE id=?', (before,)).fetchone()
        if anchor is None:
            raise ValueError('Outcome cursor not found in this profile')
        clauses.append('o.rowid<?')
        args.append(anchor[0])
    if unread_only:
        clauses.append('a.seen_revision<a.revision')
    current, scope_args = _CURRENT, ()
    if objective_id is not None:
        current += ' AND o.id=?'
        scope_args = (objective_id,)
    counts = conn.execute('SELECT count(*),coalesce(sum(a.seen_revision<a.revision),0) ' + current, scope_args).fetchone()
    rows = conn.execute('SELECT a.*,o.title,o.cancelled,c.status,c.round,substr(c.summary,1,2000) AS summary,'
                        'c.deliverable_id,coalesce(h.archived,0) AS archived ' + current +
                        ''.join(' AND ' + clause for clause in clauses) + ' ORDER BY o.rowid DESC LIMIT ?',
                        (*scope_args, *args, limit + 1)).fetchall()
    items = []
    for row in rows[:limit]:
        status = 'cancelled' if row['cancelled'] else row['status']
        acceptance = conn.execute('SELECT request_id,evidence_ids FROM objective_acceptances '
                                  'WHERE objective_id=? AND round=? AND approved=1 ORDER BY created DESC,request_id DESC LIMIT 1',
                                  (row['objective_id'], row['round'])).fetchone() if status == 'accepted' else None
        items.append({'objectiveId': row['objective_id'], 'revision': row['revision'],
                      'seen': row['seen_revision'] == row['revision'], 'status': status,
                      'title': row['title'], 'summary': row['summary'] or '', 'round': row['round'],
                      'deliverableId': row['deliverable_id'] if status == 'accepted' else None,
                      'acceptanceRequestId': acceptance['request_id'] if acceptance else None,
                      'evidenceIds': json.loads(acceptance['evidence_ids']) if acceptance else [],
                      'createdAt': _iso(row['created']), 'updatedAt': _iso(row['updated']),
                      'archived': bool(row['archived'])})
    has_more = len(rows) > limit
    return {'items': items, 'unread': counts[1], 'total': counts[0], 'hasMore': has_more,
            'generation': str(conn.execute('SELECT revision FROM owner_outcome_generation WHERE singleton=1').fetchone()[0]),
            'nextCursor': items[-1]['objectiveId'] if has_more else None}


class OrganizationOutcomeStore:
    @staticmethod
    def _migrate_outcomes(conn):
        conn.execute('INSERT OR IGNORE INTO owner_outcomes(objective_id,revision,seen_revision,created,updated) '
                     'SELECT o.id,1,0,o.created,o.created FROM objectives o '
                     f'JOIN objective_control c ON c.objective_id=o.id WHERE {_TERMINAL}')

    def outcomes(self, *, limit=MAX_OUTCOME_PAGE, before=None, unread_only=False):
        with self._connect() as conn:
            conn.execute('BEGIN')
            return outcomes_view(conn, limit=limit, before=before, unread_only=unread_only)

    def mark_outcome_seen(self, objective_id, revision):
        from eidolon_cli.organization_store import _text
        objective_id = _text(objective_id, 'Objective ID', 128)
        if type(revision) is not int or not 1 <= revision <= MAX_OUTCOME_REVISION:
            raise ValueError('revision must be a positive safe integer')
        with self._write() as conn:
            changed = conn.execute('UPDATE owner_outcomes SET seen_revision=revision '
                                   'WHERE objective_id=? AND revision=? AND objective_id IN '
                                   '(SELECT o.id ' + _CURRENT + ')', (objective_id, revision))
            if changed.rowcount != 1:
                raise ValueError('Outcome changed or is no longer terminal in this profile; refresh before marking seen')

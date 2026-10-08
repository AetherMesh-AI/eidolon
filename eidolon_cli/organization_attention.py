"""Durable owner seen state projected from authoritative pending requests.

One row per request retains its attention generation across retries and restarts.
Triggers cover every ledger writer, including another backend without a viewer.
Seen never changes request state, execution authority, or completion evidence.
"""
from __future__ import annotations

MAX_ATTENTION_PAGE = 100
MAX_ATTENTION_REVISION = 2**53 - 1

# Compare JSON paths/types/atoms, rather than serialized object key order or
# whitespace. Container paths retain empty objects/arrays and array positions.
# SQLite JSON is already required by the organization history/request ledger.
_PAYLOAD_CHANGED = """(OLD.payload IS NOT NEW.payload AND CASE
 WHEN json_valid(OLD.payload) AND json_valid(NEW.payload) THEN
  EXISTS (SELECT fullkey,type,atom FROM json_tree(OLD.payload)
          EXCEPT SELECT fullkey,type,atom FROM json_tree(NEW.payload)) OR
  EXISTS (SELECT fullkey,type,atom FROM json_tree(NEW.payload)
          EXCEPT SELECT fullkey,type,atom FROM json_tree(OLD.payload))
 ELSE 1 END)"""
_NOW = "(julianday('now') - 2440587.5) * 86400.0"
_BUMP = f"""
 INSERT INTO owner_attention(request_id,revision,seen_revision,created,updated)
 VALUES (NEW.id,1,0,{_NOW},{_NOW})
 ON CONFLICT(request_id) DO UPDATE SET revision=revision+1,updated=excluded.updated;
"""
ATTENTION_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS owner_attention (
 request_id TEXT PRIMARY KEY REFERENCES requests(id) ON DELETE CASCADE,
 revision INTEGER NOT NULL CHECK(revision>0),
 seen_revision INTEGER NOT NULL DEFAULT 0 CHECK(seen_revision>=0 AND seen_revision<=revision),
 created REAL NOT NULL, updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS attention_pending_requests ON requests(id,objective_id)
 WHERE status='pending_intervention';
CREATE TRIGGER IF NOT EXISTS attention_request_insert AFTER INSERT ON requests
 WHEN NEW.status='pending_intervention' BEGIN {_BUMP} END;
CREATE TRIGGER IF NOT EXISTS attention_request_update
 AFTER UPDATE OF status,reason,payload,type,team,task_id ON requests
 WHEN NEW.status='pending_intervention' AND (
  OLD.status IS NOT NEW.status OR OLD.reason IS NOT NEW.reason OR
  OLD.type IS NOT NEW.type OR OLD.team IS NOT NEW.team OR
  OLD.task_id IS NOT NEW.task_id OR {_PAYLOAD_CHANGED})
 BEGIN {_BUMP} END;
CREATE TRIGGER IF NOT EXISTS attention_contract_update AFTER UPDATE ON request_contracts
 WHEN (OLD.requester_id IS NOT NEW.requester_id OR
       OLD.requested_outcome IS NOT NEW.requested_outcome OR
       OLD.required_authority IS NOT NEW.required_authority OR
       OLD.parent_request_id IS NOT NEW.parent_request_id OR
       OLD.dependencies IS NOT NEW.dependencies OR OLD.evidence_ids IS NOT NEW.evidence_ids)
  AND EXISTS (SELECT 1 FROM requests WHERE id=NEW.request_id AND status='pending_intervention')
 BEGIN UPDATE owner_attention SET revision=revision+1,updated={_NOW} WHERE request_id=NEW.request_id; END;
CREATE TRIGGER IF NOT EXISTS attention_contract_insert AFTER INSERT ON request_contracts
 WHEN EXISTS (SELECT 1 FROM requests WHERE id=NEW.request_id AND status='pending_intervention')
 BEGIN UPDATE owner_attention SET revision=revision+1,updated={_NOW} WHERE request_id=NEW.request_id; END;
CREATE TRIGGER IF NOT EXISTS attention_contract_delete AFTER DELETE ON request_contracts
 WHEN EXISTS (SELECT 1 FROM requests WHERE id=OLD.request_id AND status='pending_intervention')
 BEGIN UPDATE owner_attention SET revision=revision+1,updated={_NOW} WHERE request_id=OLD.request_id; END;
"""

_CURRENT = """FROM owner_attention a JOIN requests r ON r.id=a.request_id
 LEFT JOIN objective_history h ON h.objective_id=r.objective_id
 WHERE r.status='pending_intervention' AND coalesce(h.archived,0)=0"""


def attention_view(conn, *, limit=MAX_ATTENTION_PAGE, before=None, unread_only=False, objective_id=None):
    """A bounded keyset page and complete counts in the caller's read transaction."""
    from eidolon_cli.organization_store import _iso, _text
    if type(limit) is not int or not 1 <= limit <= MAX_ATTENTION_PAGE:
        raise ValueError(f'Attention page limit must be between 1 and {MAX_ATTENTION_PAGE}')
    if type(unread_only) is not bool:
        raise ValueError('unreadOnly must be a boolean')
    clauses, args = [], []
    if before is not None:
        before = _text(before, 'Attention cursor', 128)
        # Requests are retained even when resolved. A page cursor therefore
        # remains valid after acknowledgement, resolution or archive.
        anchor = conn.execute('SELECT rowid FROM requests WHERE id=?', (before,)).fetchone()
        if anchor is None:
            raise ValueError('Attention cursor not found in this profile')
        clauses.append('r.rowid<?')
        args.append(anchor[0])
    if unread_only:
        clauses.append('a.seen_revision<a.revision')
    current, scope_args = _CURRENT, ()
    if objective_id is not None:
        current += ' AND r.objective_id=?'
        scope_args = (objective_id,)
    counts = conn.execute('SELECT count(*),coalesce(sum(a.seen_revision<a.revision),0) ' + current, scope_args).fetchone()
    query = ('SELECT a.*,r.objective_id ' + current +
             ''.join(' AND ' + clause for clause in clauses) + ' ORDER BY r.rowid DESC LIMIT ?')
    rows = conn.execute(query, (*scope_args, *args, limit + 1)).fetchall()
    items = [{'requestId': row['request_id'], 'objectiveId': row['objective_id'],
              'revision': row['revision'], 'seen': row['seen_revision'] == row['revision'],
              'createdAt': _iso(row['created']), 'updatedAt': _iso(row['updated'])}
             for row in rows[:limit]]
    has_more = len(rows) > limit
    return {'items': items, 'unread': counts[1], 'total': counts[0], 'hasMore': has_more,
            'nextCursor': items[-1]['requestId'] if has_more else None}


class OrganizationAttentionStore:
    @staticmethod
    def _migrate_attention(conn):
        # Earliest known timestamp for pre-feature pending requests. Reopening
        # the ledger never resets acknowledgement or invents a new generation.
        conn.execute("INSERT OR IGNORE INTO owner_attention(request_id,revision,seen_revision,created,updated) "
                     "SELECT id,1,0,created,created FROM requests WHERE status='pending_intervention'")

    def attention(self, *, limit=MAX_ATTENTION_PAGE, before=None, unread_only=False):
        with self._connect() as conn:
            conn.execute('BEGIN')
            return attention_view(conn, limit=limit, before=before, unread_only=unread_only)

    def mark_attention_seen(self, request_id, revision):
        from eidolon_cli.organization_store import _text
        request_id = _text(request_id, 'Request ID', 128)
        if type(revision) is not int or not 1 <= revision <= MAX_ATTENTION_REVISION:
            raise ValueError('revision must be a positive safe integer')
        with self._write() as conn:
            changed = conn.execute(
                'UPDATE owner_attention SET seen_revision=revision WHERE request_id=? AND revision=? '
                'AND request_id IN (SELECT r.id FROM requests r LEFT JOIN objective_history h '
                "ON h.objective_id=r.objective_id WHERE r.status='pending_intervention' AND coalesce(h.archived,0)=0)",
                (request_id, revision))
            if changed.rowcount != 1:
                raise ValueError('Attention changed or is no longer pending in this profile; refresh before marking seen')

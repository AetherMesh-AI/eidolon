"""Immutable, exact-receipt objective dependencies within existing authority.

The admission caller owns semantic authorization and its write transaction. This
graph only narrows that authority: it never initializes storage, grants access,
refreshes a stale pin, starts work, or treats retained evidence as live source.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3

from eidolon_cli.organization_dependency_receipts import accepted_dependency_receipt
from eidolon_cli.organization_milestones import _objective
from eidolon_cli.organization_outcome_contracts import digest


TABLE = 'organization_objective_dependencies'
MAX_DEPENDENCY_CONTEXT_ITEMS = 12
MAX_DEPENDENCY_CONTEXT_BYTES = 64 * 1024
SCHEMA = f'''
CREATE TABLE IF NOT EXISTS {TABLE} (
 predecessor_id TEXT NOT NULL REFERENCES objectives(id),
 successor_id TEXT NOT NULL REFERENCES objectives(id),
 record TEXT NOT NULL, sha256 TEXT NOT NULL,
 PRIMARY KEY(predecessor_id,successor_id), CHECK(predecessor_id<>successor_id));
CREATE INDEX IF NOT EXISTS objective_dependencies_successor ON {TABLE}(successor_id);
'''
for _operation in ('UPDATE', 'DELETE'):
    SCHEMA += (f'CREATE TRIGGER IF NOT EXISTS immutable_{TABLE}_{_operation.lower()} '
               f'BEFORE {_operation} ON {TABLE} BEGIN SELECT RAISE(ABORT, '
               "'Immutable objective dependency'); END;\n")


class ObjectiveDependencyError(ValueError):
    """Stable blocking reason for admission and owner-facing resolution routing."""

    def __init__(self, reason):
        self.reason = reason
        message = 'Objective dependency: ' + reason
        if reason == 'oversized_dependency_context':
            message += (f'. This context supports at most {MAX_DEPENDENCY_CONTEXT_ITEMS} accepted dependencies '
                        f'and {MAX_DEPENDENCY_CONTEXT_BYTES} serialized UTF-8 bytes. Select fewer or narrower '
                        'accepted deliverables within the current context budget. Retained content is preserved.')
        super().__init__(message)


def _require(condition, reason):
    if not condition:
        raise ObjectiveDependencyError(reason)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _identifier(value):
    _require(isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}', value),
             'invalid_identity')
    return value


def _schema_available(conn):
    names = {row['name']: row['type'] for row in conn.execute(
        'SELECT name,type FROM sqlite_master WHERE name=? OR name IN (?,?)',
        (TABLE, f'immutable_{TABLE}_update', f'immutable_{TABLE}_delete'))}
    if not names:
        return False
    _require(names == {TABLE: 'table', f'immutable_{TABLE}_update': 'trigger',
                       f'immutable_{TABLE}_delete': 'trigger'}, 'incomplete_dependency_storage')
    columns = [(row['name'], row['type'], row['notnull'], row['pk'])
               for row in conn.execute(f'PRAGMA table_info({TABLE})')]
    _require(columns == [('predecessor_id', 'TEXT', 1, 1), ('successor_id', 'TEXT', 1, 2),
                         ('record', 'TEXT', 1, 0), ('sha256', 'TEXT', 1, 0)],
             'incomplete_dependency_storage')
    return True


def _owner(conn):
    row = conn.execute("SELECT i.identity_id FROM agent_identity i JOIN agents a ON a.id=i.agent_id "
                       "WHERE a.id='owner' AND a.role='Owner'").fetchone()
    _require(row is not None, 'owner_identity_missing')
    return row['identity_id']


def _authority(conn, objective_id, settings):
    from eidolon_cli.organization_identity import _leader_enabled
    from eidolon_cli.organization_projects import validate_objective_projects

    assignment = conn.execute('SELECT * FROM objective_assignments WHERE objective_id=?',
                              (objective_id,)).fetchone()
    _require(assignment is not None, 'objective_assignment_missing')
    leaders = {}
    for field, role, capabilities in (
        ('manager_id', 'Manager', {'request.plan', 'request.integrate'}),
        ('executive_id', 'Executive', {'request.accept'}),
    ):
        row = conn.execute('SELECT a.*,i.identity_id FROM agents a JOIN agent_identity i ON i.agent_id=a.id '
                           'WHERE a.id=?', (assignment[field],)).fetchone()
        _require(row is not None and row['role'] == role and _leader_enabled(conn, row)
                 and capabilities.issubset(json.loads(row['accepts'])), 'authority_unavailable')
        leaders[role.lower()] = {key: row[key] for key in ('id', 'identity_id', 'team', 'manager_id')}
    _require(leaders['manager']['manager_id'] == leaders['executive']['id'], 'authority_mismatch')
    projects = validate_objective_projects(conn, objective_id, settings)
    return {'leaders': leaders, 'projects': projects}


def _current_record(conn, predecessor_id, successor_id, owner_identity_id, manager_id):
    from eidolon_cli.organization_policy import persisted_settings

    _require(_owner(conn) == owner_identity_id, 'owner_identity_changed')
    settings = persisted_settings(conn)
    _require(settings is not None, 'authority_unavailable')
    _require(not conn.execute('SELECT 1 FROM organization_policy_pause WHERE id=1').fetchone(),
             'authority_unavailable')
    predecessor_authority = _authority(conn, predecessor_id, settings)
    successor_authority = _authority(conn, successor_id, settings)
    _require(predecessor_authority['projects'] == successor_authority['projects'], 'cross_project_dependency')
    _require(predecessor_authority['leaders'] == successor_authority['leaders']
             and successor_authority['leaders']['manager']['id'] == manager_id, 'authority_mismatch')
    successor, projects, scope_sha256 = _objective(conn, successor_id)
    _require(not successor['cancelled'], 'successor_cancelled')
    _require(projects == successor_authority['projects'], 'authority_mismatch')
    receipt = accepted_dependency_receipt(conn, predecessor_id)
    _require(receipt is not None, 'predecessor_not_accepted')
    return {'schemaVersion': 1, 'ownerIdentityId': owner_identity_id,
            'predecessor': {'objectiveId': predecessor_id, 'receiptSha256': receipt.sha256,
                            'receipt': receipt.to_dict()},
            'successor': {'objectiveId': successor_id, 'scopeSha256': scope_sha256},
            'authority': successor_authority}


def _records(conn, successor_id=None):
    records = {}
    query, arguments = f'SELECT * FROM {TABLE} ORDER BY predecessor_id,successor_id', ()
    if successor_id is not None:
        query = (f'WITH RECURSIVE ancestors(id) AS (SELECT ? UNION SELECT d.predecessor_id FROM {TABLE} d '
                 f'JOIN ancestors a ON d.successor_id=a.id) SELECT d.* FROM {TABLE} d '
                 'JOIN ancestors a ON d.successor_id=a.id ORDER BY d.predecessor_id,d.successor_id')
        arguments = (successor_id,)
    for row in conn.execute(query, arguments):
        record = json.loads(row['record'])
        key = (row['predecessor_id'], row['successor_id'])
        _require(digest(record) == row['sha256']
                 and record['predecessor']['objectiveId'] == key[0]
                 and record['successor']['objectiveId'] == key[1], 'corrupt_dependency_pin')
        records[key] = record
    return records


def _acyclic(records):
    # Iterative traversal also handles long retained histories without recursion.
    outgoing, indegree = {}, {}
    for predecessor, successor in records:
        _require(predecessor != successor, 'self_dependency')
        outgoing.setdefault(predecessor, []).append(successor)
        indegree.setdefault(predecessor, 0)
        indegree[successor] = indegree.get(successor, 0) + 1
    ready = [key for key, count in indegree.items() if count == 0]
    visited = 0
    while ready:
        predecessor = ready.pop()
        visited += 1
        for successor in outgoing.get(predecessor, ()):
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
    _require(visited == len(indegree), 'dependency_cycle')


def add_objective_dependency(conn, predecessor_id, successor_id, *, predecessor_receipt_sha256,
                             predecessor_receipt, successor_scope_sha256, owner_identity_id, manager_id):
    """Pin one authorized edge atomically inside the caller's write transaction.

    Identical valid input is idempotent. Any stale pin stays blocking; this API
    cannot replace it. Call from OrganizationStore._write, which uses BEGIN
    IMMEDIATE. A deferred caller is promoted to an actual SQLite writer before
    reading authority: in_transaction alone does not prove writer ownership.
    """
    _require(conn.in_transaction, 'write_transaction_required')
    for identifier in (predecessor_id, successor_id, owner_identity_id, manager_id):
        _identifier(identifier)
    _require(predecessor_id != successor_id, 'self_dependency')
    _require(_schema_available(conn), 'incomplete_dependency_storage')
    # No rows or immutable triggers are touched, but SQLite acquires its write
    # lock. Do not commit/rollback: admission and successor creation are atomic.
    conn.execute(f'UPDATE {TABLE} SET sha256=sha256 WHERE 0')
    record = _current_record(conn, predecessor_id, successor_id, owner_identity_id, manager_id)
    _require(isinstance(predecessor_receipt, dict)
             and _json(predecessor_receipt) == _json(record['predecessor']['receipt'])
             and predecessor_receipt_sha256 == record['predecessor']['receiptSha256'],
             'stale_predecessor_receipt')
    _require(successor_scope_sha256 == record['successor']['scopeSha256'], 'stale_successor_scope')
    records = _records(conn)
    key = (predecessor_id, successor_id)
    old = records.get(key)
    _require(old is None or _json(old) == _json(record), 'dependency_pin_conflict')
    records[key] = record
    _acyclic(records)
    sha256 = digest(record)
    if old is None:
        conn.execute(f'INSERT INTO {TABLE} VALUES (?,?,?,?)',
                     (*key, _json(record), sha256))
    return {**record, 'sha256': sha256}


@contextmanager
def _read_snapshot(conn):
    own_transaction = not conn.in_transaction
    if own_transaction:
        conn.execute('BEGIN')
    try:
        yield
    finally:
        if own_transaction:
            conn.rollback()


def objective_dependencies_satisfied(conn, successor_id):
    """Read-only claim fence. Missing legacy graph is allowed; bad pins block.

    Recomputes retained acceptance and both current authorities in one snapshot.
    Existing caller transactions are preserved; no migrations or filesystem
    reads occur. Claims still require all ordinary execution and budget guards.
    """
    try:
        _identifier(successor_id)
        with _read_snapshot(conn):
            _validated_records(conn, successor_id)
            return True
    except (ValueError, sqlite3.DatabaseError, KeyError, IndexError, TypeError, AttributeError):
        return False


def _validated_records(conn, successor_id):
    if not _schema_available(conn):
        return {}
    if not conn.execute(f'SELECT 1 FROM {TABLE} WHERE successor_id=? LIMIT 1', (successor_id,)).fetchone():
        return {}
    records = _records(conn, successor_id)
    _acyclic(records)
    for (predecessor, successor), record in records.items():
        current = _current_record(conn, predecessor, successor, record['ownerIdentityId'],
                                  record['authority']['leaders']['manager']['id'])
        _require(_json(current) == _json(record), 'stale_dependency_pin')
    return records


def accepted_dependency_context(conn, successor_id):
    """Project exact direct-predecessor final bodies as untrusted historical data.

    The complete relevant graph and acceptance receipts are revalidated in the
    same snapshot as the content read, including after an earlier claim check.
    Neither this projection nor its hashes become successor acceptance evidence.
    Limits apply to direct count and the whole serialized UTF-8 context; a large
    dependency requires a visible resolution rather than silent truncation.
    """
    try:
        _identifier(successor_id)
        with _read_snapshot(conn):
            records = _validated_records(conn, successor_id)
            direct = [record for (_, successor), record in records.items() if successor == successor_id]
            _require(len(direct) <= MAX_DEPENDENCY_CONTEXT_ITEMS, 'oversized_dependency_context')
            result = []
            for record in direct:
                predecessor = record['predecessor']
                finals = [artifact for artifact in predecessor['receipt']['artifacts']
                          if artifact['kind'] == 'integrated_deliverable']
                _require(len(finals) == 1, 'invalid_dependency_artifact')
                pin = finals[0]
                artifact = conn.execute('SELECT objective_id,content,sha256 FROM objective_deliverables WHERE id=?',
                                        (pin['id'],)).fetchone()
                _require(artifact is not None and artifact['objective_id'] == predecessor['objectiveId']
                         and artifact['sha256'] == pin['sha256']
                         and hashlib.sha256(artifact['content'].encode('utf-8')).hexdigest() == pin['sha256'],
                         'stale_dependency_artifact')
                result.append({'objectiveId': predecessor['objectiveId'], 'receiptSha256': predecessor['receiptSha256'],
                               'artifactId': pin['id'], 'artifactSha256': pin['sha256'], 'content': artifact['content'],
                               'historical': True, 'untrusted': True, 'liveSourceVerified': False})
                _require(len(_json(result).encode('utf-8')) <= MAX_DEPENDENCY_CONTEXT_BYTES,
                         'oversized_dependency_context')
            return result
    except ObjectiveDependencyError:
        raise
    except (ValueError, sqlite3.DatabaseError, KeyError, IndexError, TypeError, AttributeError) as error:
        raise ObjectiveDependencyError('invalid_dependency_context') from error

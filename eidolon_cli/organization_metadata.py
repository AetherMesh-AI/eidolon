"""Organization/owner display metadata, independent of execution-ledger initialization.

Callers own route authorization. IDs describe this ledger, never authority or a
cross-profile lookup key. Reads never construct an OrganizationStore or service.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import unicodedata
import uuid


_VERSION = 1
_TABLES = {
    'organization_metadata': ('singleton', 'schema_version', 'organization_id', 'display_name',
                              'owner_id', 'owner_display_name', 'revision', 'created_at', 'updated_at'),
    'organization_metadata_audit': ('organization_id', 'revision', 'idempotency_key', 'record_json', 'created_at'),
    'organization_metadata_receipts': ('idempotency_key', 'organization_id', 'input_hash', 'result_json'),
}
_DDL = (
    '''CREATE TABLE organization_metadata (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), schema_version INTEGER NOT NULL,
        organization_id TEXT NOT NULL, display_name TEXT NOT NULL,
        owner_id TEXT NOT NULL, owner_display_name TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision>0), created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''',
    '''CREATE TABLE organization_metadata_audit (
        organization_id TEXT NOT NULL, revision INTEGER NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
        record_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(organization_id, revision))''',
    '''CREATE TABLE organization_metadata_receipts (
        idempotency_key TEXT PRIMARY KEY, organization_id TEXT NOT NULL,
        input_hash TEXT NOT NULL, result_json TEXT NOT NULL)''',
)

# PRAGMA-visible constraints are part of the v1 storage contract, not just names.
_COLUMN_RULES = {
    'organization_metadata': (('INTEGER', 0, 1), ('INTEGER', 1, 0), ('TEXT', 1, 0),
                              ('TEXT', 1, 0), ('TEXT', 1, 0), ('TEXT', 1, 0),
                              ('INTEGER', 1, 0), ('TEXT', 1, 0), ('TEXT', 1, 0)),
    'organization_metadata_audit': (('TEXT', 1, 1), ('INTEGER', 1, 2), ('TEXT', 1, 0),
                                    ('TEXT', 1, 0), ('TEXT', 1, 0)),
    'organization_metadata_receipts': (('TEXT', 0, 1), ('TEXT', 1, 0), ('TEXT', 1, 0), ('TEXT', 1, 0)),
}
_UNIQUE_KEYS = {
    'organization_metadata': set(),  # INTEGER PRIMARY KEY is the rowid, not an index.
    'organization_metadata_audit': {('pk', ('organization_id', 'revision')), ('u', ('idempotency_key',))},
    'organization_metadata_receipts': {('pk', ('idempotency_key',))},
}


class MetadataError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f'Organization metadata: {reason}')


def _storage_error(exc: sqlite3.Error) -> MetadataError:
    code = getattr(exc, 'sqlite_errorcode', 0) & 255
    reason = {sqlite3.SQLITE_BUSY: 'locked', sqlite3.SQLITE_LOCKED: 'locked',
              sqlite3.SQLITE_CORRUPT: 'corrupt', sqlite3.SQLITE_NOTADB: 'corrupt'}.get(code, 'unreadable')
    return MetadataError(reason)


def _descriptor(state, *, reason=None, metadata=None):
    result = {'capabilityVersion': _VERSION, 'state': state, 'metadata': metadata}
    if reason:
        result['reason'] = reason
    return result


def _record(row):
    return {'organizationId': row['organization_id'], 'displayName': row['display_name'],
            'owner': {'ownerId': row['owner_id'], 'displayName': row['owner_display_name']},
            'revision': row['revision'], 'createdAt': row['created_at'], 'updatedAt': row['updated_at']}


def _compatible_table(conn, name, columns):
    fields = conn.execute(f'PRAGMA table_xinfo({name})').fetchall()
    if tuple(row['name'] for row in fields) != columns:
        return False
    if tuple((row['type'].upper(), row['notnull'], row['pk']) for row in fields) != _COLUMN_RULES[name]:
        return False
    if any(row['hidden'] or row['dflt_value'] is not None for row in fields):
        return False
    keys = set()
    for index in conn.execute('SELECT * FROM pragma_index_list(?)', (name,)):
        if not index['unique']:
            continue
        fields = conn.execute('SELECT * FROM pragma_index_xinfo(?) WHERE key=1 ORDER BY seqno',
                              (index['name'],)).fetchall()
        if index['partial'] or any(row['cid'] < 0 or row['coll'] != 'BINARY' or row['desc'] for row in fields):
            return False
        keys.add((index['origin'], tuple(row['name'] for row in fields)))
    if keys != _UNIQUE_KEYS[name]:
        return False
    # SQLite exposes keys through PRAGMA, but not CHECK expressions. These are
    # the v1 writer's two fixed clauses; unknown spellings fail closed.
    sql = conn.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()[0]
    compact = ''.join(sql.lower().split())
    checks = ('check(singleton=1)', 'check(revision>0)') if name == 'organization_metadata' else ()
    return compact.count('check(') == len(checks) and all(check in compact for check in checks)


def _validate_record(record):
    try:
        if not isinstance(record, dict) or set(record) != {
                'organizationId', 'displayName', 'owner', 'revision', 'createdAt', 'updatedAt'}:
            raise ValueError('record')
        owner = record['owner']
        if not isinstance(owner, dict) or set(owner) != {'ownerId', 'displayName'}:
            raise ValueError('owner')
        _uuid(record['organizationId'], 'organizationId')
        _uuid(owner['ownerId'], 'ownerId')
        if (_text(record['displayName'], 'displayName', 160) != record['displayName']
                or _text(owner['displayName'], 'ownerDisplayName', 160) != owner['displayName']):
            raise ValueError('name')
        if type(record['revision']) is not int or record['revision'] < 1:
            raise ValueError('revision')
        for field in ('createdAt', 'updatedAt'):
            if datetime.fromisoformat(record[field]).tzinfo is None:
                raise ValueError('timestamp')
    except (ValueError, TypeError):
        raise MetadataError('corrupt') from None
    return record


def _receipt_result(receipt, metadata):
    try:
        if not isinstance(receipt['result_json'], str):
            raise ValueError('receipt')
        result = json.loads(receipt['result_json'])
        if (not isinstance(result, dict) or set(result) != {'capabilityVersion', 'state', 'metadata'}
                or type(result['capabilityVersion']) is not int or result['capabilityVersion'] != _VERSION
                or result['state'] != 'ready'):
            raise ValueError('receipt')
        record = _validate_record(result['metadata'])
        if (record['organizationId'] != metadata['organizationId']
                or record['owner']['ownerId'] != metadata['owner']['ownerId']
                or record['createdAt'] != metadata['createdAt'] or record['revision'] > metadata['revision']):
            raise ValueError('receipt identity/revision')
        return result
    except (ValueError, TypeError, RecursionError):
        raise MetadataError('corrupt') from None


def _inspect(conn):
    objects = dict(conn.execute("SELECT name,type FROM sqlite_master WHERE name LIKE 'organization_metadata%'"))
    present = set(objects) & _TABLES.keys()
    if not present:
        return _descriptor('unconfigured', reason='table_missing')
    if present != _TABLES.keys():
        return _descriptor('unsupported', reason='incomplete_schema')
    for name, columns in _TABLES.items():
        if objects[name] != 'table' or not _compatible_table(conn, name, columns):
            return _descriptor('unsupported', reason='schema')
    rows = conn.execute('SELECT * FROM organization_metadata').fetchall()
    if not rows:
        if any(conn.execute(f'SELECT 1 FROM {name} LIMIT 1').fetchone()
               for name in ('organization_metadata_audit', 'organization_metadata_receipts')):
            raise MetadataError('corrupt')
        return _descriptor('unconfigured', reason='row_missing')
    if len(rows) != 1 or rows[0]['singleton'] != 1:
        raise MetadataError('corrupt')
    row = rows[0]
    if row['schema_version'] != _VERSION:
        return _descriptor('unsupported', reason='schema_version')
    return _descriptor('ready', metadata=_validate_record(_record(row)))


def read_descriptor(path: Path) -> dict:
    """Read committed metadata using SQLite's read-only, WAL-aware connection.

    No immutable shortcut: it could ignore a committed WAL and report stale IDs.
    SQLite may maintain transient read-lock bookkeeping in WAL sidecars;
    this path performs no schema, row, journal-mode or execution-state writes.
    """
    path = Path(path)
    try:
        path.stat()
    except FileNotFoundError:
        return _descriptor('unconfigured', reason='database_missing')
    except OSError:
        raise MetadataError('unreadable') from None
    try:
        with closing(sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True,
                                     timeout=0.25, isolation_level=None)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')
            return _inspect(conn)
    except sqlite3.Error as exc:
        raise _storage_error(exc) from exc


def _text(value, name, limit):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(unicodedata.category(char).startswith('C') for char in value)):
        raise ValueError(f'{name} must be nonempty text of at most {limit} characters without controls')
    return value.strip()


def _uuid(value, name):
    if not isinstance(value, str):
        raise ValueError(f'{name} must be a canonical UUID')
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
    except ValueError:
        raise ValueError(f'{name} must be a canonical UUID') from None
    return value


def _save_in_transaction(conn, payload, key, input_hash):
    current = _inspect(conn)
    if current['state'] == 'unsupported':
        raise MetadataError('unsupported_schema')
    metadata = current['metadata']
    if current.get('reason') != 'table_missing':
        receipt = conn.execute('SELECT * FROM organization_metadata_receipts WHERE idempotency_key=?', (key,)).fetchone()
        if receipt:
            if receipt['input_hash'] != input_hash:
                raise MetadataError('idempotency_conflict')
            if not metadata or receipt['organization_id'] != metadata['organizationId']:
                raise MetadataError('identity_conflict')
            return _receipt_result(receipt, metadata)
    expected_id, expected_revision = payload['expectedOrganizationId'], payload['expectedRevision']
    if metadata:
        if metadata['organizationId'] != expected_id:
            raise MetadataError('identity_conflict')
        if metadata['revision'] != expected_revision:
            raise MetadataError('revision_conflict')
    elif expected_id is not None or expected_revision is not None:
        raise MetadataError('initialization_conflict')
    if current.get('reason') == 'table_missing':
        for sql in _DDL:
            conn.execute(sql)  # executescript would commit before the transaction finishes.
    now = datetime.now(timezone.utc).isoformat()
    record = {'organizationId': metadata['organizationId'] if metadata else str(uuid.uuid4()),
              'displayName': payload['displayName'],
              'owner': {'ownerId': metadata['owner']['ownerId'] if metadata else str(uuid.uuid4()),
                        'displayName': payload['ownerDisplayName']},
              'revision': metadata['revision'] + 1 if metadata else 1,
              'createdAt': metadata['createdAt'] if metadata else now, 'updatedAt': now}
    conn.execute('INSERT OR REPLACE INTO organization_metadata VALUES (1,?,?,?,?,?,?,?,?)',
                 (_VERSION, record['organizationId'], record['displayName'], record['owner']['ownerId'],
                  record['owner']['displayName'], record['revision'], record['createdAt'], record['updatedAt']))
    result = _descriptor('ready', metadata=record)
    encoded = json.dumps(result, sort_keys=True, ensure_ascii=False)
    conn.execute('INSERT INTO organization_metadata_audit VALUES (?,?,?,?,?)',
                 (record['organizationId'], record['revision'], key, encoded, now))
    conn.execute('INSERT INTO organization_metadata_receipts VALUES (?,?,?,?)',
                 (key, record['organizationId'], input_hash, encoded))
    return result


def save_metadata(path: Path, *, display_name: str, owner_display_name: str,
                  expected_organization_id: str | None, expected_revision: int | None,
                  idempotency_key: str) -> dict:
    """Explicit initialization or CAS update; the original receipt wins on retry."""
    payload = {'displayName': _text(display_name, 'displayName', 160),
               'ownerDisplayName': _text(owner_display_name, 'ownerDisplayName', 160),
               'expectedOrganizationId': expected_organization_id, 'expectedRevision': expected_revision}
    key = _text(idempotency_key, 'idempotencyKey', 200)
    if expected_organization_id is None:
        if expected_revision is not None:
            raise ValueError('Initialization requires null organization ID and revision')
    else:
        _uuid(expected_organization_id, 'expectedOrganizationId')
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError('expectedRevision must be a positive integer')
    input_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    path = Path(path)
    try:
        if expected_organization_id is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            mode = 'rwc'
        else:
            try:
                path.stat()
            except FileNotFoundError:
                raise MetadataError('initialization_conflict') from None
            mode = 'rw'  # A stale update cannot provision a replacement database.
        with closing(sqlite3.connect(path.absolute().as_uri() + '?mode=' + mode, uri=True,
                                     timeout=5, isolation_level=None)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('BEGIN IMMEDIATE')
            try:
                result = _save_in_transaction(conn, payload, key, input_hash)
                conn.commit()
                return result
            except BaseException:
                conn.rollback()
                raise
    except sqlite3.Error as exc:
        raise _storage_error(exc) from exc
    except OSError:
        raise MetadataError('unreadable') from None

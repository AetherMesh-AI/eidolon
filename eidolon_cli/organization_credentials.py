"""Organization credential metadata and independent, exact capability grants.

Only metadata is persisted here. Values pass through SecureInputs to a secure
backend; neither a catalog name nor a grant enables environment injection.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

from eidolon_cli.organization_credential_security import (
    CredentialError, PERMISSIONS, Principal, SecureInputs, UnavailableBackend,
    ValueBinding, component, create_scope, identifier, metadata_fields,
)

_SCHEMA = '''
CREATE TABLE IF NOT EXISTS credential_identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), organization_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS credential_entries (
 id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, metadata TEXT NOT NULL,
 revision INTEGER NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL,
 backend_ref TEXT, value_binding TEXT, created REAL NOT NULL, updated REAL NOT NULL, actor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS credential_grants (
 actor TEXT NOT NULL, scope TEXT NOT NULL, revision INTEGER NOT NULL,
 permissions TEXT NOT NULL, bindings TEXT NOT NULL, PRIMARY KEY(actor,scope));
CREATE TABLE IF NOT EXISTS credential_receipts (
 operation_id TEXT PRIMARY KEY, actor TEXT NOT NULL, input_hash TEXT NOT NULL, result TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS credential_cancelled_operations (
 actor TEXT NOT NULL, operation_id TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(actor,operation_id));
CREATE TABLE IF NOT EXISTS credential_audit (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL, actor TEXT NOT NULL,
 action TEXT NOT NULL, entry_id TEXT, revision INTEGER, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS credential_uses (
 id TEXT PRIMARY KEY, entry_id TEXT NOT NULL, version INTEGER NOT NULL, actor TEXT NOT NULL,
 tool TEXT NOT NULL, destination TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL, finished REAL);
CREATE TRIGGER IF NOT EXISTS credential_cancelled_no_update BEFORE UPDATE ON credential_cancelled_operations BEGIN SELECT RAISE(ABORT,'Immutable cancellation'); END;
CREATE TRIGGER IF NOT EXISTS credential_cancelled_no_delete BEFORE DELETE ON credential_cancelled_operations BEGIN SELECT RAISE(ABORT,'Immutable cancellation'); END;
CREATE TRIGGER IF NOT EXISTS credential_audit_no_update BEFORE UPDATE ON credential_audit BEGIN SELECT RAISE(ABORT,'Immutable credential audit'); END;
CREATE TRIGGER IF NOT EXISTS credential_audit_no_delete BEFORE DELETE ON credential_audit BEGIN SELECT RAISE(ABORT,'Immutable credential audit'); END;
CREATE TRIGGER IF NOT EXISTS credential_receipts_no_update BEFORE UPDATE ON credential_receipts BEGIN SELECT RAISE(ABORT,'Immutable credential receipt'); END;
CREATE TRIGGER IF NOT EXISTS credential_receipts_no_delete BEFORE DELETE ON credential_receipts BEGIN SELECT RAISE(ABORT,'Immutable credential receipt'); END;
'''


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _fingerprint(value):
    # Metadata and opaque operation identity only; never hash credential bytes.
    return hashlib.sha256(_json(value).encode()).hexdigest()


def entry_identifier(organization_id, operation_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'eidolon:credential:' + organization_id + ':' + operation_id))


class CredentialCatalog:
    def __init__(self, path: Path, organization_id: str, *, backend=None, inputs=None, clock=time.time):
        self.path = Path(path)
        self.organization_id = identifier(organization_id, 'organization')
        self.backend = backend or UnavailableBackend()
        self.inputs = inputs or SecureInputs()
        self.clock = clock

    def _principal(self, principal):
        if not isinstance(principal, Principal) or principal.organization_id != self.organization_id:
            raise CredentialError('scope_denied')
        identifier(principal.actor_id, 'actor')

    @contextmanager
    def _connection(self, *, write=False):
        if self.path.resolve() != self.path.absolute():
            raise CredentialError('storage_unavailable')
        if not write and not self.path.exists():
            yield None
            return
        try:
            if write:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path if write else self.path.as_uri() + '?mode=ro',
                                         uri=not write, timeout=2, isolation_level=None)
            connection.row_factory = sqlite3.Row
            try:
                if write:
                    connection.executescript('BEGIN IMMEDIATE;\n' + _SCHEMA)
                    connection.execute('INSERT OR IGNORE INTO credential_identity VALUES (1,?)', (self.organization_id,))
                else:
                    connection.execute('PRAGMA query_only=ON')
                    connection.execute('BEGIN')
                identity = connection.execute('SELECT organization_id FROM credential_identity WHERE singleton=1').fetchone()
                if identity is None or identity[0] != self.organization_id:
                    raise CredentialError('scope_denied')
                yield connection
                if write:
                    connection.commit()
            finally:
                connection.close()
        except sqlite3.Error:
            raise CredentialError('storage_unavailable') from None

    def _allowed(self, conn, principal, permission, scope, *, tool=None, destination=None):
        self._principal(principal)
        if principal.owner:
            return True
        if conn is None:
            return False
        grant = conn.execute('SELECT * FROM credential_grants WHERE actor=? AND scope=?', (principal.actor_id, scope)).fetchone()
        if not grant or permission not in json.loads(grant['permissions']):
            return False
        return permission != 'use' or {'tool': tool, 'destination': destination} in json.loads(grant['bindings'])

    def _require(self, conn, principal, permission, scope, **kwargs):
        if not self._allowed(conn, principal, permission, scope, **kwargs):
            raise CredentialError('permission_denied')

    def _row(self, conn, entry_id):
        identifier(entry_id, 'entry')
        row = conn.execute('SELECT * FROM credential_entries WHERE id=?', (entry_id,)).fetchone() if conn else None
        if row is None:
            raise CredentialError('entry_missing')
        return row

    def _public(self, conn, row, principal):
        fields = json.loads(row['metadata'])
        state = row['status']
        if state == 'active':
            if fields['expiresAt'] is not None and fields['expiresAt'] <= self.clock():
                state = 'expired'
            elif not row['backend_ref']:
                state = 'missing'
            else:
                state = self.backend.status().state
        return {'id': row['id'], 'organizationId': self.organization_id, **fields,
                'revision': row['revision'], 'version': row['version'], 'status': state,
                'createdAt': row['created'], 'updatedAt': row['updated'], 'updatedBy': row['actor'],
                'permissions': self._entry_permissions(conn, principal, row['id']),
                'inFlight': conn.execute("SELECT count(*) FROM credential_uses WHERE entry_id=? AND state='started'", (row['id'],)).fetchone()[0]}

    def _entry_permissions(self, conn, principal, entry_id):
        if principal.owner:
            return sorted(PERMISSIONS - {'create'})
        row = conn.execute('SELECT permissions FROM credential_grants WHERE actor=? AND scope=?',
                           (principal.actor_id, entry_id)).fetchone()
        return json.loads(row[0]) if row else []

    def granted_scopes(self, principal):
        self._principal(principal)
        with self._connection() as conn:
            return [{'scope': row['scope'], 'permissions': json.loads(row['permissions'])}
                    for row in conn.execute('SELECT scope,permissions FROM credential_grants WHERE actor=?',
                                            (principal.actor_id,)) if json.loads(row['permissions'])] if conn else []

    def discover(self, principal):
        self._principal(principal)
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM credential_entries WHERE status!='removed' ORDER BY name").fetchall() if conn else []
            return {'version': 1, 'organizationId': self.organization_id, 'backend': self.backend.status().public(),
                    'entries': [self._public(conn, row, principal) for row in rows
                        if self._allowed(conn, principal, 'discover', row['id'])],
                    'canCreate': principal.owner, 'environmentInjection': False, 'providerCredentialCreation': False}

    @staticmethod
    def _cancelled(conn, principal, operation_id):
        if conn is None or not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='credential_cancelled_operations'").fetchone():
            return False
        return bool(conn.execute('SELECT 1 FROM credential_cancelled_operations WHERE actor=? AND operation_id=?',
            (principal.actor_id, operation_id)).fetchone())

    def _receipt(self, conn, principal, operation_id, payload):
        identifier(operation_id, 'operation')
        if self._cancelled(conn, principal, operation_id):
            raise CredentialError('operation_cancelled')
        prior = conn.execute('SELECT * FROM credential_receipts WHERE operation_id=?', (operation_id,)).fetchone()
        if prior:
            if prior['actor'] != principal.actor_id or prior['input_hash'] != _fingerprint(payload):
                raise CredentialError('idempotency_conflict')
            return json.loads(prior['result'])
        return None

    def _finish(self, conn, principal, operation_id, action, payload, result, entry_id=None, revision=None):
        conn.execute('INSERT INTO credential_receipts VALUES (?,?,?,?)',
                     (operation_id, principal.actor_id, _fingerprint(payload), _json(result)))
        conn.execute('INSERT INTO credential_audit(operation_id,actor,action,entry_id,revision,created) VALUES (?,?,?,?,?,?)',
                     (operation_id, principal.actor_id, action, entry_id, revision, self.clock()))
        return result

    def receipt(self, principal, operation_id):
        self._principal(principal)
        identifier(operation_id, 'operation')
        with self._connection() as conn:
            row = conn.execute('SELECT * FROM credential_receipts WHERE operation_id=? AND actor=?',
                               (operation_id, principal.actor_id)).fetchone() if conn else None
            return json.loads(row['result']) if row else None

    def cancel_operation(self, principal, operation_id):
        """Fence delayed owner mutations; a missing read receipt alone is unsafe."""
        self._principal(principal)
        if not principal.owner:
            raise CredentialError('owner_required')
        identifier(operation_id, 'operation')
        with self._connection(write=True) as conn:
            row = conn.execute('SELECT actor,result FROM credential_receipts WHERE operation_id=?', (operation_id,)).fetchone()
            if row:
                if row['actor'] != principal.actor_id:
                    raise CredentialError('permission_denied')
                return {'operationId': operation_id, 'state': 'committed', 'receipt': json.loads(row['result'])}
            created = conn.execute('INSERT OR IGNORE INTO credential_cancelled_operations VALUES (?,?,?)',
                (principal.actor_id, operation_id, self.clock())).rowcount
            if created:
                conn.execute('INSERT INTO credential_audit(operation_id,actor,action,created) VALUES (?,?,?,?)',
                    (operation_id, principal.actor_id, 'cancel_operation', self.clock()))
            return {'operationId': operation_id, 'state': 'cancelled', 'receipt': None}

    def create(self, principal, fields, *, operation_id):
        self._principal(principal)
        fields = metadata_fields(fields)
        scope = 'create:' + create_scope(fields)
        payload = {'action': 'create', 'metadata': fields}
        with self._connection(write=True) as conn:
            self._require(conn, principal, 'create', scope)
            prior = self._receipt(conn, principal, operation_id, payload)
            if prior:
                return prior
            entry_id = entry_identifier(self.organization_id, operation_id)
            if conn.execute('SELECT 1 FROM credential_entries WHERE name=?', (fields['name'],)).fetchone():
                raise CredentialError('name_conflict')
            now = self.clock()
            conn.execute('INSERT INTO credential_entries VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                         (entry_id, fields['name'], _json(fields), 1, 0, 'active', None, None, now, now, principal.actor_id))
            result = {'entryId': entry_id, 'revision': 1, 'version': 0, 'operationId': operation_id, 'action': 'create'}
            return self._finish(conn, principal, operation_id, 'create', payload, result, entry_id, 1)

    def edit_metadata(self, principal, entry_id, fields, *, expected_revision, operation_id):
        self._principal(principal)
        fields = metadata_fields(fields)
        payload = {'action': 'edit_metadata', 'entryId': entry_id, 'metadata': fields, 'expectedRevision': expected_revision}
        with self._connection(write=True) as conn:
            self._require(conn, principal, 'edit_metadata', entry_id)
            prior = self._receipt(conn, principal, operation_id, payload)
            if prior:
                return prior
            row = self._current(conn, entry_id, expected_revision)
            # Metadata editors may rename/descript, not change secret classification or extend expiry.
            previous = json.loads(row['metadata'])
            if fields['kind'] != previous['kind'] or fields['expiresAt'] != previous['expiresAt']:
                raise CredentialError('classification_change_denied')
            if conn.execute('SELECT 1 FROM credential_entries WHERE name=? AND id!=?', (fields['name'], entry_id)).fetchone():
                raise CredentialError('name_conflict')
            revision = row['revision'] + 1
            conn.execute('UPDATE credential_entries SET name=?,metadata=?,revision=?,updated=?,actor=? WHERE id=?',
                         (fields['name'], _json(fields), revision, self.clock(), principal.actor_id, entry_id))
            result = {'entryId': entry_id, 'revision': revision, 'version': row['version'], 'operationId': operation_id, 'action': 'edit_metadata'}
            return self._finish(conn, principal, operation_id, 'edit_metadata', payload, result, entry_id, revision)

    def _current(self, conn, entry_id, revision):
        row = self._row(conn, entry_id)
        if type(revision) is not int or row['revision'] != revision:
            raise CredentialError('revision_conflict')
        if row['status'] != 'active':
            raise CredentialError('entry_revoked')
        return row

    def value_binding(self, principal, entry_id, *, expected_revision, operation_id):
        self._principal(principal)
        identifier(operation_id, 'operation')
        with self._connection() as conn:
            self._require(conn, principal, 'replace_value', entry_id)
            if self._cancelled(conn, principal, operation_id):
                raise CredentialError('operation_cancelled')
            row = self._current(conn, entry_id, expected_revision)
            if self.backend.status().state != 'available':
                raise CredentialError('backend_' + self.backend.status().state)
            return ValueBinding(self.organization_id, entry_id, row['version'] + 1, principal.actor_id, operation_id)

    def replace_value(self, principal, entry_id, *, expected_revision, operation_id, input_handle, transport):
        self._principal(principal)
        identifier(input_handle, 'input_handle')
        with self._connection(write=True) as conn:
            return self._replace_value(conn, principal, entry_id, expected_revision=expected_revision,
                operation_id=operation_id, input_handle=input_handle, transport=transport)

    def _replace_value(self, conn, principal, entry_id, *, expected_revision, operation_id,
                       input_handle, transport, guard=None):
        self._principal(principal)
        identifier(input_handle, 'input_handle')
        payload = {'action': 'replace_value', 'entryId': entry_id, 'expectedRevision': expected_revision,
                   'inputHandleId': input_handle}  # Random handle identity, not a value fingerprint.
        self._require(conn, principal, 'replace_value', entry_id)
        prior = self._receipt(conn, principal, operation_id, payload)
        if prior:
            return prior
        row = self._current(conn, entry_id, expected_revision)
        binding = ValueBinding(self.organization_id, entry_id, row['version'] + 1, principal.actor_id, operation_id)
        if self.backend.status().state != 'available':
            raise CredentialError('backend_' + self.backend.status().state)
        reference = self.inputs.consume(input_handle, binding, transport, lambda value: self.backend.put(binding, value), guard=guard)
        revision = row['revision'] + 1
        conn.execute('UPDATE credential_entries SET revision=?,version=?,backend_ref=?,value_binding=?,updated=?,actor=? WHERE id=?',
                     (revision, binding.version, reference, _json(asdict(binding)), self.clock(), principal.actor_id, entry_id))
        result = {'entryId': entry_id, 'revision': revision, 'version': binding.version, 'operationId': operation_id, 'action': 'replace_value'}
        return self._finish(conn, principal, operation_id, 'replace_value', payload, result, entry_id, revision)

    def revoke(self, principal, entry_id, *, expected_revision, operation_id, remove=False):
        self._principal(principal)
        action = 'remove' if remove else 'revoke'
        payload = {'action': action, 'entryId': entry_id, 'expectedRevision': expected_revision}
        with self._connection(write=True) as conn:
            self._require(conn, principal, action, entry_id)
            prior = self._receipt(conn, principal, operation_id, payload)
            if prior:
                return prior
            row = self._row(conn, entry_id)
            if type(expected_revision) is not int or row['revision'] != expected_revision:
                raise CredentialError('revision_conflict')
            if row['status'] == 'removed' or (remove and row['status'] != 'revoked'):
                raise CredentialError('revoke_first')
            revision = row['revision'] + 1
            conn.execute('UPDATE credential_entries SET status=?,revision=?,updated=?,actor=? WHERE id=?',
                         ('removed' if remove else 'revoked', revision, self.clock(), principal.actor_id, entry_id))
            queued = conn.execute("UPDATE credential_uses SET state='cancelled',finished=? WHERE entry_id=? AND state IN ('queued','resolving')", (self.clock(), entry_id)).rowcount
            count = conn.execute("SELECT count(*) FROM credential_uses WHERE entry_id=? AND state='started'", (entry_id,)).fetchone()[0]
            result = {'entryId': entry_id, 'revision': revision, 'version': row['version'], 'operationId': operation_id,
                      'action': action, 'inFlight': count, 'queuedCancelled': queued, 'providerRevoked': False,
                      'storedValueDeleted': False}
            return self._finish(conn, principal, operation_id, action, payload, result, entry_id, revision)

    def configure_grant(self, principal, *, actor_id, scope, permissions, bindings, expected_revision, operation_id):
        self._principal(principal)
        if not principal.owner:
            raise CredentialError('owner_required')
        identifier(actor_id, 'actor')
        if not isinstance(scope, str) or not scope or len(scope) > 200:
            raise CredentialError('invalid_scope')
        if (not isinstance(permissions, list) or any(not isinstance(item, str) for item in permissions)
                or len(set(permissions)) != len(permissions) or not set(permissions) <= PERMISSIONS):
            raise CredentialError('invalid_permissions')
        if not isinstance(bindings, list) or len(bindings) > 32:
            raise CredentialError('invalid_bindings')
        for binding in bindings:
            if not isinstance(binding, dict) or set(binding) != {'tool', 'destination'}:
                raise CredentialError('invalid_bindings')
            for key, value in binding.items():
                identifier(value, key)
        if ('use' in permissions) != bool(bindings):
            raise CredentialError('invalid_bindings')
        if permissions and (('create' in permissions) != scope.startswith('create:') or (scope.startswith('create:') and permissions != ['create'])):
            raise CredentialError('invalid_scope')
        if scope.startswith('create:'):
            parts = scope.split(':')
            if len(parts) != 5 or parts[-1] not in {'config', 'secret'}:
                raise CredentialError('invalid_scope')
            if any(component(part) != part for part in parts[1:4]):
                raise CredentialError('invalid_scope')
        payload = {'action': 'configure_grant', 'actorId': actor_id, 'scope': scope, 'permissions': sorted(permissions),
                   'bindings': bindings, 'expectedRevision': expected_revision}
        with self._connection(write=True) as conn:
            prior = self._receipt(conn, principal, operation_id, payload)
            if prior:
                return prior
            if not scope.startswith('create:'):
                self._row(conn, scope)
            row = conn.execute('SELECT revision FROM credential_grants WHERE actor=? AND scope=?', (actor_id, scope)).fetchone()
            current = row[0] if row else 0
            if type(expected_revision) is not int or current != expected_revision:
                raise CredentialError('revision_conflict')
            conn.execute('INSERT OR REPLACE INTO credential_grants VALUES (?,?,?,?,?)',
                         (actor_id, scope, current + 1, _json(sorted(permissions)), _json(bindings)))
            result = {'actorId': actor_id, 'scope': scope, 'revision': current + 1, 'operationId': operation_id, 'action': 'configure_grant'}
            return self._finish(conn, principal, operation_id, 'configure_grant', payload, result)

    def owner_details(self, principal):
        self._principal(principal)
        if not principal.owner:
            raise CredentialError('owner_required')
        result = self.discover(principal)
        with self._connection() as conn:
            result['grants'] = [dict(row, permissions=json.loads(row['permissions']), bindings=json.loads(row['bindings']))
                                for row in conn.execute('SELECT * FROM credential_grants ORDER BY actor,scope')] if conn else []
            result['audit'] = [dict(row) for row in conn.execute('SELECT * FROM credential_audit ORDER BY sequence DESC LIMIT 100')] if conn else []
            result['tombstones'] = [{'id': row['id'], 'revision': row['revision'], 'status': row['status']}
                for row in conn.execute("SELECT * FROM credential_entries WHERE status='removed'")] if conn else []
        return result

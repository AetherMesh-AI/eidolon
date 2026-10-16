"""Owner-prepared replacement for one live, scheduler-issued worker generation.

The durable handoff is an authorization receipt, not a value bearer. Its input
exists only in SecureInputs, bound to the original owner transport and capsule.
Lock order is worker lease, catalog writer, registry/input. Cancellation never
needs the worker lease, and no registry lock is held while opening the catalog.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import threading
import uuid
import weakref

from eidolon_cli.organization_credential_security import CredentialError, ValueBinding, identifier
from eidolon_cli.organization_credentials import _fingerprint


_TABLE = 'credential_replacement_handoffs'
_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS credential_replacement_handoffs (
        owner_operation_id TEXT PRIMARY KEY, owner_actor TEXT NOT NULL,
        handoff_id TEXT NOT NULL UNIQUE, worker_operation_id TEXT NOT NULL UNIQUE,
        actor TEXT NOT NULL, entry_id TEXT NOT NULL, expected_revision INTEGER NOT NULL,
        next_version INTEGER NOT NULL, grant_revision INTEGER NOT NULL,
        request_id TEXT NOT NULL, generation TEXT NOT NULL,
        created REAL NOT NULL, expires REAL NOT NULL, replacement_hash TEXT NOT NULL)''',
    '''CREATE TRIGGER IF NOT EXISTS credential_handoffs_no_update
        BEFORE UPDATE ON credential_replacement_handoffs
        BEGIN SELECT RAISE(ABORT,'Immutable credential handoff'); END''',
    '''CREATE TRIGGER IF NOT EXISTS credential_handoffs_no_delete
        BEFORE DELETE ON credential_replacement_handoffs
        BEGIN SELECT RAISE(ABORT,'Immutable credential handoff'); END''',
)


@dataclass(frozen=True, repr=False)
class _Prepared:
    capsule: weakref.ReferenceType
    catalog: object
    binding: ValueBinding
    handle: str
    transport: object
    transport_guard: object
    expires: float


def _owner(catalog, principal):
    catalog._principal(principal)
    if not principal.owner:
        raise CredentialError('owner_required')


def _key(capsule):
    return (str(capsule.catalog.path.resolve()), capsule.principal.actor_id,
            capsule.request_id, capsule.generation)


def _schema(conn):
    # executescript would implicitly commit the caller's authorization transaction.
    for statement in _SCHEMA:
        conn.execute(statement)


def _has_table(conn):
    return bool(conn and conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,)).fetchone())


def _payload(entry_id, expected_revision, actor_id, request_id, generation, grant_revision):
    return {'action': 'prepare_replacement', 'entryId': entry_id,
            'expectedRevision': expected_revision, 'actorId': actor_id,
            'requestId': request_id, 'generation': generation, 'grantRevision': grant_revision}


def _receipt(row):
    return {**_payload(row['entry_id'], row['expected_revision'], row['actor'],
                      row['request_id'], row['generation'], row['grant_revision']),
            'handoffId': row['handoff_id'], 'operationId': row['owner_operation_id'],
            'workerOperationId': row['worker_operation_id'], 'nextVersion': row['next_version'],
            'grantRevision': row['grant_revision'], 'createdAt': row['created'],
            'expiresAt': row['expires']}


def _current(catalog, conn, entry_id, expected_revision):
    row = catalog._current(conn, entry_id, expected_revision)
    expiry = json.loads(row['metadata'])['expiresAt']
    if expiry is not None and expiry <= catalog.clock():
        raise CredentialError('entry_expired')
    return row


def _grant(catalog, conn, capsule, entry_id, revision=None):
    catalog._principal(capsule.principal)
    if capsule.principal.owner:
        raise CredentialError('permission_denied')
    catalog._require(conn, capsule.principal, 'replace_value', entry_id)
    grant = conn.execute('SELECT revision FROM credential_grants WHERE actor=? AND scope=?',
                         (capsule.principal.actor_id, entry_id)).fetchone()
    if revision is not None and grant['revision'] != revision:
        raise CredentialError('grant_revision_conflict')
    return grant['revision']


def _committed(conn, row):
    prior = conn.execute('SELECT * FROM credential_receipts WHERE operation_id=?',
                         (row['worker_operation_id'],)).fetchone()
    if prior is None:
        return None
    expected = {'entryId': row['entry_id'], 'revision': row['expected_revision'] + 1,
                'version': row['next_version'], 'operationId': row['worker_operation_id'],
                'action': 'replace_value'}
    if (prior['actor'] != row['actor'] or prior['input_hash'] != row['replacement_hash']
            or json.loads(prior['result']) != expected):
        raise CredentialError('idempotency_conflict')
    return expected


class ReplacementHandoffs:
    def __init__(self):
        self._lock = threading.Lock()
        self._capsules = weakref.WeakValueDictionary()
        self._prepared = {}

    def register(self, capsule):
        capsule.catalog._principal(capsule.principal)
        identifier(capsule.request_id, 'request')
        identifier(capsule.generation, 'generation')
        if capsule.principal.owner or capsule.retired.is_set():
            raise CredentialError('worker_unavailable')
        self._prune()
        with self._lock:
            prior = self._capsules.get(_key(capsule))
            if prior is not None and prior is not capsule:
                raise CredentialError('worker_unavailable')
            replaced = [other for key, other in self._capsules.items()
                        if key[:2] == _key(capsule)[:2] and other is not capsule]
            inputs = []
            for other in replaced:
                inputs.extend(self._retire_locked(other))
            self._capsules[_key(capsule)] = capsule
        for item in inputs:
            item.catalog.inputs.retire(item.handle)

    def _retire_locked(self, capsule):
        capsule.retired.set()
        if self._capsules.get(_key(capsule)) is capsule:
            del self._capsules[_key(capsule)]
        retired = [key for key, item in self._prepared.items() if item.capsule() is capsule]
        return [self._prepared.pop(key) for key in retired]

    def retire_capsule(self, capsule):
        capsule.retired.set()
        with self._lock:
            inputs = self._retire_locked(capsule)
        for item in inputs:
            item.catalog.inputs.retire(item.handle)

    def _prune(self):
        with self._lock:
            snapshot = list(self._prepared.items())
        for key, item in snapshot:
            capsule = item.capsule()
            if (capsule is not None and not capsule.retired.is_set()
                    and item.catalog.clock() < item.expires
                    and item.catalog.inputs.available(item.handle, item.binding, item.transport)):
                continue
            with self._lock:
                if self._prepared.get(key) is not item:
                    continue
                del self._prepared[key]
            item.catalog.inputs.retire(item.handle)

    def _registered(self, capsule):
        with self._lock:
            return self._capsules.get(_key(capsule)) is capsule and not capsule.retired.is_set()

    def _find(self, catalog, actor_id, request_id, generation):
        with self._lock:
            capsule = self._capsules.get((str(catalog.path.resolve()), actor_id, request_id, generation))
        if capsule is None or capsule.retired.is_set():
            raise CredentialError('worker_unavailable')
        catalog._principal(capsule.principal)
        return capsule

    @staticmethod
    def _guard(capsule, lease_guard, transport_guard, expires=None):
        lease_guard()
        if capsule.retired.is_set():
            raise CredentialError('worker_unavailable')
        if expires is not None and capsule.catalog.clock() >= expires:
            raise CredentialError('handoff_expired')
        transport_guard()

    def _memory(self, catalog, row, capsule=None):
        with self._lock:
            item = self._prepared.get((str(catalog.path.resolve()), row['handoff_id']))
        actual = item.capsule() if item else None
        if (actual is None or (capsule is not None and actual is not capsule)
                or not self._registered(actual)):
            raise CredentialError('handoff_unavailable')
        return item, actual

    @staticmethod
    def _owned(conn, owner, operation_id):
        row = conn.execute('SELECT * FROM credential_replacement_handoffs WHERE owner_operation_id=?',
                           (operation_id,)).fetchone() if _has_table(conn) else None
        prior = conn.execute('SELECT * FROM credential_receipts WHERE operation_id=?',
                             (operation_id,)).fetchone() if conn else None
        if row is not None:
            if row['owner_actor'] != owner.actor_id:
                raise CredentialError('permission_denied')
            payload = _payload(row['entry_id'], row['expected_revision'], row['actor'],
                               row['request_id'], row['generation'], row['grant_revision'])
            if (not prior or prior['actor'] != owner.actor_id or prior['input_hash'] != _fingerprint(payload)
                    or json.loads(prior['result']) != _receipt(row)):
                raise CredentialError('idempotency_conflict')
        elif prior or (conn and conn.execute('SELECT 1 FROM credential_cancelled_operations '
                'WHERE operation_id=? AND actor!=?', (operation_id, owner.actor_id)).fetchone()) or (_has_table(conn) and conn.execute(
                'SELECT 1 FROM credential_replacement_handoffs WHERE worker_operation_id=?',
                (operation_id,)).fetchone()):
            raise CredentialError('idempotency_conflict')
        return row

    @staticmethod
    def _terminal(catalog, conn, owner, operation_id, row):
        result = {'operationId': operation_id, 'state': 'absent', 'receipt': None}
        committed = _committed(conn, row) if row is not None else None
        if committed is not None:
            return {**result, 'state': 'committed', 'receipt': committed}
        if catalog._cancelled(conn, owner, operation_id):
            return {**result, 'state': 'cancelled', 'receipt': _receipt(row) if row is not None else None}
        if row is None:
            return result
        return None

    def targets(self, catalog, owner, entry_id, expected_revision):
        _owner(catalog, owner)
        with catalog._connection() as conn:
            _current(catalog, conn, entry_id, expected_revision)
        with self._lock:
            capsules = [capsule for key, capsule in self._capsules.items()
                        if key[0] == str(catalog.path.resolve())]
        results = []
        for capsule in capsules:
            try:
                with capsule.lease_scope() as guard:
                    with catalog._connection() as conn:
                        _current(catalog, conn, entry_id, expected_revision)
                        revision = _grant(catalog, conn, capsule, entry_id)
                        guard()
                        if not self._registered(capsule):
                            continue
                        results.append({'actorId': capsule.principal.actor_id,
                            'requestId': capsule.request_id, 'generation': capsule.generation,
                            'entryId': entry_id, 'expectedRevision': expected_revision,
                            'grantRevision': revision})
            except CredentialError:
                continue
        return sorted(results, key=lambda item: (item['actorId'], item['requestId'], item['generation']))

    def prepare(self, catalog, owner, entry_id, expected_revision, actor_id, request_id,
                generation, operation_id, transport, value, transport_guard, expected_grant_revision):
        _owner(catalog, owner)
        for item, label in ((entry_id, 'entry'), (actor_id, 'actor'), (request_id, 'request'),
                            (generation, 'generation'), (operation_id, 'operation')):
            identifier(item, label)
        if type(expected_revision) is not int:
            raise CredentialError('revision_conflict')
        if type(expected_grant_revision) is not int or expected_grant_revision < 1:
            raise CredentialError('grant_revision_conflict')
        self._prune()
        capsule = self._find(catalog, actor_id, request_id, generation)
        payload = _payload(entry_id, expected_revision, actor_id, request_id, generation, expected_grant_revision)
        provisional = None
        memory_key = None
        try:
            with capsule.lease_scope() as lease_guard:
                with catalog._connection(write=True) as conn:
                    _schema(conn)
                    self._guard(capsule, lease_guard, transport_guard)
                    prior_row = self._owned(conn, owner, operation_id)
                    prior = catalog._receipt(conn, owner, operation_id, payload)
                    if prior is not None:
                        return prior
                    assert prior_row is None
                    row = _current(catalog, conn, entry_id, expected_revision)
                    grant_revision = _grant(catalog, conn, capsule, entry_id, expected_grant_revision)
                    state = catalog.backend.status().state
                    if state != 'available':
                        raise CredentialError('backend_' + state)
                    handoff_id, worker_operation = str(uuid.uuid4()), str(uuid.uuid4())
                    if worker_operation == operation_id or conn.execute(
                            'SELECT 1 FROM credential_receipts WHERE operation_id=? UNION ALL '
                            'SELECT 1 FROM credential_cancelled_operations WHERE operation_id=? UNION ALL '
                            'SELECT 1 FROM credential_replacement_handoffs WHERE owner_operation_id=? OR worker_operation_id=?',
                            (worker_operation,) * 4).fetchone():
                        raise CredentialError('idempotency_conflict')
                    now = catalog.clock()
                    binding = ValueBinding(catalog.organization_id, entry_id, row['version'] + 1,
                                           actor_id, worker_operation)
                    with self._lock:
                        if self._capsules.get(_key(capsule)) is not capsule:
                            raise CredentialError('worker_unavailable')
                        handle = catalog.inputs.submit(binding, transport, value,
                            guard=lambda: self._guard(capsule, lease_guard, transport_guard, now + 90))
                        provisional = _Prepared(weakref.ref(capsule), catalog, binding, handle,
                                                transport, transport_guard, now + 90)
                        memory_key = (str(catalog.path.resolve()), handoff_id)
                        self._prepared[memory_key] = provisional
                    # Non-bearer operation identity only, never a hash of value bytes.
                    # This verifies exact committed truth even after process restart.
                    replacement_hash = _fingerprint({'action': 'replace_value', 'entryId': entry_id,
                        'expectedRevision': expected_revision, 'inputHandleId': handle})
                    conn.execute('INSERT INTO credential_replacement_handoffs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (operation_id, owner.actor_id, handoff_id, worker_operation, actor_id, entry_id,
                         expected_revision, binding.version, grant_revision, request_id, generation,
                         now, now + 90, replacement_hash))
                    mapping = conn.execute('SELECT * FROM credential_replacement_handoffs WHERE handoff_id=?',
                                           (handoff_id,)).fetchone()
                    result = catalog._finish(conn, owner, operation_id, 'prepare_replacement', payload,
                                             _receipt(mapping), entry_id, expected_revision)
            return result
        except BaseException:
            if provisional is not None:
                with self._lock:
                    if self._prepared.get(memory_key) is provisional:
                        del self._prepared[memory_key]
                catalog.inputs.retire(provisional.handle)
            raise

    def _validate(self, catalog, conn, row, capsule, lease_guard):
        if (row['actor'], row['request_id'], row['generation']) != (
                capsule.principal.actor_id, capsule.request_id, capsule.generation):
            raise CredentialError('handoff_unavailable')
        if conn.execute('SELECT 1 FROM credential_cancelled_operations WHERE '
                '(actor=? AND operation_id=?) OR (actor=? AND operation_id=?)',
                (row['owner_actor'], row['owner_operation_id'], row['actor'], row['worker_operation_id'])).fetchone():
            raise CredentialError('operation_cancelled')
        _current(catalog, conn, row['entry_id'], row['expected_revision'])
        _grant(catalog, conn, capsule, row['entry_id'], row['grant_revision'])
        if catalog.clock() >= row['expires']:
            raise CredentialError('handoff_expired')
        item, _ = self._memory(catalog, row, capsule)
        if item.catalog.inputs is not catalog.inputs:
            raise CredentialError('handoff_unavailable')
        self._guard(capsule, lease_guard, item.transport_guard, row['expires'])
        if not item.catalog.inputs.available(item.handle, item.binding, item.transport):
            raise CredentialError('handoff_unavailable')
        return item

    def discover(self, capsule, lease_guard):
        catalog = capsule.catalog
        catalog._principal(capsule.principal)
        lease_guard()
        if not self._registered(capsule):
            raise CredentialError('worker_unavailable')
        self._prune()
        with catalog._connection() as conn:
            rows = conn.execute('SELECT * FROM credential_replacement_handoffs WHERE '
                'actor=? AND request_id=? AND generation=? ORDER BY created,handoff_id',
                (capsule.principal.actor_id, capsule.request_id, capsule.generation)).fetchall() if _has_table(conn) else []
            results = []
            for row in rows:
                try:
                    if _committed(conn, row) is not None:
                        continue
                    self._validate(catalog, conn, row, capsule, lease_guard)
                except CredentialError:
                    continue
                results.append({'handoffId': row['handoff_id'], 'entryId': row['entry_id'],
                                'expectedRevision': row['expected_revision'], 'expiresAt': row['expires']})
            return results

    def replace(self, capsule, handoff_id, lease_guard):
        identifier(handoff_id, 'handoff')
        catalog = capsule.catalog
        catalog._principal(capsule.principal)
        lease_guard()
        if not self._registered(capsule):
            raise CredentialError('worker_unavailable')
        with catalog._connection(write=True) as conn:
            lease_guard()
            if not self._registered(capsule):
                raise CredentialError('worker_unavailable')
            row = conn.execute('SELECT * FROM credential_replacement_handoffs WHERE handoff_id=?',
                               (handoff_id,)).fetchone() if _has_table(conn) else None
            if row is None or (row['actor'], row['request_id'], row['generation']) != (
                    capsule.principal.actor_id, capsule.request_id, capsule.generation):
                raise CredentialError('handoff_unavailable')
            _grant(catalog, conn, capsule, row['entry_id'], row['grant_revision'])
            prior = _committed(conn, row)
            if prior is not None:
                return prior
            item = self._validate(catalog, conn, row, capsule, lease_guard)
            def consume_guard():
                self._guard(capsule, lease_guard, item.transport_guard, row['expires'])
                _current(catalog, conn, row['entry_id'], row['expected_revision'])
            result = catalog._replace_value(conn, capsule.principal, row['entry_id'],
                expected_revision=row['expected_revision'], operation_id=row['worker_operation_id'],
                input_handle=item.handle, transport=item.transport,
                guard=consume_guard)
            self._prune()
            return result

    def status(self, catalog, owner, operation_id):
        _owner(catalog, owner)
        identifier(operation_id, 'operation')
        self._prune()
        with catalog._connection() as conn:
            row = self._owned(conn, owner, operation_id)
            terminal = self._terminal(catalog, conn, owner, operation_id, row)
            if terminal is not None:
                return terminal
        result = {'operationId': operation_id, 'state': 'unavailable', 'receipt': _receipt(row)}
        try:
            _, capsule = self._memory(catalog, row)
            with capsule.lease_scope() as guard:
                with catalog._connection() as conn:
                    terminal = self._terminal(catalog, conn, owner, operation_id, row)
                    if terminal is not None:
                        return terminal
                    self._validate(catalog, conn, row, capsule, guard)
                    return {**result, 'state': 'pending'}
        except CredentialError:
            # A worker finishing or route disappearing cannot hide a committed put.
            with catalog._connection() as conn:
                return self._terminal(catalog, conn, owner, operation_id, row) or result

    def cancel(self, catalog, owner, operation_id):
        _owner(catalog, owner)
        identifier(operation_id, 'operation')
        with catalog._connection(write=True) as conn:
            row = self._owned(conn, owner, operation_id)
            committed = _committed(conn, row) if row is not None else None
            if committed is not None:
                return {'operationId': operation_id, 'state': 'committed', 'receipt': committed}
            created = conn.execute('INSERT OR IGNORE INTO credential_cancelled_operations VALUES (?,?,?)',
                                  (owner.actor_id, operation_id, catalog.clock())).rowcount
            if row is not None:
                conn.execute('INSERT OR IGNORE INTO credential_cancelled_operations VALUES (?,?,?)',
                             (row['actor'], row['worker_operation_id'], catalog.clock()))
                with self._lock:
                    item = self._prepared.pop((str(catalog.path.resolve()), row['handoff_id']), None)
                if item is not None:
                    item.catalog.inputs.retire(item.handle)
            if created:
                conn.execute('INSERT INTO credential_audit(operation_id,actor,action,entry_id,revision,created) '
                    'VALUES (?,?,?,?,?,?)', (operation_id, owner.actor_id, 'cancel_replacement',
                    row['entry_id'] if row is not None else None,
                    row['expected_revision'] if row is not None else None, catalog.clock()))
            return {'operationId': operation_id, 'state': 'cancelled',
                    'receipt': _receipt(row) if row is not None else None}


handoffs = ReplacementHandoffs()

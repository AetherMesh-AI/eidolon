"""Exact trusted adapter invocation. No arbitrary command, URL, or env interface.

Adapters are registered by trusted application code, not catalog contents. A use
receipt never contains adapter output. Actual provider tools require their own
reviewed, output-safe adapter; none is enabled by default.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import uuid
from typing import Callable

from eidolon_cli.organization_credential_security import CredentialError, ValueBinding, identifier


@dataclass(frozen=True)
class TrustedAdapter:
    tool: str
    destination: str
    invoke: Callable[[bytes], None]


class CredentialBroker:
    def __init__(self, catalog, adapters=()):
        self.catalog = catalog
        self._adapters = {(item.tool, item.destination): item for item in adapters}
        if len(self._adapters) != len(adapters):
            raise ValueError('Duplicate trusted adapter binding')

    def _authorize(self, conn, principal, entry_id, version, tool, destination):
        catalog = self.catalog
        catalog._require(conn, principal, 'use', entry_id, tool=tool, destination=destination)
        row = catalog._row(conn, entry_id)
        metadata = json.loads(row['metadata'])
        if row['status'] != 'active':
            raise CredentialError('entry_revoked')
        if type(version) is not int or row['version'] != version:
            raise CredentialError('version_conflict')
        if metadata['expiresAt'] is not None and metadata['expiresAt'] <= catalog.clock():
            raise CredentialError('entry_expired')
        if not row['backend_ref']:
            raise CredentialError('value_missing')
        if catalog.backend.status().state != 'available':
            raise CredentialError('backend_' + catalog.backend.status().state)
        return row

    def queue(self, principal, entry_id, *, version, tool, destination, operation_id):
        catalog = self.catalog
        catalog._principal(principal)
        identifier(tool, 'tool')
        identifier(destination, 'destination')
        if (tool, destination) not in self._adapters:
            raise CredentialError('adapter_unavailable')
        payload = {'action': 'queue_use', 'entryId': entry_id, 'version': version, 'tool': tool, 'destination': destination}
        with catalog._connection(write=True) as conn:
            self._authorize(conn, principal, entry_id, version, tool, destination)
            prior = catalog._receipt(conn, principal, operation_id, payload)
            if prior:
                return prior
            use_id = str(uuid.uuid4())
            conn.execute('INSERT INTO credential_uses VALUES (?,?,?,?,?,?,?,?,?)',
                         (use_id, entry_id, version, principal.actor_id, tool, destination, 'queued', catalog.clock(), None))
            result = {'useId': use_id, 'state': 'queued', 'entryId': entry_id, 'version': version,
                      'operationId': operation_id}
            return catalog._finish(conn, principal, operation_id, 'queue_use', payload, result, entry_id)

    def run(self, principal, use_id):
        catalog = self.catalog
        catalog._principal(principal)
        identifier(use_id, 'use')
        with catalog._connection(write=True) as conn:
            use = conn.execute('SELECT * FROM credential_uses WHERE id=? AND actor=?', (use_id, principal.actor_id)).fetchone() if conn else None
            if not use:
                raise CredentialError('use_missing')
            if use['state'] != 'queued':
                return {'useId': use_id, 'state': use['state'], 'replayed': True}
            row = self._authorize(conn, principal, use['entry_id'], use['version'], use['tool'], use['destination'])
            reference, binding = row['backend_ref'], ValueBinding(**json.loads(row['value_binding']))
            conn.execute("UPDATE credential_uses SET state='resolving' WHERE id=?", (use_id,))
        adapter = self._adapters.get((use['tool'], use['destination']))
        if adapter is None:
            raise CredentialError('adapter_unavailable')

        def consume(value):
            # This final transaction is the exposure boundary. Revocation before
            # it cancels the use; after it, the owner sees an in-flight operation.
            with catalog._connection(write=True) as conn:
                current = conn.execute('SELECT state FROM credential_uses WHERE id=?', (use_id,)).fetchone()
                if current[0] != 'resolving':
                    raise CredentialError('use_not_queued')
                self._authorize(conn, principal, use['entry_id'], use['version'], use['tool'], use['destination'])
                conn.execute("UPDATE credential_uses SET state='started' WHERE id=?", (use_id,))
            adapter.invoke(value)

        state = 'completed'
        try:
            catalog.backend.consume(reference, binding, consume)
        except Exception:
            # Backend/provider exceptions may contain values; never retain them.
            state = 'failed'
        with catalog._connection(write=True) as conn:
            current = conn.execute('SELECT state FROM credential_uses WHERE id=?', (use_id,)).fetchone()[0]
            if current != 'cancelled':
                conn.execute('UPDATE credential_uses SET state=?,finished=? WHERE id=?', (state, catalog.clock(), use_id))
            else:
                state = current
            row = catalog._row(conn, use['entry_id'])
            conn.execute('INSERT INTO credential_audit(operation_id,actor,action,entry_id,revision,created) VALUES (?,?,?,?,?,?)',
                         (use_id, principal.actor_id, 'use_' + state, use['entry_id'], row['revision'], catalog.clock()))
            return {'useId': use_id, 'state': state, 'revokedDuringUse': row['status'] != 'active',
                    'providerRevoked': False}

"""Persisted grant/routing generations fence stale organization schedulers."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_roster import OrganizationStaff


POLICY_SCHEMA = """
CREATE TABLE IF NOT EXISTS organization_policy (
 id INTEGER PRIMARY KEY CHECK(id=1), generation INTEGER NOT NULL,
 fingerprint TEXT NOT NULL, settings TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS request_policy (
 request_id TEXT PRIMARY KEY REFERENCES requests(id), generation INTEGER NOT NULL);
CREATE TRIGGER IF NOT EXISTS request_requires_current_policy
BEFORE UPDATE OF status ON requests WHEN NEW.status='running' AND NOT EXISTS (
 SELECT 1 FROM request_policy c JOIN organization_policy p ON p.generation=c.generation
 WHERE c.request_id=NEW.id AND p.id=1)
BEGIN SELECT RAISE(ABORT, 'Request requires a current organization policy'); END;
"""

# Execution-slot/timeout knobs are each process's operational bounds. They may
# differ while an old provider call is still unwinding. Grants, source roots,
# staff identities/routes and worker capacity are authoritative shared policy.
_AUTHORITY_FIELDS = ('team', 'capabilities', 'roster', 'tool_grants', 'read_roots', 'max_workers', 'max_replans', 'max_stages', 'max_owner_resolutions', 'max_output_tokens')


def _fingerprint(settings):
    return hashlib.sha256(json.dumps({key: settings.get(key, getattr(OrganizationSettings, key)) for key in _AUTHORITY_FIELDS},
                                     sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def persisted_settings(conn):
    """A read-only store reopen must not reset an explicit roster to defaults."""
    row = conn.execute('SELECT settings,fingerprint FROM organization_policy WHERE id=1').fetchone()
    if row is None:
        return None
    values = json.loads(row['settings'])
    legacy = hashlib.sha256(json.dumps({key: values[key] for key in _AUTHORITY_FIELDS if key in values},
                                      sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if _fingerprint(values) != row['fingerprint'] and legacy != row['fingerprint']:
        raise ValueError('Persisted organization policy does not match its identity')
    for key in ('capabilities', 'tool_grants', 'read_roots'):
        values[key] = tuple(values[key])
    if values['roster'] is not None:
        values['roster'] = tuple(OrganizationStaff(**{**staff,
            'capabilities': tuple(staff['capabilities']), 'tool_grants': tuple(staff['tool_grants']),
            'responsibilities': tuple(staff.get('responsibilities', ()))})
            for staff in values['roster'])
    return OrganizationSettings(**values)


class OrganizationPolicyStore:
    def _adopt_policy(self, conn):
        values = asdict(self.settings)
        fingerprint = _fingerprint(values)
        serialized = json.dumps(values, sort_keys=True)
        previous = conn.execute('SELECT * FROM organization_policy WHERE id=1').fetchone()
        changed = previous is None or previous['fingerprint'] != fingerprint
        generation = 1 if previous is None else previous['generation'] + int(changed)
        conn.execute('INSERT INTO organization_policy VALUES (1,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                     'generation=excluded.generation,fingerprint=excluded.fingerprint,settings=excluded.settings',
                     (generation, fingerprint, serialized))
        self._policy_generation, self._policy_fingerprint = generation, fingerprint
        if changed:
            for request in conn.execute("SELECT * FROM requests WHERE status='running'").fetchall():
                self._pending(conn, request,
                              'Organization grants or routing changed. This execution was fenced; review its outcome before retrying under the current policy.')

    def _policy_current(self, conn):
        row = conn.execute('SELECT generation,fingerprint FROM organization_policy WHERE id=1').fetchone()
        return (row is not None and row['generation'] == self._policy_generation
                and row['fingerprint'] == self._policy_fingerprint)

    def _require_current_policy(self, conn):
        if not self._policy_current(conn):
            raise ValueError('Organization grants or routing changed; restart this runtime before submitting or retrying work')

    def _stamp_claim_policy(self, conn, request_id):
        self._require_current_policy(conn)
        conn.execute('INSERT INTO request_policy VALUES (?,?) ON CONFLICT(request_id) DO UPDATE SET generation=excluded.generation',
                     (request_id, self._policy_generation))

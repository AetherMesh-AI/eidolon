"""Persisted grant/routing generations fence stale organization schedulers."""
from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_roster import OrganizationStaff


POLICY_SCHEMA = """
CREATE TABLE IF NOT EXISTS organization_policy (
 id INTEGER PRIMARY KEY CHECK(id=1), generation INTEGER NOT NULL,
 fingerprint TEXT NOT NULL, settings TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS organization_configuration (
 id INTEGER PRIMARY KEY CHECK(id=1), configuration TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS request_policy (
 request_id TEXT PRIMARY KEY REFERENCES requests(id), generation INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS organization_policy_pause (
 id INTEGER PRIMARY KEY CHECK(id=1));
CREATE TRIGGER IF NOT EXISTS request_requires_unpaused_policy
BEFORE UPDATE OF status ON requests WHEN NEW.status='running' AND EXISTS (
 SELECT 1 FROM organization_policy_pause WHERE id=1)
BEGIN SELECT RAISE(ABORT, 'Organization configuration is paused'); END;
CREATE TRIGGER IF NOT EXISTS request_requires_current_policy
BEFORE UPDATE OF status ON requests WHEN NEW.status='running' AND NOT EXISTS (
 SELECT 1 FROM request_policy c JOIN organization_policy p ON p.generation=c.generation
 WHERE c.request_id=NEW.id AND p.id=1)
BEGIN SELECT RAISE(ABORT, 'Request requires a current organization policy'); END;
"""

# Startup operational settings can differ between processes. Explicit owner
# configuration changes still advance the generation, even for concurrency.
_AUTHORITY_FIELDS = ('team', 'capabilities', 'roster', 'tool_grants', 'read_roots', 'max_workers',
                     'max_replans', 'max_stages', 'max_owner_resolutions', 'max_output_tokens',
                     'max_members', 'max_request_depth', 'max_requests_per_stage',
                     'max_context_tokens', 'max_model_calls', 'max_total_tokens', 'objective_timeout_seconds',
                     'max_cost_usd', 'model_costs', 'project_grants', 'max_project_runs', 'projects')


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
            **{key: tuple(staff.get(key, ())) for key in (
                'capabilities', 'tool_grants', 'responsibilities', 'authority', 'managed_teams')}})
            for staff in values['roster'])
    for key in ('ledger_project_ids', 'project_registry_conflicts'):
        values[key] = tuple(values.get(key, ()))
    from eidolon_cli.organization_budget import parse_model_costs
    values['model_costs'] = parse_model_costs(values.get('model_costs', []))
    from eidolon_cli.organization_project_config import parse_project_grants
    values['project_grants'] = parse_project_grants(values.get('project_grants', []), len(values['read_roots']))
    from eidolon_cli.organization_projects import parse_projects
    values['projects'] = parse_projects(values.get('projects', []), values['read_roots'], values['project_grants'])
    # Legacy ledgers may already have more than the newly introduced default.
    # Their existing explicit members survive migration; the hard bound is 64.
    if 'max_members' not in values:
        values['max_members'] = max(OrganizationSettings.max_members, len(values['roster'] or ()))
    return OrganizationSettings(**values)


def resolve_settings(conn, seed_settings=None):
    """DB-managed membership survives service restarts; security policy does not.

    Only roster and the two owner-editable capacity settings override a startup
    configuration. Revoking an organization tool grant still makes a retained
    member unavailable instead of silently granting it again or erasing history.
    """
    from eidolon_cli.organization_project_registry import resolve_registry
    saved = persisted_settings(conn)
    base = seed_settings or saved or OrganizationSettings()
    row = conn.execute('SELECT configuration FROM organization_configuration WHERE id=1').fetchone()
    if row is None:
        return resolve_registry(conn, base)
    override = json.loads(row['configuration'])
    if set(override) != {'roster', 'max_inflight', 'max_members'}:
        raise ValueError('Persisted organization configuration contains unsupported settings')
    raw = json.loads(json.dumps(asdict(base)))
    raw.update(override)
    grants = list(dict.fromkeys([*raw['tool_grants'],
                                *(grant for staff in raw['roster'] for grant in staff.get('tool_grants', []))]))
    validated = OrganizationSettings.from_config({'organization': {**raw, 'tool_grants': grants}})
    return resolve_registry(conn, replace(validated, tool_grants=base.tool_grants,
        ledger_project_ids=base.ledger_project_ids))


class OrganizationPolicyStore:
    def reload_configuration(self, settings):
        """Explicit in-memory policy adoption; file hosts use reload_profile_configuration."""
        with self._write() as conn:
            return self._reload_configuration(conn, settings)

    def _reload_configuration(self, conn, settings):
        updated = resolve_settings(conn, settings)
        if updated == self.settings and self._policy_current(conn):
            return False
        self.settings = updated
        self._adopt_policy(conn, resume=True)
        conn.execute("UPDATE agents SET team=? WHERE id IN ('owner','executive','director','manager','reviewer','control:apply','control:project')", (self.settings.team,))
        self._sync_staff(conn)
        self._migrate_identities(conn)
        return True

    def reload_profile_configuration(self, home, *, require_sources=False):
        """Serialize the source read with adoption, including invalid-file pauses.

        This fences cooperating ledger hosts, not arbitrary external editors.
        It is not an atomic file-plus-database write protocol.
        """
        from eidolon_cli.organization_config import load_profile_configuration, profile_configuration_scope, ProfileSourcesRequired
        from eidolon_cli.config_primitives import InvalidUserConfigError
        error = None
        with self._write() as conn, profile_configuration_scope(home):
            try:
                settings, enabled = load_profile_configuration(home, require_sources=require_sources)
                settings = resolve_settings(conn, settings)
            except ProfileSourcesRequired:
                raise  # No policy adoption or pause before cold source hydration.
            except Exception:
                # Raise only after committing the pause; raising inside _write
                # would roll back the cross-process execution fence.
                self._pause_configuration(conn)
                error = InvalidUserConfigError('Organization configuration is invalid; repair the profile configuration before resuming work')
            else:
                self._reload_configuration(conn, settings)
        if error is not None:
            raise error from None
        return enabled

    def pause_configuration(self):
        """Invalid file policy fences every host, preserving the last good policy."""
        with self._write() as conn:
            return self._pause_configuration(conn)

    def _pause_configuration(self, conn):
        if conn.execute('SELECT 1 FROM organization_policy_pause WHERE id=1').fetchone() is not None:
            return False
        conn.execute('INSERT INTO organization_policy_pause VALUES (1)')
        conn.execute('UPDATE organization_policy SET generation=generation+1 WHERE id=1')
        for request in conn.execute("SELECT * FROM requests WHERE status='running'").fetchall():
            self._pending(conn, request, 'Organization configuration is invalid. This execution was fenced; repair the profile configuration and review its outcome before retrying.')
        return True

    @staticmethod
    def _policy_paused(conn):
        return (conn.execute('SELECT 1 FROM organization_policy_pause WHERE id=1').fetchone() is not None
                or conn.execute('SELECT 1 FROM organization_project_registry_conflicts WHERE id=1').fetchone() is not None)

    def _adopt_policy(self, conn, *, exclude_request_id=None, force=False, resume=False):
        from eidolon_cli.organization_project_registry import adopt_registry_status, resolve_registry
        self.settings = resolve_registry(conn, self.settings)
        force = adopt_registry_status(conn, self.settings) or force
        if resume and conn.execute('SELECT 1 FROM organization_policy_pause WHERE id=1').fetchone() is not None:
            conn.execute('DELETE FROM organization_policy_pause WHERE id=1')
            force = True
        values = asdict(self.settings)
        fingerprint = _fingerprint(values)
        serialized = json.dumps(values, sort_keys=True)
        previous = conn.execute('SELECT * FROM organization_policy WHERE id=1').fetchone()
        changed = force or previous is None or previous['fingerprint'] != fingerprint
        generation = 1 if previous is None else previous['generation'] + int(changed)
        conn.execute('INSERT INTO organization_policy VALUES (1,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                     'generation=excluded.generation,fingerprint=excluded.fingerprint,settings=excluded.settings',
                     (generation, fingerprint, serialized))
        self._policy_generation, self._policy_fingerprint = generation, fingerprint
        if changed:
            for request in conn.execute("SELECT * FROM requests WHERE status='running'").fetchall():
                if request['id'] == exclude_request_id:
                    self._stamp_claim_policy(conn, request['id'])
                    continue
                self._pending(conn, request,
                              'Organization grants or routing changed. This execution was fenced; review its outcome before retrying under the current policy.')

    def _policy_current(self, conn):
        row = conn.execute('SELECT generation,fingerprint FROM organization_policy WHERE id=1').fetchone()
        return (not self._policy_paused(conn) and row is not None and row['generation'] == self._policy_generation
                and row['fingerprint'] == self._policy_fingerprint)

    def _require_current_policy(self, conn):
        if not self._policy_current(conn):
            raise ValueError('Organization grants or routing changed; wait for active work to stop, then refresh Organization before submitting or retrying work')

    def _stamp_claim_policy(self, conn, request_id):
        self._require_current_policy(conn)
        conn.execute('INSERT INTO request_policy VALUES (?,?) ON CONFLICT(request_id) DO UPDATE SET generation=excluded.generation',
                     (request_id, self._policy_generation))

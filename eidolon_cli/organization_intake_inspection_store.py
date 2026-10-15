"""Durable owner delegation and one-attempt pre-inference inspection receipts."""
import json
import time

from eidolon_cli.organization_intake_context import digest
from eidolon_cli.organization_intake_inspection import inspection_authority, inspect_context

INSPECTION_SCHEMA = '''
CREATE TABLE IF NOT EXISTS organization_intake_inspection_policies (
 owner_identity_id TEXT NOT NULL, revision INTEGER NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
 input_hash TEXT NOT NULL, policy_json TEXT NOT NULL, created REAL NOT NULL,
 PRIMARY KEY(owner_identity_id,revision));
CREATE TABLE IF NOT EXISTS organization_intake_inspections (
 intake_id TEXT PRIMARY KEY REFERENCES organization_request_intakes(id), authority_sha256 TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('started','completed')), result_json TEXT, created REAL NOT NULL);
CREATE TRIGGER IF NOT EXISTS intake_inspection_policy_no_update BEFORE UPDATE ON organization_intake_inspection_policies
 BEGIN SELECT RAISE(ABORT,'Inspection delegation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS intake_inspection_policy_no_delete BEFORE DELETE ON organization_intake_inspection_policies
 BEGIN SELECT RAISE(ABORT,'Inspection delegation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS intake_inspection_receipt_no_delete BEFORE DELETE ON organization_intake_inspections
 BEGIN SELECT RAISE(ABORT,'Inspection receipt is immutable'); END;
CREATE TRIGGER IF NOT EXISTS intake_inspection_receipt_final BEFORE UPDATE ON organization_intake_inspections
 WHEN OLD.status='completed' OR NEW.intake_id!=OLD.intake_id OR NEW.authority_sha256!=OLD.authority_sha256 OR NEW.created!=OLD.created
 BEGIN SELECT RAISE(ABORT,'Inspection receipt is immutable'); END;
'''


def current_policy(conn):
    owner = conn.execute("SELECT identity_id FROM agent_identity WHERE agent_id='owner'").fetchone()
    if owner is None:
        raise ValueError('Inspection owner is unavailable')
    row = conn.execute('SELECT policy_json FROM organization_intake_inspection_policies WHERE owner_identity_id=? ORDER BY revision DESC LIMIT 1', (owner[0],)).fetchone()
    return json.loads(row[0]) if row else {'enabled': False, 'revision': 0, 'ownerIdentityId': owner[0],
                                          'workerId': None, 'projectIds': [], 'paths': [], 'rootIdentities': {}}


def inspection_projection(store, conn, intake_id=None):
    policy = current_policy(conn)
    public = {'status': 'disabled', 'items': []}
    binding = {'delegation': digest(policy), 'receipt': None}
    if policy['enabled']:
        authority = inspection_authority(store, conn, policy)
        binding['authority'] = authority['sha256']
        public['status'] = 'not_observed'
        if intake_id:
            row = conn.execute('SELECT * FROM organization_intake_inspections WHERE intake_id=?', (intake_id,)).fetchone()
            if row and row['status'] == 'completed':
                if row['authority_sha256'] != authority['sha256']:
                    raise ValueError('Inspection delegation changed before admission')
                result = json.loads(row['result_json'])
                public = {**result['public'], 'observedAt': result['receipt']['observedAt'],
                          'authoritySha256': result['receipt']['authoritySha256']}
                binding['receipt'] = digest(result)
    return public, digest(binding)


def policy_view(store, conn):
    from eidolon_cli.organization_metadata import _inspect
    from eidolon_cli.organization_roster import configured_staff, staff_unavailability
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    inspection_tables = {'organization_intake_inspection_policies', 'organization_intake_inspections'}
    if tables & inspection_tables and not inspection_tables <= tables:
        raise ValueError('Inspection storage is incomplete')
    policy = current_policy(conn) if inspection_tables <= tables else {
        'enabled': False, 'revision': 0, 'workerId': None, 'projectIds': [], 'paths': []}
    metadata = _inspect(conn).get('metadata') or {}
    workers = [{'id': w.id, 'name': w.name, 'team': w.team} for w in configured_staff(store.settings)
               if w.role == 'Worker' and not staff_unavailability(w, store.settings, 'work.inspect')
               and not store._staff_reason(conn, {'id': w.id}, 'work.inspect')]
    projects = [{'id': p.id, 'root': p.root, 'team': p.team} for p in store.settings.projects
                if p.root not in store.settings.revoked_root_aliases]
    return {key: policy[key] for key in ('enabled', 'revision', 'workerId', 'projectIds', 'paths')} | {
        'workers': workers, 'projects': projects, 'organizationId': metadata.get('organizationId')}


def read_inspection_policy(home):
    """Read configuration for explicit owner review without adopting or migrating it."""
    from contextlib import closing
    from pathlib import Path
    import sqlite3
    from eidolon_cli.organization_config import load_profile_configuration, profile_configuration_scope
    from eidolon_cli.organization_observation import _compatible_schema, _ObservationView
    from eidolon_cli.organization_policy import resolve_settings
    home = Path(home)
    path = home / 'organization' / 'state.db'
    if not path.exists():
        return {'enabled': False, 'revision': 0, 'workerId': None, 'projectIds': [], 'paths': [],
                'workers': [], 'projects': [], 'organizationId': None}
    try:
        with closing(sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True,
                                     timeout=.25, isolation_level=None)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not _compatible_schema(conn, tables):
                raise ValueError('Inspection history schema is unsupported')
            with profile_configuration_scope(home):
                settings, _ = load_profile_configuration(home)
            settings = resolve_settings(conn, settings)
            return policy_view(_ObservationView(path, conn, settings), conn)
    except sqlite3.Error:
        raise RuntimeError('Inspection history storage is unavailable') from None


class OrganizationIntakeInspectionStore:
    def inspection_policy_view(self):
        with self._connect() as conn:
            return policy_view(self, conn)

    def configure_inspection(self, *, expected_organization_id, expected_revision, enabled,
                             worker_id, project_ids, paths, confirmed, idempotency_key):
        from eidolon_cli.organization_owner_policy import _identity
        from eidolon_cli.organization_projects import selected_projects
        from eidolon_cli.organization_root_grants import directory_identity
        from eidolon_cli.organization_intake_contract import _text
        if confirmed is not True or type(enabled) is not bool or type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Inspection delegation requires explicit owner confirmation and current revision')
        key = _text(idempotency_key, 'Idempotency key', 128)
        inputs = [expected_organization_id, expected_revision, enabled, worker_id, project_ids, paths]
        with self._write() as conn:
            self._require_current_policy(conn)
            _identity(conn, expected_organization_id)
            old = current_policy(conn)
            receipt = conn.execute('SELECT * FROM organization_intake_inspection_policies WHERE idempotency_key=?', (key,)).fetchone()
            if receipt:
                if receipt['owner_identity_id'] != old['ownerIdentityId'] or receipt['input_hash'] != digest(inputs):
                    raise ValueError('Inspection key belongs to another owner or intent')
            else:
                if old['revision'] != expected_revision:
                    raise ValueError('Inspection policy revision changed')
                if not enabled and (worker_id is not None or project_ids != [] or paths != []):
                    raise ValueError('Disabled inspection does not accept hidden delegation')
                projects = selected_projects(project_ids, self.settings) if enabled else []
                roots = {p['root']: [0, 0] for p in projects}
                policy = {'enabled': enabled, 'revision': old['revision']+1, 'ownerIdentityId': old['ownerIdentityId'],
                          'workerId': worker_id, 'projectIds': project_ids, 'paths': paths, 'rootIdentities': roots}
                if enabled:
                    inspection_authority(self, conn, policy)
                    for project in projects:
                        identity = directory_identity(project['readRoot'])
                        roots[project['root']] = [identity['device'], identity['inode']]
                conn.execute('INSERT INTO organization_intake_inspection_policies VALUES (?,?,?,?,?,?)',
                    (old['ownerIdentityId'], policy['revision'], key, digest(inputs), json.dumps(policy), time.time()))
        return self.inspection_policy_view()

    def prepare_intake_inspection(self, intake_id, *, cancel):
        from eidolon_cli.organization_intake_context import intake_context
        with self._write() as conn:
            row = self._intake_row(conn, intake_id)
            policy = current_policy(conn)
            if not policy['enabled']:
                return
            if row['status'] != 'pending' or row['dispatched']:
                raise ValueError('Inspection must precede the single provider send')
            if conn.execute('SELECT 1 FROM organization_intake_inspections WHERE intake_id=?', (intake_id,)).fetchone():
                raise ValueError('Inspection is already journaled; no automatic replay')
            context, _ = intake_context(self, conn, row['owner_priority'])
            if context.snapshot_id != row['snapshot_id']:
                raise ValueError('Intake context changed before inspection')
            authority = inspection_authority(self, conn, policy)
            conn.execute("INSERT INTO organization_intake_inspections VALUES (?,?,'started',NULL,?)",
                         (intake_id, authority['sha256'], time.time()))
        def current():
            with self._connect() as conn:
                self._intake_row(conn, intake_id)
                return inspection_authority(self, conn, current_policy(conn))
        result = inspect_context(current, cancel=cancel)
        with self._write() as conn:
            row = self._intake_row(conn, intake_id)
            # The final authority check is inside the durable acceptance transaction.
            fresh = inspection_authority(self, conn, current_policy(conn))
            if cancel.is_set() or fresh['sha256'] != result['receipt']['authoritySha256']:
                raise ValueError('Inspection authority changed before receipt acceptance')
            if row['status'] != 'pending' or row['dispatched']:
                raise ValueError('Intake state changed before inspection acceptance')
            context, _ = intake_context(self, conn, row['owner_priority'])
            if context.snapshot_id != row['snapshot_id']:
                raise ValueError('Intake context changed during inspection')
            conn.execute("UPDATE organization_intake_inspections SET status='completed',result_json=? WHERE intake_id=? AND status='started'",
                         (json.dumps(result), intake_id))
            context, payload = intake_context(self, conn, row['owner_priority'], intake_id=intake_id)
            conn.execute('UPDATE organization_request_intakes SET snapshot_id=?,context_json=? WHERE id=?',
                         (context.snapshot_id, json.dumps(payload), intake_id))

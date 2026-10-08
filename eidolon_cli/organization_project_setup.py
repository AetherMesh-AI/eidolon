"""Reviewed project identities: inert drafts and explicit ledger-only activation."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
import hashlib
import json
import sqlite3

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_policy import resolve_settings, _fingerprint
from eidolon_cli.organization_project_registry import registry_rows
from eidolon_cli.organization_projects import parse_projects


def _revision(content, settings, conn, *, canonical_roots=None):
    policy = conn.execute('SELECT generation,fingerprint FROM organization_policy WHERE id=1').fetchone() if conn else None
    fingerprint = _fingerprint(asdict(settings))
    # Cold get_service initializes the default policy, then observes this file.
    # Predict only that bootstrap revision; an intervening owner change advances
    # the actual generation and therefore invalidates the draft.
    initial = (1 + int(fingerprint != _fingerprint(asdict(OrganizationSettings()))), fingerprint)
    roots = canonical_roots if canonical_roots is not None else [str(Path(root).resolve()) for root in settings.read_roots]
    return hashlib.sha256(content + json.dumps([tuple(policy) if policy else initial, asdict(settings), roots], sort_keys=True).encode()).hexdigest()


def _blocked(conn):
    if conn is None:
        return False
    return (conn.execute("SELECT 1 FROM objectives o LEFT JOIN objective_control c ON c.objective_id=o.id "
                         "WHERE o.cancelled=0 AND (c.status IS NULL OR c.status NOT IN ('accepted','legacy_completed')) LIMIT 1").fetchone() is not None
            or conn.execute("SELECT 1 FROM requests WHERE status NOT IN ('completed','cancelled') LIMIT 1").fetchone() is not None)


def _managed():
    from eidolon_cli import config
    return config.is_managed() or config.managed_scope.is_key_managed('organization.projects')


def _config_content(path):
    import yaml
    try:
        content = path.read_bytes() if path.exists() else b''
        parsed = yaml.safe_load(content)
        if parsed is not None and not isinstance(parsed, dict):
            raise ValueError('Profile configuration must be a YAML mapping; review it before preparing a repository draft')
        return content, parsed or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError('Profile configuration cannot be read safely; review its YAML before preparing a repository draft') from exc


def _source(home):
    from eidolon_cli import config_primitives as config
    content, raw = _config_content(home / 'config.yaml')
    expanded, _ = config._merge_managed_overlay(config._expand_env_vars(
        {'organization': raw.get('organization', {})}))
    return content, OrganizationSettings.from_config(expanded)


@contextmanager
def _context():
    from eidolon_constants import get_eidolon_home
    from eidolon_cli import config
    with config._CONFIG_LOCK:
        home = get_eidolon_home()
        from eidolon_cli.organization_config import profile_configuration_scope
        with profile_configuration_scope(home):
            content, settings = _source(home)
        path = home / 'organization' / 'state.db'
        if not path.exists():
            yield content, settings, None
            return
        # Do not construct OrganizationStore or get_service here: their cold
        # startup adopts file policy and may fence live claims on another host.
        conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('BEGIN')
            yield content, resolve_settings(conn, settings), conn
        finally:
            conn.close()


def _view(content, settings, conn):
    blockers = []
    if _blocked(conn):
        blockers.append('active_objectives')
    if _managed():
        blockers.append('managed_configuration')
    conflicts = list(settings.project_registry_conflicts)
    if conn is not None and conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='organization_project_registry_conflicts'").fetchone():
        retained = conn.execute('SELECT reasons FROM organization_project_registry_conflicts WHERE id=1').fetchone()
        if retained is not None and not conflicts:
            conflicts = json.loads(retained['reasons'])
    if conflicts:
        blockers.append('registry_conflict')
    return {
        'storage': 'profile-ledger',
        'ledgerProjects': [json.loads(row['project']) for row in registry_rows(conn)],
        'registryConflicts': conflicts,
        'repair': ("Restore each registered project's approved YAML root, recipe and team, or remove the conflicting YAML identity, then restart this profile's backend to reload its configuration. Exported identities remain available for review."
                   if conflicts else None),
        'version': 1, 'revision': _revision(content, settings, conn),
        'projects': [asdict(project) for project in settings.projects],
        'roots': [f'root{index}' for index in range(len(settings.read_roots))],
        'recipes': [{'id': grant.id, 'root': grant.execution['root']} for grant in settings.project_grants],
        'teams': sorted({settings.team, *(member.team for member in settings.roster or ())}),
        'blocked': bool(blockers), 'blockers': blockers,
    }


def project_setup():
    with _context() as (content, settings, conn):
        return _view(content, settings, conn)


def _validate(project, settings, conn):
    if _managed():
        raise ValueError('Project configuration is managed and cannot be changed here')
    if _blocked(conn):
        raise ValueError('Finish or cancel open objectives before configuring a project; their approved bindings must stay unchanged')
    if settings.project_registry_conflicts:
        raise ValueError('Registered projects require configuration repair before adding another project; refresh setup for details')
    existing = [asdict(item) for item in settings.projects]
    parse_projects([*existing, project], settings.read_roots, settings.project_grants)
    if project['team'] not in {settings.team, *(member.team for member in settings.roster or ())}:
        raise ValueError('Project team must select an existing configured team')


def project_draft(project, expected_revision):
    """Validate one list item for owner review; never persist or activate it.

    Preparing a draft never grants approval. The separate save action requires
    explicit confirmation and stores identities only in the profile ledger.
    """
    import yaml
    if not isinstance(expected_revision, str) or len(expected_revision) != 64:
        raise ValueError('Refresh project setup before preparing a draft; expectedRevision is required')
    with _context() as (content, settings, conn):
        revision = _revision(content, settings, conn)
        if expected_revision != revision:
            raise ValueError('Project configuration changed; refresh setup and review your selection before preparing a draft')
        _validate(project, settings, conn)
        # Emit only the newly validated identity. Never reveal private paths,
        # existing configuration, grant internals, or provider credentials.
        return {'version': 1, 'revision': revision, 'project': dict(project),
                'yaml': yaml.safe_dump([project], sort_keys=False, allow_unicode=True)}


def project_save(project, expected_revision, idempotency_key, *, confirm_save=False):
    """Deliberate owner registration, atomic solely within the profile ledger.

    Source bytes are observed under the shared observer lock, never rewritten.
    A concurrent non-cooperating file edit is observed on the normal next reload;
    this operation does not claim a transaction with an external text editor.
    """
    from contextlib import ExitStack
    import time
    from eidolon_cli.organization_config import profile_configuration_scope
    from eidolon_cli.organization_project_registry import binding_hash
    from eidolon_cli.organization_service import get_service, _ExecutionLock
    if confirm_save is not True:
        raise ValueError('Review and confirm saving this project identity in the profile ledger before activation')
    if not isinstance(expected_revision, str) or len(expected_revision) != 64:
        raise ValueError('Refresh project setup before saving; expectedRevision is required')
    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128:
        raise ValueError('A bounded idempotencyKey is required for project registration')
    digest = hashlib.sha256(json.dumps([project, expected_revision], sort_keys=True).encode()).hexdigest()
    service = get_service()
    store = service.store
    with service._lock, ExitStack() as locks:
        with store._write() as conn, profile_configuration_scope(service.home):
            row = conn.execute('SELECT input_hash,result FROM organization_project_registration_receipts WHERE idempotency_key=?',
                               (idempotency_key,)).fetchone()
            if row is not None:
                if row['input_hash'] != digest:
                    raise ValueError('Project registration idempotencyKey belongs to a different draft')
                return json.loads(row['result'])
            if service.active_execution_count:
                raise ValueError('Wait for organization executions to finish stopping before saving a project')
            content, raw_settings = _source(service.home)
            settings = resolve_settings(conn, raw_settings)
            canonical_roots = [str(Path(root).resolve()) for root in settings.read_roots]
            if expected_revision != _revision(content, settings, conn, canonical_roots=canonical_roots):
                raise ValueError('Project configuration changed; refresh setup and review your selection before saving')
            _validate(project, settings, conn)
            # A cancelled request may still be unwinding in another process.
            # All four supported capacity slots bound live and unwinding calls,
            # without opening one descriptor for every historical request.
            # The ledger writer prevents admission while checking these slots.
            for index in range(4):
                if not locks.enter_context(_ExecutionLock(service.home / 'organization' / 'capacity-locks', str(index))):
                    raise ValueError('An organization execution is still stopping in another runtime; save after it exits')
            validated = parse_projects([project], settings.read_roots, settings.project_grants)[0]
            conn.execute('INSERT INTO organization_project_registry VALUES (?,?,?,?)',
                         (project['id'], json.dumps(project, sort_keys=True), binding_hash(validated, settings, canonical_root=canonical_roots[int(validated.root[4:])]), time.time()))
            store._reload_configuration(conn, raw_settings)
            if store.settings.project_registry_conflicts:
                raise ValueError('Project authority changed while saving; refresh and review the binding again')
            result = {'version': 1, 'saved': True, 'project': dict(project),
                      'revision': _revision(content, store.settings, conn)}
            conn.execute('INSERT INTO organization_project_registration_receipts VALUES (?,?,?,?)',
                         (idempotency_key, digest, json.dumps(result, sort_keys=True), time.time()))
        service.settings = store.settings
    return result

"""Served-owner review of prepared validation, with no execution or service startup.

Registrations are append-only sidecars. They never alter legacy project bindings,
settings, existing objectives, tools or staff grants. Live preparation verifies
bounded bytes, not Docker availability or successful workload execution.
"""
from __future__ import annotations

from dataclasses import asdict
import os
from pathlib import Path
import platform
import sys
import tempfile
import time

from eidolon_cli.organization_metadata import _text, _uuid
from eidolon_cli.organization_owner_policy import (
    OwnerPolicyError, _connection, _identity, _schema, _source,
)
from eidolon_cli.organization_prepared_validation_contract import (
    _directory, _exact, _hash, _integer, legacy_project_binding_sha256,
    normalize_prepared_validation_contract,
)
from eidolon_cli.organization_project_dependency_bundle import (
    DependencyBundleLimits, _cleanup, _json, copy_verified_dependency_bundle, normalize_prepared_dependency_manifest,
)
from eidolon_cli.organization_project_node import IMAGE
from eidolon_cli.organization_project_snapshot_bundle import (
    ProjectSnapshotLimits, capture_project_snapshot,
)
from eidolon_cli.organization_project_validation import canonical, digest
from eidolon_cli.organization_project_validation_profile import normalize_validation_profile
from eidolon_cli.organization_projects import _binding
from eidolon_cli.organization_root_grants import directory_identity, roots_managed

_MAX_REGISTRATIONS = 10_000
_MAX_CANDIDATE_BYTES = 80 * 1024**2
_PREPARATION_SECONDS = 120
_PREPARATION_LIMITS = DependencyBundleLimits()
_TABLE = 'organization_prepared_validation_registrations'
_RECEIPTS = 'organization_prepared_validation_registration_receipts'
_DDL = (
    f'CREATE TABLE IF NOT EXISTS {_TABLE} ('
    'sequence INTEGER PRIMARY KEY, project_id TEXT NOT NULL, revision INTEGER NOT NULL, '
    'organization_id TEXT NOT NULL, owner_id TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), '
    'policy_generation INTEGER NOT NULL, source_revision TEXT NOT NULL, candidate TEXT NOT NULL, '
    'candidate_hash TEXT NOT NULL, receipt TEXT NOT NULL, receipt_hash TEXT NOT NULL, '
    'UNIQUE(project_id,revision))',
    f'CREATE TABLE IF NOT EXISTS {_RECEIPTS} ('
    'idempotency_key TEXT PRIMARY KEY, input_hash TEXT NOT NULL, '
    f'sequence INTEGER NOT NULL UNIQUE REFERENCES {_TABLE}(sequence))',
)
_DDL += tuple(
    f'CREATE TRIGGER IF NOT EXISTS {table}_immutable_{operation.lower()} '
    f'BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,\'Prepared registration is immutable\'); END'
    for table in (_TABLE, _RECEIPTS) for operation in ('UPDATE', 'DELETE')
)
_DDL += (
    f'CREATE TRIGGER IF NOT EXISTS {_TABLE}_no_replace BEFORE INSERT ON {_TABLE} '
    f'WHEN EXISTS(SELECT 1 FROM {_TABLE} WHERE sequence=NEW.sequence OR '
    'project_id=NEW.project_id AND revision=NEW.revision) '
    "BEGIN SELECT RAISE(ABORT,'Prepared registration is immutable'); END",
    f'CREATE TRIGGER IF NOT EXISTS {_RECEIPTS}_no_replace BEFORE INSERT ON {_RECEIPTS} '
    f'WHEN EXISTS(SELECT 1 FROM {_RECEIPTS} WHERE idempotency_key=NEW.idempotency_key OR sequence=NEW.sequence) '
    "BEGIN SELECT RAISE(ABORT,'Prepared registration is immutable'); END",
)
_CAS_FIELDS = {'expectedOrganizationId', 'expectedOwnerId', 'expectedGeneration',
               'sourceRevision', 'expectedSidecarRevision'}


def _sidecar_schema(conn):
    names = {sql.split('IF NOT EXISTS ', 1)[1].split()[0] for sql in _DDL}
    present = {row[0] for row in conn.execute('SELECT name FROM sqlite_master')} & names
    if not present:
        return False
    if present != names:
        raise OwnerPolicyError('unsupported_prepared_schema')
    normalize = lambda sql: ''.join(sql.lower().replace('if not exists', '').split()).rstrip(';')
    for sql in _DDL:
        name = sql.split('IF NOT EXISTS ', 1)[1].split()[0]
        actual = conn.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()[0]
        if normalize(actual) != normalize(sql):
            raise OwnerPolicyError('unsupported_prepared_schema')
    return True


def _sidecar_rows(conn):
    if not _sidecar_schema(conn):
        return []
    # Inspect bounded scalar metadata and SQL byte lengths before fetching any
    # manifest-bearing text. Historical manifests never accumulate in memory.
    rows = conn.execute(f'SELECT g.sequence,g.project_id,g.revision,g.organization_id,g.owner_id,'
        'g.enabled,g.policy_generation,g.source_revision,g.candidate_hash,g.receipt_hash,'
        'length(CAST(g.candidate AS BLOB)) AS candidate_bytes,'
        'length(CAST(g.receipt AS BLOB)) AS receipt_bytes,r.idempotency_key,r.input_hash '
        f'FROM {_TABLE} g LEFT JOIN {_RECEIPTS} r ON r.sequence=g.sequence '
        'ORDER BY g.sequence LIMIT ?', (_MAX_REGISTRATIONS + 9,)).fetchall()
    if len(rows) > _MAX_REGISTRATIONS + 8:
        raise OwnerPolicyError('prepared_registration_capacity')
    revisions = {}
    for sequence, row in enumerate(rows, 1):
        revision = revisions.get(row['project_id'], 0) + 1
        if (row['sequence'] != sequence or row['revision'] != revision
                or type(row['enabled']) is not int or row['enabled'] not in (0, 1)
                or not 0 < row['candidate_bytes'] <= _MAX_CANDIDATE_BYTES
                or not 0 < row['receipt_bytes'] <= 32768
                or type(row['idempotency_key']) is not str or not 1 <= len(row['idempotency_key']) <= 128
                or type(row['project_id']) is not str or not 1 <= len(row['project_id']) <= 64):
            raise OwnerPolicyError('prepared_registration_corrupt')
        for field in ('candidate_hash', 'receipt_hash', 'input_hash', 'source_revision'):
            _hash(row[field])
        _uuid(row['organization_id'], 'organizationId')
        _uuid(row['owner_id'], 'ownerId')
        _integer(row['policy_generation'], 'Policy generation', 1)
        revisions[row['project_id']] = revision
    if len(revisions) > 8:
        raise OwnerPolicyError('prepared_registration_corrupt')
    return rows


def _record(conn, row):
    # _sidecar_rows has already checked exact SQL byte bounds for this row.
    blobs = conn.execute(f"SELECT CASE WHEN typeof(candidate)='text' AND length(CAST(candidate AS BLOB))<=83886080 "
                         "THEN candidate END AS candidate,CASE WHEN typeof(receipt)='text' AND "
                         f"length(CAST(receipt AS BLOB))<=32768 THEN receipt END AS receipt FROM {_TABLE} WHERE sequence=?",
                         (row['sequence'],)).fetchone()
    if blobs is None or type(blobs['candidate']) is not str or type(blobs['receipt']) is not str:
        raise OwnerPolicyError('prepared_registration_corrupt')
    from eidolon_cli.organization_prepared_validation_finish import observed_finish_static_record, retain_finish_static_record
    identity = ('candidate', row['project_id'], row['sequence'])
    candidate = observed_finish_static_record(conn, identity, blobs['candidate'], row['candidate_hash'])
    observed = candidate is not None
    if not observed:
        candidate = _json(blobs['candidate'].encode('utf-8'), _MAX_CANDIDATE_BYTES, 'Retained prepared candidate')
    receipt = _json(blobs['receipt'].encode('utf-8'), 32768, 'Retained prepared receipt')
    if (type(candidate) is not dict or type(receipt) is not dict
            or (not observed and digest(candidate) != row['candidate_hash']) or digest(receipt) != row['receipt_hash']
            or type(candidate.get('enabled')) is not bool or candidate['enabled'] != bool(row['enabled'])
            or receipt.get('sidecarRevision') != row['sequence']
            or receipt.get('previousSidecarRevision') != row['sequence'] - 1
            or receipt.get('registrationRevision') != row['revision']
            or receipt.get('projectId') != row['project_id']
            or receipt.get('organizationId') != row['organization_id']
            or receipt.get('ownerId') != row['owner_id']
            or receipt.get('policyGeneration') != row['policy_generation']
            or receipt.get('idempotencyKey') != row['idempotency_key']):
        raise OwnerPolicyError('prepared_registration_corrupt')
    if not observed:
        retain_finish_static_record(conn, identity, blobs['candidate'], row['candidate_hash'])
    if candidate['enabled']:
        contract = candidate.get('contract', {})
        if (type(contract) is not dict or contract.get('revision') != row['revision']
                or contract.get('projectId') != row['project_id']
                or contract.get('organizationId') != row['organization_id']
                or contract.get('ownerId') != row['owner_id']
                or receipt.get('sourceRevision') != row['source_revision']
                or receipt.get('candidateHash') != row['candidate_hash']):
            raise OwnerPolicyError('prepared_registration_corrupt')
        return candidate, {**receipt, 'candidate': candidate}
    prior = conn.execute(f'SELECT candidate_hash,enabled FROM {_TABLE} WHERE project_id=? AND revision=?',
                         (row['project_id'], row['revision'] - 1)).fetchone()
    if (prior is None or prior['enabled'] != 1
            or candidate != {'enabled': False, 'projectId': row['project_id'], 'revision': row['revision'],
                             'revokedCandidateHash': prior['candidate_hash']}
            or receipt.get('disabled') is not True
            or receipt.get('previousRegistrationRevision') != row['revision'] - 1
            or receipt.get('registrationHash') != prior['candidate_hash']):
        raise OwnerPolicyError('prepared_registration_corrupt')
    return candidate, receipt


def current_prepared_registration(conn, project_id, *, organization_id, owner_id,
                                  policy_generation, source_revision):
    """Resolve exact current owner opt-in in the caller's served-ledger transaction.

    This is registration authority, not proof of Docker readiness or execution.
    New objectives must explicitly select its revision/hash; old objectives must
    never silently acquire or replace this binding.
    """
    rows = [row for row in _sidecar_rows(conn) if row['project_id'] == project_id]
    if not rows:
        return None
    row = rows[-1]
    candidate, receipt = _record(conn, row)
    if candidate['enabled'] is not True:
        return None
    identity = _identity(conn, organization_id)
    policy = _schema(conn)
    database = next((row[2] for row in conn.execute('PRAGMA database_list') if row[1] == 'main'), '')
    path = Path(database)
    if not database or path.name != 'state.db' or path.parent.name != 'organization' or path.resolve() != path:
        raise OwnerPolicyError('identity_conflict')
    settings, current_source, _ = _source(path.parent.parent, conn)
    if (identity['owner']['ownerId'] != owner_id or row['owner_id'] != owner_id
            or row['organization_id'] != organization_id):
        raise OwnerPolicyError('identity_conflict')
    if policy['generation'] != policy_generation or receipt['policyGeneration'] != policy_generation:
        raise OwnerPolicyError('generation_conflict')
    if current_source != source_revision or receipt['sourceRevision'] != source_revision:
        raise OwnerPolicyError('source_conflict')
    project = next((item for item in settings.projects if item.id == project_id), None)
    if project is None or project.root in settings.revoked_root_aliases or settings.project_registry_conflicts:
        raise OwnerPolicyError('prepared_registration_conflict')
    contract = candidate['contract']
    binding = _binding(project, settings)
    if (legacy_project_binding_sha256(binding) != contract['projectBindingSha256']
            or directory_identity(binding['readRoot']) != {key: contract['root'][key] for key in ('device', 'inode')}
            or directory_identity(contract['dependencies']['directory']) !=
            {key: contract['dependencies'][key] for key in ('device', 'inode')}):
        raise OwnerPolicyError('directory_identity_conflict')
    return {'sidecarRevision': row['sequence'], 'revision': row['revision'],
            'enabled': True, 'contract': contract, 'registrationSha256': row['candidate_hash'],
            'policyGeneration': receipt['policyGeneration'], 'sourceRevision': receipt['sourceRevision']}


def check_current_prepared_registration(conn, validated_registration, *, organization_id,
                                        owner_id, policy_generation, source_revision):
    """Fresh scalar fence for a full registration validated at admission/dispatch.

    The caller must retain the unmodified ``current_prepared_registration`` result
    and open a fresh served-ledger transaction for every checkpoint. This helper
    neither validates an arbitrary candidate nor replaces full dispatch validation.
    Its sidecar revision is the selected row sequence, not the global sequence:
    unrelated project updates cannot silently replace or invalidate this selection.
    No manifest-bearing blob is fetched, hashed, or normalized in this hot path.
    """
    _uuid(organization_id, 'organizationId')
    _uuid(owner_id, 'ownerId')
    _integer(policy_generation, 'Policy generation', 1)
    _hash(source_revision)
    _exact(validated_registration, {'sidecarRevision', 'revision', 'enabled', 'contract',
        'registrationSha256', 'policyGeneration', 'sourceRevision'}, 'Validated registration')
    registration = validated_registration
    contract = registration['contract']
    if type(contract) is not dict or registration['enabled'] is not True:
        raise OwnerPolicyError('prepared_registration_conflict')
    for field in ('sidecarRevision', 'revision', 'policyGeneration'):
        _integer(registration[field], field, 1)
    _hash(registration['registrationSha256'])
    _hash(registration['sourceRevision'])
    project_id = contract.get('projectId')
    if (type(project_id) is not str or not 1 <= len(project_id) <= 64
            or contract.get('revision') != registration['revision']
            or contract.get('organizationId') != organization_id or contract.get('ownerId') != owner_id
            or registration['policyGeneration'] != policy_generation
            or registration['sourceRevision'] != source_revision):
        raise OwnerPolicyError('prepared_registration_conflict')
    if not _sidecar_schema(conn):
        raise OwnerPolicyError('prepared_registration_conflict')
    maximum = conn.execute(f'SELECT max(sequence) FROM {_TABLE}').fetchone()[0]
    if type(maximum) is not int or not 1 <= maximum <= _MAX_REGISTRATIONS + 8:
        raise OwnerPolicyError('prepared_registration_capacity')
    row = conn.execute('SELECT sequence,revision,enabled,organization_id,owner_id,'
        f'policy_generation,source_revision,candidate_hash FROM {_TABLE} '
        'WHERE project_id=? ORDER BY revision DESC LIMIT 1', (project_id,)).fetchone()
    expected = (registration['sidecarRevision'], registration['revision'], 1,
                organization_id, owner_id, policy_generation, source_revision,
                registration['registrationSha256'])
    if row is None or tuple(row) != expected or type(row['enabled']) is not int:
        raise OwnerPolicyError('prepared_registration_conflict')
    identity = _identity(conn, organization_id)
    policy = _schema(conn)
    database = next((row[2] for row in conn.execute('PRAGMA database_list') if row[1] == 'main'), '')
    path = Path(database)
    if (identity['owner']['ownerId'] != owner_id or not database or path.name != 'state.db'
            or path.parent.name != 'organization' or path.resolve() != path):
        raise OwnerPolicyError('identity_conflict')
    if policy['generation'] != policy_generation:
        raise OwnerPolicyError('generation_conflict')
    settings, current_source, _ = _source(path.parent.parent, conn)
    if current_source != source_revision:
        raise OwnerPolicyError('source_conflict')
    project = next((item for item in settings.projects if item.id == project_id), None)
    if project is None or project.root in settings.revoked_root_aliases or settings.project_registry_conflicts:
        raise OwnerPolicyError('prepared_registration_conflict')
    binding = _binding(project, settings)
    if (legacy_project_binding_sha256(binding) != contract['projectBindingSha256']
            or directory_identity(binding['readRoot']) != {key: contract['root'][key] for key in ('device', 'inode')}
            or directory_identity(contract['dependencies']['directory']) !=
            {key: contract['dependencies'][key] for key in ('device', 'inode')}):
        raise OwnerPolicyError('directory_identity_conflict')


def _platform():
    system, architecture = sys.platform, platform.machine().lower()
    supported = (system == 'linux' and architecture in {'x86_64', 'amd64'}
                 and all(hasattr(os, flag) for flag in ('O_PATH', 'O_NOFOLLOW', 'O_DIRECTORY')))
    return {'supported': supported, 'system': system, 'architecture': architecture,
            'backend': 'linux-docker-pinned', 'runtimeReadiness': 'not_probed'}


def _view(conn, home, identity, policy, *, resolve_refs=True):
    from eidolon_cli.organization_policy import persisted_settings
    persisted = persisted_settings(conn)
    source_blocked = False
    try:
        settings, source_revision, _ = _source(home, conn)
    except (ValueError, OSError, OwnerPolicyError):
        # Revocation must remain available even when an approved folder vanished.
        settings = persisted
        source_revision = digest({'state': 'source_unreadable', 'policy': dict(policy)})
        source_blocked = True
    if settings is None:
        raise OwnerPolicyError('execution_not_initialized')
    rows = _sidecar_rows(conn)
    latest = {row['project_id']: row for row in rows}
    host = _platform()
    blockers = ['source_unreadable'] if source_blocked else []
    if roots_managed():
        blockers.append('managed_configuration')
    if settings.project_registry_conflicts:
        blockers.append('registry_conflict')
    if persisted != settings:
        blockers.append('source_policy_conflict')
    if not host['supported']:
        blockers.append('unsupported_platform')
    if len(rows) >= _MAX_REGISTRATIONS:
        blockers.append('prepared_registration_capacity')
    projects, registration_refs = [], []
    for project in settings.projects:
        row = latest.get(project.id)
        if row and (row['organization_id'] != identity['organizationId']
                    or row['owner_id'] != identity['owner']['ownerId']):
            raise OwnerPolicyError('identity_conflict')
        if resolve_refs and row and row['enabled'] and not source_blocked:
            try:
                current = current_prepared_registration(conn, project.id,
                    organization_id=identity['organizationId'], owner_id=identity['owner']['ownerId'],
                    policy_generation=policy['generation'], source_revision=source_revision)
                if current is not None:
                    registration_refs.append({'projectId': project.id, 'revision': current['revision'],
                                              'registrationSha256': current['registrationSha256']})
            except (OwnerPolicyError, ValueError, OSError):
                pass  # The retained registration stays visible and independently revocable.
        projects.append({**asdict(project), 'registrationRevision': row['revision'] if row else 0,
                         'registrationHash': row['candidate_hash'] if row else None,
                         'enabled': bool(row and row['enabled']),
                         'rootRevoked': project.root in settings.revoked_root_aliases})
    return {'capabilityVersion': 1, 'organizationId': identity['organizationId'],
            'ownerId': identity['owner']['ownerId'], 'policyGeneration': policy['generation'],
            'sourceRevision': source_revision, 'sidecarRevision': len(rows),
            'projects': projects, 'registrationRefs': registration_refs,
            'blockers': blockers, 'platform': host}, settings


def prepared_validation_setup(home, expected_organization_id):
    _uuid(expected_organization_id, 'expectedOrganizationId')
    with _connection(home) as conn:
        identity = _identity(conn, expected_organization_id)
        return _view(conn, home, identity, _schema(conn))[0]


def _context(conn, home, params):
    _uuid(params.get('expectedOrganizationId'), 'expectedOrganizationId')
    _uuid(params.get('expectedOwnerId'), 'expectedOwnerId')
    _integer(params.get('expectedGeneration'), 'Policy generation', 1)
    _integer(params.get('expectedSidecarRevision'), 'Sidecar revision')
    _hash(params.get('sourceRevision'))
    identity = _identity(conn, params['expectedOrganizationId'])
    policy = _schema(conn)
    view, settings = _view(conn, home, identity, policy, resolve_refs=False)
    for field, expected, reason in (
        ('ownerId', 'expectedOwnerId', 'identity_conflict'),
        ('policyGeneration', 'expectedGeneration', 'generation_conflict'),
        ('sourceRevision', 'sourceRevision', 'source_conflict'),
        ('sidecarRevision', 'expectedSidecarRevision', 'sidecar_conflict'),
    ):
        if view[field] != params[expected]:
            raise OwnerPolicyError(reason)
    if view['blockers']:
        raise OwnerPolicyError(view['blockers'][0])
    return view, settings


def _selected_project(view, settings, project_id):
    selected = next((project for project in view['projects'] if project['id'] == project_id), None)
    if selected is None or selected['rootRevoked']:
        raise ValueError('Select an existing project with current folder access')
    project = next(project for project in settings.projects if project.id == project_id)
    return selected, _binding(project, settings)


def _selection(value):
    value = _json(value, 40 * 1024**2, 'Prepared owner selection')
    _exact(value, {'projectId', 'enabled', 'source', 'dependencies', 'profile'}, 'Prepared owner selection')
    if value['enabled'] is not True:
        raise ValueError('Explicitly enable the reviewed prepared validation registration')
    _exact(value['source'], {'files', 'limits', 'executionOnlyHashes'}, 'Source selection')
    _exact(value['source']['limits'], {'max_files', 'max_file_bytes', 'max_total_bytes',
                                     'max_path_bytes', 'max_depth'}, 'Source limits')
    _exact(value['dependencies'], {'directory', 'manifest'}, 'Prepared dependencies')
    _directory(value['dependencies']['directory'])
    value['profile'] = normalize_validation_profile(value['profile'])
    return value


class _LiveAuthority:
    """Bounded checkpoint checks without keeping a stale SQLite read transaction."""
    def __init__(self, home, params):
        self.home, self.params = home, params
        self.last = 0.0

    def check(self, *, force=False):
        now = time.monotonic()
        if force or now - self.last >= .05:
            with _connection(self.home) as conn:
                _context(conn, self.home, self.params)
            self.last = now
        return True

    def is_set(self):
        self.check()
        return False


def _verify_dependencies(directory, identity, manifest, lockfile, packages, toolchain, authority, deadline):
    # The parent is a fresh 0700 directory. Cleanup uses the exact reserved inode
    # and descriptor-safe rmtree, never a broad owner-supplied path.
    from eidolon_cli.organization_project_compiler_bundle import _directory as open_directory
    with tempfile.TemporaryDirectory(prefix='eidolon-prepared-review-') as temporary:
        target = Path(temporary) / 'dependencies'
        receipt = copy_verified_dependency_bundle(directory, target, manifest,
            lockfile_bytes=lockfile, package_manifests=packages, expected_toolchain=toolchain,
            cancel=authority, deadline=deadline, limits=_PREPARATION_LIMITS,
            expected_source_identity=(identity['device'], identity['inode']))
        info = target.stat(follow_symlinks=False)
        with open_directory(temporary) as parent:
            _cleanup(parent, target.name, (info.st_dev, info.st_ino))
        if target.exists():
            raise OwnerPolicyError('preparation_cleanup_unconfirmed')
        return receipt


def _prepare(home, params):
    selection = _selection(params.get('selection'))
    with _connection(home) as conn:
        view, settings = _context(conn, home, params)
        project, binding = _selected_project(view, settings, selection['projectId'])
        prior = [row for row in _sidecar_rows(conn) if row['project_id'] == project['id']]
        if prior:
            _record(conn, prior[-1])
    authority = _LiveAuthority(home, params)
    root = directory_identity(binding['readRoot'])
    dependencies = selection['dependencies']
    dependency_identity = directory_identity(dependencies['directory'])
    deadline = time.monotonic() + _PREPARATION_SECONDS
    snapshot = capture_project_snapshot(binding['readRoot'], selection['source']['files'],
        root_alias=binding['root'], expected_root_identity=(root['device'], root['inode']),
        check_authorized=authority.check, limits=ProjectSnapshotLimits(**selection['source']['limits']),
        deadline_monotonic=deadline,
        reviewed_execution_only_hashes=selection['source']['executionOnlyHashes'])
    files = {item.path: item.content for item in snapshot.files}
    manifest = dependencies['manifest']
    if (type(manifest) is not dict or type(manifest.get('packageManifests')) is not list
            or any(type(item) is not dict or type(item.get('path')) is not str
                   for item in manifest['packageManifests'])):
        raise ValueError('Prepared dependencies require the exact package manifest inventory')
    try:
        lockfile = files['package-lock.json']
        packages = {item['path']: files[item['path']] for item in manifest['packageManifests']}
    except KeyError:
        raise ValueError('Select the lockfile and every exact package manifest') from None
    checked = normalize_prepared_dependency_manifest(manifest, lockfile_bytes=lockfile,
        package_manifests=packages, expected_toolchain=manifest.get('toolchain'), limits=_PREPARATION_LIMITS)
    contract = {'version': 1, 'contract': 'prepared-validation-v1',
        'organizationId': view['organizationId'], 'ownerId': view['ownerId'],
        'projectId': project['id'], 'projectBindingSha256': legacy_project_binding_sha256(binding),
        'revision': project['registrationRevision'] + 1,
        'root': {'alias': binding['root'], **root}, 'source': selection['source'],
        'dependencies': {'directory': dependencies['directory'], **dependency_identity,
            'manifest': checked['manifest'], 'toolchain': checked['manifest']['toolchain'],
            **{key: checked[key] for key in ('bundleSha256', 'inventorySha256', 'lockfileSha256')}},
        'profile': selection['profile'], 'runtime': {'backend': 'linux-docker-pinned',
            'image': IMAGE, 'allowLocalDocker': True, 'network': 'none', 'credentials': 'none'}}
    contract = normalize_prepared_validation_contract(contract, project_binding=binding,
        organization_id=view['organizationId'], owner_id=view['ownerId'],
        registration_revision=project['registrationRevision'] + 1,
        root_identity=(root['device'], root['inode']), lockfile_bytes=lockfile, package_manifests=packages)
    verified = _verify_dependencies(dependencies['directory'], dependency_identity, checked['manifest'], lockfile,
                                    packages, checked['manifest']['toolchain'], authority, deadline)
    if any(verified[key] != checked[key] for key in ('bundleSha256', 'inventorySha256', 'lockfileSha256')):
        raise OwnerPolicyError('dependency_integrity_conflict')
    if (root != directory_identity(binding['readRoot'])
            or dependency_identity != directory_identity(dependencies['directory'])):
        raise OwnerPolicyError('directory_identity_conflict')
    authority.check(force=True)
    with _connection(home) as conn:
        _context(conn, home, params)
        view, _ = _view(conn, home, _identity(conn, params['expectedOrganizationId']), _schema(conn))
    candidate = {'enabled': True, 'contract': contract,
        'selection': {'projectId': project['id'], 'enabled': True, 'source': contract['source'],
            'dependencies': {'directory': dependencies['directory'], 'manifest': checked['manifest']},
            'profile': contract['profile']},
        'verification': {'state': 'verified_bytes_runtime_unprobed',
            'snapshotSha256': snapshot.snapshot_sha256, 'sourceFiles': len(snapshot.files),
            'sourceBytes': snapshot.byte_count, 'dependencyFiles': len(checked['manifest']['files']),
            'dependencyBytes': sum(item['bytes'] for item in checked['manifest']['files']),
            'stagingCleanupConfirmed': True}}
    candidate_hash = digest(candidate)
    preview_version = digest({**{key: params[key] for key in _CAS_FIELDS}, 'candidateHash': candidate_hash})
    return {**view, 'candidate': candidate, 'candidateHash': candidate_hash, 'previewVersion': preview_version}


def preview_prepared_validation(home, params):
    _exact(params, _CAS_FIELDS | {'selection'}, 'Prepared preview')
    return _prepare(home, params)


def _replay(conn, params):
    row = next((row for row in _sidecar_rows(conn) if row['idempotency_key'] == params['idempotencyKey']), None)
    if row is None:
        return None
    identity = _identity(conn, params['expectedOrganizationId'])
    _, result = _record(conn, row)
    if (result['organizationId'] != identity['organizationId']
            or result['ownerId'] != identity['owner']['ownerId']
            or params['expectedOwnerId'] != identity['owner']['ownerId']):
        raise OwnerPolicyError('identity_conflict')
    if row['input_hash'] != digest(params):
        raise OwnerPolicyError('idempotency_conflict')
    # A replay reports historical commit only, never renews authority/readiness.
    return {'capabilityVersion': 1, 'receipt': result}


def save_prepared_validation(home, params):
    _exact(params, _CAS_FIELDS | {'candidate', 'candidateHash', 'previewVersion',
                                'idempotencyKey', 'confirmed'}, 'Prepared registration')
    if params['confirmed'] is not True:
        raise ValueError('Review and explicitly confirm this exact prepared registration')
    _text(params['idempotencyKey'], 'idempotencyKey', 128)
    _hash(params['candidateHash'])
    _hash(params['previewVersion'])
    params = _json(params, 80 * 1024**2, 'Prepared registration')
    with _connection(home) as conn:
        replay = _replay(conn, params)
        if replay is not None:
            return replay
    candidate = params['candidate']
    if type(candidate) is not dict or digest(candidate) != params['candidateHash']:
        raise OwnerPolicyError('candidate_conflict')
    cas = {key: params[key] for key in _CAS_FIELDS}
    preview = _prepare(home, {**cas, 'selection': candidate.get('selection')})
    if (preview['candidate'] != candidate or preview['candidateHash'] != params['candidateHash']
            or preview['previewVersion'] != params['previewVersion']):
        raise OwnerPolicyError('preview_conflict')
    with _connection(home, write=True) as conn:
        replay = _replay(conn, params)
        if replay is not None:
            return replay
        view, settings = _context(conn, home, params)
        project, binding = _selected_project(view, settings, candidate['contract']['projectId'])
        contract = candidate['contract']
        if (directory_identity(binding['readRoot']) != {key: contract['root'][key] for key in ('device', 'inode')}
                or directory_identity(contract['dependencies']['directory']) !=
                {key: contract['dependencies'][key] for key in ('device', 'inode')}):
            raise OwnerPolicyError('directory_identity_conflict')
        for statement in _DDL:
            conn.execute(statement)
        receipt = {'organizationId': view['organizationId'], 'ownerId': view['ownerId'],
            'policyGeneration': view['policyGeneration'], 'sourceRevision': view['sourceRevision'],
            'previousSidecarRevision': view['sidecarRevision'], 'sidecarRevision': view['sidecarRevision'] + 1,
            'projectId': project['id'], 'registrationRevision': contract['revision'],
            'candidateHash': params['candidateHash'], 'previewVersion': params['previewVersion'],
            'idempotencyKey': params['idempotencyKey'], 'candidate': candidate}
        stored_receipt = {key: value for key, value in receipt.items() if key != 'candidate'}
        conn.execute(f'INSERT INTO {_TABLE} VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (receipt['sidecarRevision'], project['id'], contract['revision'], view['organizationId'],
             view['ownerId'], 1, view['policyGeneration'], view['sourceRevision'], canonical(candidate),
             digest(candidate), canonical(stored_receipt), digest(stored_receipt)))
        conn.execute(f'INSERT INTO {_RECEIPTS} VALUES (?,?,?)',
                     (params['idempotencyKey'], digest(params), receipt['sidecarRevision']))
    return {'capabilityVersion': 1, 'receipt': receipt}


def revoke_prepared_validation(home, params):
    """Disable one exact registration without reading source or dependencies."""
    _exact(params, {'expectedOrganizationId', 'expectedOwnerId', 'expectedGeneration',
        'expectedSidecarRevision', 'projectId', 'registrationRevision', 'registrationHash',
        'idempotencyKey', 'confirmed'}, 'Prepared revocation')
    if params['confirmed'] is not True:
        raise ValueError('Explicitly confirm disabling this prepared registration')
    _uuid(params['expectedOrganizationId'], 'expectedOrganizationId')
    _uuid(params['expectedOwnerId'], 'expectedOwnerId')
    _integer(params['expectedGeneration'], 'Policy generation', 1)
    _integer(params['expectedSidecarRevision'], 'Sidecar revision')
    _integer(params['registrationRevision'], 'Registration revision', 1)
    _hash(params['registrationHash'])
    _text(params['idempotencyKey'], 'idempotencyKey', 128)
    with _connection(home, write=True) as conn:
        replay = _replay(conn, params)
        if replay is not None:
            return replay
        identity = _identity(conn, params['expectedOrganizationId'])
        policy = _schema(conn)
        if identity['owner']['ownerId'] != params['expectedOwnerId']:
            raise OwnerPolicyError('identity_conflict')
        if policy['generation'] != params['expectedGeneration']:
            raise OwnerPolicyError('generation_conflict')
        rows = _sidecar_rows(conn)
        if len(rows) != params['expectedSidecarRevision']:
            raise OwnerPolicyError('sidecar_conflict')
        selected = [row for row in rows if row['project_id'] == params['projectId']]
        if not selected:
            raise OwnerPolicyError('prepared_registration_missing')
        prior = selected[-1]
        old, _ = _record(conn, prior)
        if (prior['candidate_hash'] != params['registrationHash']
                or prior['revision'] != params['registrationRevision'] or old['enabled'] is not True):
            raise OwnerPolicyError('prepared_registration_conflict')
        candidate = {'enabled': False, 'projectId': prior['project_id'], 'revision': prior['revision'] + 1,
                     'revokedCandidateHash': prior['candidate_hash']}
        receipt = {'organizationId': identity['organizationId'], 'ownerId': identity['owner']['ownerId'],
            'policyGeneration': policy['generation'], 'previousSidecarRevision': len(rows),
            'sidecarRevision': len(rows) + 1, 'projectId': prior['project_id'],
            'registrationRevision': prior['revision'] + 1, 'previousRegistrationRevision': prior['revision'],
            'registrationHash': prior['candidate_hash'], 'idempotencyKey': params['idempotencyKey'],
            'disabled': True}
        conn.execute(f'INSERT INTO {_TABLE} VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (receipt['sidecarRevision'], prior['project_id'], receipt['registrationRevision'],
             identity['organizationId'], identity['owner']['ownerId'], 0, policy['generation'],
             prior['source_revision'], canonical(candidate), digest(candidate), canonical(receipt), digest(receipt)))
        conn.execute(f'INSERT INTO {_RECEIPTS} VALUES (?,?,?)',
                     (params['idempotencyKey'], digest(params), receipt['sidecarRevision']))
    return {'capabilityVersion': 1, 'receipt': receipt}

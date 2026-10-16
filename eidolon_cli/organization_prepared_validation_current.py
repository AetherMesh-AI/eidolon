"""Live source/owner/dependency checks for prepared finish, review and acceptance."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import time

from eidolon_cli.organization_prepared_validation_authority import require_prepared_authority
from eidolon_cli.organization_prepared_validation_stage import validation_context
from eidolon_cli.organization_project_effective_snapshot import capture_effective_project_snapshot
from eidolon_cli.organization_project_snapshot_bundle import ProjectSnapshotLimits, capture_project_snapshot
from eidolon_cli.organization_project_validation import canonical


@dataclass(frozen=True)
class _AuthorityObservation:
    envelope: str
    retained_manifest: str
    authority: object
    authority_identity: str
    fresh: object
    dependency: object
    value: str
    check_claim: object


def _checkpoint(store, authority, objective_id, check_claim=None):
    last_check = 0.0
    grant = authority['contract']

    def check(*, force=False):
        nonlocal last_check
        now = time.monotonic()
        if not force and now - last_check < .05:
            return True
        if check_claim is not None:
            check_claim()
        from eidolon_cli.organization_prepared_validation_setup import check_current_prepared_registration
        with store._connect() as current:
            store._require_current_policy(current)
            check_current_prepared_registration(current, authority['registration'],
                organization_id=grant['organizationId'], owner_id=grant['ownerId'],
                policy_generation=authority['policyGeneration'],
                source_revision=authority['registration']['sourceRevision'])
            authors = store._project_execution_authors(current, objective_id, authority['project'])
            if sorted(authors) != authority['contributors']:
                raise ValueError('Prepared contributor authority changed during verification')
        last_check = now
        return True
    return check


def _authority_value(store, conn, envelope, authority, fresh, check):
    from eidolon_cli.organization_project_effective_snapshot import verify_effective_snapshot_lineage
    verify_effective_snapshot_lineage(conn, fresh, authority['project']['readRoot'], check_authorized=check)
    if any(getattr(fresh, {'snapshotSha256': 'snapshot_sha256', 'preimageSha256': 'preimage_sha256',
                          'managedRevisionsSha256': 'managed_revisions_sha256'}[key]) != value
           for key, value in envelope['source'].items()):
        raise ValueError('Prepared source or managed lineage changed after execution')
    from eidolon_cli.organization_prepared_validation_review_scope import reviewed_source_evidence, review_scope_artifact
    hashes = reviewed_source_evidence(store, conn, envelope['objectiveId'], envelope['projectId'], fresh)
    scope_id = 'prepared_scope_' + hashlib.sha256(envelope['setId'].encode()).hexdigest()
    scope = review_scope_artifact(conn, scope_id)
    if scope is None or json.loads(scope['content'])['reviewedSourceEvidence'] != hashes:
        raise ValueError('Prepared review scope no longer matches current reviewed source work')
    check(force=True)
    return {'context': validation_context(authority, fresh, envelope['objectiveId']),
            'contributorIds': authority['contributors'], 'sourceEvidence': {**hashes, scope_id: scope['sha256']}}


@contextmanager
def observe_current_prepared_authority(store, conn, envelope, retained_snapshot, *, check_claim=None):
    """Observe bytes once; the caller owns the dependency capability's lifetime."""
    authority = require_prepared_authority(store, conn, envelope['objectiveId'], envelope['projectId'])
    grant = authority['contract']
    check = _checkpoint(store, authority, envelope['objectiveId'], check_claim)
    with store._connect() as capture_connection:
        fresh = capture_effective_project_snapshot(capture_connection, envelope['objectiveId'], authority['project']['readRoot'],
            grant['source']['files'], root_alias=grant['root']['alias'],
            expected_root_identity=tuple(grant['root'][key] for key in ('device', 'inode')),
            check_authorized=check, limits=ProjectSnapshotLimits(**grant['source']['limits']),
            deadline_monotonic=time.monotonic() + 120,
            reviewed_execution_only_hashes=grant['source']['executionOnlyHashes'])
    fresh = _share_observed_source(fresh, retained_snapshot)
    from eidolon_cli.organization_project_dependency_verification import _observe_dependency_bundle
    dependencies = grant['dependencies']
    files = {item.path: item.content for item in fresh.effective.files}
    manifest = dependencies['manifest']
    with _observe_dependency_bundle(dependencies['directory'], manifest,
            lockfile_bytes=files['package-lock.json'],
            package_manifests={item['path']: files[item['path']] for item in manifest['packageManifests']},
            expected_toolchain=dependencies['toolchain'],
            expected_source_identity=tuple(dependencies[key] for key in ('device', 'inode')),
            expected_bundle_sha256=grant['profile']['bundleSha256'],
            check_authorized=check, deadline=time.monotonic() + 120) as dependency:
        value = _authority_value(store, conn, envelope, authority, fresh, check)
        yield _AuthorityObservation(canonical(envelope), canonical(retained_snapshot.manifest()),
            authority, _authority_identity(authority), fresh, dependency, canonical(value), check_claim)


def recheck_observed_prepared_authority(store, conn, envelope, retained_snapshot, observation, *, exact_retained_inputs=False):
    """Revalidate live authority; exact private CAS may reuse immutable lineage."""
    if (type(observation) is not _AuthorityObservation or observation.envelope != canonical(envelope)
            or observation.retained_manifest != canonical(retained_snapshot.manifest())):
        raise ValueError('Prepared finish observation belongs to another source or generation')
    if exact_retained_inputs:
        from eidolon_cli.organization_prepared_validation_finish import _ACTIVE, _require_inventory, _require_token
        token = _ACTIVE.get()
        if token is None or token.targets.get(envelope['projectId'], {}).get('observation') is not observation:
            raise ValueError('Prepared retained-input reuse requires its private active proof scope')
        _require_token(token, store, conn)
        _require_inventory(token, conn)
    authority = require_prepared_authority(store, conn, envelope['objectiveId'], envelope['projectId'])
    if (_authority_identity(authority) != observation.authority_identity
            or (not exact_retained_inputs and authority != observation.authority)):
        raise ValueError('Prepared authority changed after its byte observation')
    check = _checkpoint(store, authority, envelope['objectiveId'], observation.check_claim)
    if exact_retained_inputs:
        check(force=True)
        return json.loads(observation.value)
    value = _authority_value(store, conn, envelope, authority, observation.fresh, check)
    if canonical(value) != observation.value:
        raise ValueError('Prepared source evidence changed after its byte observation')
    return value


def recheck_finish_external_observation(store, observation):
    """Fence observed files against the pre-transition target, including rejection.

    The write transaction may legitimately advance the objective round. Recheck
    original source bytes directly and read external authority through a fresh
    connection, which still sees the committed pre-transition contributor set.
    """
    if type(observation) is not _AuthorityObservation:
        raise ValueError('Prepared finish requires its original byte observation')
    authority, envelope = observation.authority, json.loads(observation.envelope)
    check = _checkpoint(store, authority, envelope['objectiveId'], observation.check_claim)
    check(force=True)
    original = observation.fresh.original
    fresh = capture_project_snapshot(authority['project']['readRoot'], [item.path for item in original.files],
        root_alias=original.root_alias, expected_root_identity=original.root_identity,
        check_authorized=check, limits=original.limits, deadline_monotonic=time.monotonic() + 120,
        reviewed_execution_only_hashes={item.path: item.sha256 for item in original.files if item.execution_only})
    if fresh.manifest() != original.manifest():
        raise ValueError('Prepared source preimage changed after finish preflight')
    from eidolon_cli.organization_project_dependency_verification import _recheck_dependency_observation
    _recheck_dependency_observation(observation.dependency, check_authorized=check, deadline=time.monotonic() + 120)
    check(force=True)
    return True


# Final hot owner reads are small metadata, never manifest-bearing candidates or
# receipt history. These admission caps bound raw reads and schema/PRAGMA inputs;
# decoded/transient memory is separate from the finish token's retained-byte cap.
_MAX_OWNER_SOURCE_BYTES = 8 * 1024**2
_MAX_OWNER_SCHEMA_ROWS = 4096


def _finish_owner_source_bounds(conn):
    """Bound every raw input consumed by the ready-owner checkpoint in SQL."""
    from eidolon_cli.organization_prepared_validation_inventory import _connection
    _connection(conn)
    schema_count = conn.execute('SELECT count(*) FROM (SELECT 1 FROM main.sqlite_schema LIMIT ?)',
                                (_MAX_OWNER_SCHEMA_ROWS + 1,)).fetchone()[0]
    if schema_count > _MAX_OWNER_SCHEMA_ROWS:
        raise ValueError('Prepared final owner source schema exceeds capacity')
    if conn.execute("SELECT 1 FROM main.sqlite_schema WHERE typeof(type)<>'text' OR "
            "type NOT IN ('table','index','view','trigger') OR typeof(name)<>'text' OR "
            "length(CAST(name AS BLOB))>256 OR typeof(tbl_name)<>'text' OR "
            "length(CAST(tbl_name AS BLOB))>256 OR (sql IS NOT NULL AND "
            "(typeof(sql)<>'text' OR length(CAST(sql AS BLOB))>65536)) LIMIT 1").fetchone():
        raise ValueError('Prepared final owner source schema exceeds capacity')
    total = conn.execute('SELECT coalesce(sum(length(CAST(name AS BLOB))+'
        'length(CAST(type AS BLOB))+length(CAST(tbl_name AS BLOB))+coalesce(length(CAST(sql AS BLOB)),0)),0) '
        'FROM main.sqlite_schema').fetchone()[0]
    if total > _MAX_OWNER_SOURCE_BYTES:
        raise ValueError('Prepared final owner source exceeds metadata capacity')

    def bounded(table, texts, integers=(), *, numbers=(), where='1', maximum=1, optional=False, exact=None):
        nonlocal total
        found = conn.execute("SELECT CASE WHEN type='table' THEN 1 ELSE 0 END FROM main.sqlite_schema "
                             "WHERE name=?", (table,)).fetchone()
        if found is None:
            if optional:
                return
            raise ValueError('Prepared final owner source table is unavailable')
        if found[0] != 1:
            raise ValueError('Prepared final owner source requires an ordinary table')
        if exact is not None:
            columns = tuple(row['name'] for row in conn.execute(f'PRAGMA main.table_xinfo({table})'))
            if columns != tuple(exact.split()):
                raise ValueError('Prepared final owner source columns changed')
        count = conn.execute(f'SELECT count(*) FROM (SELECT 1 FROM main.{table} WHERE {where} LIMIT ?)',
                             (maximum + 1,)).fetchone()[0]
        if count > maximum:
            raise ValueError('Prepared final owner source exceeds metadata capacity')
        invalid = [f"typeof({name})<>'text' OR length(CAST({name} AS BLOB))>{limit}" for name, limit in texts.items()]
        invalid += [f"typeof({name})<>'integer' OR {name}<0" for name in integers]
        invalid += [f"typeof({name}) NOT IN ('integer','real')" for name in numbers]
        if conn.execute(f'SELECT 1 FROM main.{table} WHERE ({where}) AND ('+
                        ' OR '.join(invalid)+') LIMIT 1').fetchone():
            raise ValueError('Prepared final owner source has invalid bounded metadata')
        sizes = '+'.join([f'length(CAST({name} AS BLOB))' for name in texts] + [str(8 * (len(integers) + len(numbers)))])
        total += conn.execute(f'SELECT coalesce(sum({sizes}),0) FROM main.{table} WHERE {where}').fetchone()[0]
        if total > _MAX_OWNER_SOURCE_BYTES:
            raise ValueError('Prepared final owner source exceeds metadata capacity')

    bounded('organization_metadata', {'organization_id': 36, 'owner_id': 36,
        'display_name': 640, 'owner_display_name': 640, 'created_at': 128, 'updated_at': 128},
        ('singleton', 'schema_version', 'revision'), exact='singleton schema_version organization_id display_name '
        'owner_id owner_display_name revision created_at updated_at')
    bounded('organization_policy', {'fingerprint': 64, 'settings': 2 * 1024**2}, ('generation',), where='id=1')
    for table in ('organization_configuration', 'organization_model_routing_config', 'organization_owner_budget_policy'):
        bounded(table, {'configuration': 2 * 1024**2}, where='id=1', optional=table == 'organization_owner_budget_policy')
    bounded('organization_root_grants', {'alias': 64, 'path': 4096, 'source_prefix': 65536},
        ('device', 'inode'), maximum=8, optional=True, exact='alias path source_prefix device inode')
    bounded('organization_root_revocations', {'alias': 64}, maximum=8, optional=True)
    bounded('organization_root_revocation_reviews', {'alias': 64}, maximum=8, optional=True)
    # SELECT * in registry_rows also returns created, whose dynamic SQLite type
    # must be checked before the normal reader materializes it.
    bounded('organization_project_registry', {'id': 64, 'project': 65536, 'binding_hash': 64},
        numbers=('created',), maximum=8, optional=True, exact='id project binding_hash created')
    from eidolon_cli.organization_prepared_validation_setup import _MAX_REGISTRATIONS
    bounded('organization_prepared_validation_registrations', {'project_id': 64, 'organization_id': 36,
        'owner_id': 36, 'source_revision': 64, 'candidate_hash': 64},
        ('sequence', 'revision', 'enabled', 'policy_generation'), maximum=_MAX_REGISTRATIONS + 8)


def recheck_finish_owner_source(store, conn, observation):
    """Observe late caller-transaction owner changes without replaying round work."""
    if type(observation) is not _AuthorityObservation:
        raise ValueError('Prepared finish requires its original owner observation')
    _finish_owner_source_bounds(conn)
    from eidolon_cli.organization_prepared_validation_setup import check_current_prepared_registration
    authority = observation.authority
    grant = authority['contract']
    check_current_prepared_registration(conn, authority['registration'],
        organization_id=grant['organizationId'], owner_id=grant['ownerId'],
        policy_generation=authority['policyGeneration'],
        source_revision=authority['registration']['sourceRevision'])


def current_prepared_authority(store, conn, envelope, retained_snapshot, *, require_review=True):
    from eidolon_cli.organization_prepared_validation_finish import observed_finish_authority
    observed = observed_finish_authority(store, conn, envelope, retained_snapshot, require_review=require_review)
    if observed is not None:
        return observed
    with observe_current_prepared_authority(store, conn, envelope, retained_snapshot) as observation:
        return json.loads(observation.value)


def verify_current_prepared_pair(store, conn, objective_id, project_id, *, require_review=True):
    from eidolon_cli.organization_prepared_validation_verification import verify_latest_prepared_validation_set
    return verify_latest_prepared_validation_set(conn, objective_id, project_id,
        current_authority=lambda connection, envelope, snapshot: current_prepared_authority(
            store, connection, envelope, snapshot, require_review=require_review), require_review=require_review)


def _authority_identity(authority):
    """Small dynamic identity; exact grant/candidate bodies are separately CASed."""
    return canonical({**{key: value for key, value in authority.items() if key not in {'contract', 'registration'}},
        'registration': {key: value for key, value in authority['registration'].items() if key != 'contract'}})


def _share_observed_source(fresh, retained_snapshot):
    """Deduplicate equal immutable bytes only after independent live capture."""
    from eidolon_cli.organization_project_effective_snapshot import EffectiveProjectSnapshot
    from eidolon_cli.organization_project_snapshot_bundle import ProjectSnapshotBundle, ProjectSnapshotFile
    retained = {item.path: item.content for item in retained_snapshot.files}
    def share(snapshot):
        files = []
        for item in snapshot.files:
            value = retained.get(item.path)
            content = value if value is not None and value == item.content else item.content
            files.append(ProjectSnapshotFile(item.path, content, item.mode, item.execution_only))
        return ProjectSnapshotBundle(snapshot.root_alias, snapshot.root_identity, tuple(files), snapshot.limits)
    return EffectiveProjectSnapshot(fresh.objective_id, share(fresh.original), share(fresh.effective), fresh.managed_revisions)

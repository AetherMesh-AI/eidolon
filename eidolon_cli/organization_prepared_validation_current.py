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
    authority: str
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
            canonical(authority), fresh, dependency, canonical(value), check_claim)


def recheck_observed_prepared_authority(store, conn, envelope, retained_snapshot, observation):
    """Revalidate SQL authority and lineage; never cache receipt or review success."""
    if (type(observation) is not _AuthorityObservation or observation.envelope != canonical(envelope)
            or observation.retained_manifest != canonical(retained_snapshot.manifest())):
        raise ValueError('Prepared finish observation belongs to another source or generation')
    authority = require_prepared_authority(store, conn, envelope['objectiveId'], envelope['projectId'])
    if canonical(authority) != observation.authority:
        raise ValueError('Prepared authority changed after its byte observation')
    check = _checkpoint(store, authority, envelope['objectiveId'], observation.check_claim)
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
    authority, envelope = json.loads(observation.authority), json.loads(observation.envelope)
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

"""Backend-only preparation and immutable two-step reservations.

No model response can enter this path as a runtime result. Registration is still
an explicit objective selection, and source capture confers no execution grant.
"""
import json
import time
import uuid

from eidolon_cli import organization_prepared_validation_contract as contract
from eidolon_cli.organization_prepared_validation_authority import require_prepared_authority
from eidolon_cli.organization_project_effective_snapshot import capture_effective_project_snapshot
from eidolon_cli.organization_project_snapshot_bundle import ProjectSnapshotLimits
from eidolon_cli.organization_project_validation import canonical, digest
from eidolon_cli.organization_project_validation_profile import validation_set_identity

SOURCE_PROOF_SCHEMA = '''
CREATE TABLE IF NOT EXISTS prepared_validation_source_proofs (
 set_id TEXT PRIMARY KEY REFERENCES prepared_validation_sets(id),
 record TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS prepared_source_proof_no_replace BEFORE INSERT ON prepared_validation_source_proofs
 WHEN EXISTS(SELECT 1 FROM prepared_validation_source_proofs WHERE set_id=NEW.set_id)
 BEGIN SELECT RAISE(ABORT,'Prepared source proofs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS prepared_source_proof_no_update BEFORE UPDATE ON prepared_validation_source_proofs
 BEGIN SELECT RAISE(ABORT,'Prepared source proofs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS prepared_source_proof_no_delete BEFORE DELETE ON prepared_validation_source_proofs
 BEGIN SELECT RAISE(ABORT,'Prepared source proofs are immutable'); END;
'''
MAX_SOURCE_PROOF_BYTES = 16 * 1024**2


def validation_context(authority, snapshot, objective_id):
    grant = authority['contract']
    return {'profile': grant['profile'], 'organization_id': grant['organizationId'], 'owner_id': grant['ownerId'],
            'owner_grant_sha256': authority['ownerGrantSha256'],
            'objective_binding_sha256': authority['objectiveBindingSha256'],
            'objective_id': objective_id, 'project_id': grant['projectId'], 'round_number': authority['round'],
            'policy_generation': authority['policyGeneration'], 'policy_fingerprint': authority['policyFingerprint'],
            'snapshot_sha256': snapshot.snapshot_sha256, 'preimage_sha256': snapshot.preimage_sha256,
            'managed_revisions_sha256': snapshot.managed_revisions_sha256}


def new_validation_envelope(context, request_id, generation):
    authority = contract.validation_authority_sha256(**{key: context[key] for key in (
        'organization_id', 'owner_id', 'owner_grant_sha256', 'objective_binding_sha256',
        'policy_generation', 'policy_fingerprint')})
    identity = validation_set_identity(context['profile'], objective_id=context['objective_id'],
        project_id=context['project_id'], source_sha256=context['snapshot_sha256'], authority_sha256=authority)
    value = {'version': 1, 'contract': 'prepared-validation-set-v1',
        'setId': 'prepared_set_' + uuid.uuid4().hex, 'requestId': request_id,
        'objectiveId': context['objective_id'], 'projectId': context['project_id'],
        'round': context['round_number'], 'generation': generation,
        'ownerGrantSha256': context['owner_grant_sha256'],
        'objectiveBindingSha256': context['objective_binding_sha256'],
        'policy': {'generation': context['policy_generation'], 'fingerprint': context['policy_fingerprint']},
        'source': {'snapshotSha256': context['snapshot_sha256'], 'preimageSha256': context['preimage_sha256'],
                   'managedRevisionsSha256': context['managed_revisions_sha256']},
        'identity': identity,
        'steps': [{**step, 'runId': 'prepared_run_' + uuid.uuid4().hex} for step in identity['requiredSteps']]}
    return contract.normalize_validation_set_envelope(value, **context)


def retain_source_proof(conn, envelope, snapshot):
    from eidolon_cli.organization_prepared_validation_ledger import _require_write_transaction
    _require_write_transaction(conn)
    value = snapshot.source_evidence()
    if any(value[key] != item for key, item in envelope['source'].items()):
        raise ValueError('Prepared source proof differs from its reserved execution snapshot')
    encoded = canonical(value)
    if len(encoded.encode('utf-8')) > MAX_SOURCE_PROOF_BYTES:
        raise ValueError('Prepared source metadata exceeds its retained proof limit')
    conn.execute('INSERT INTO prepared_validation_source_proofs VALUES (?,?,?)',
                 (envelope['setId'], encoded, digest(value)))


def capture_for_claim(store, claim, cancel):
    """Capture outside a write transaction; callers must fence again at reservation."""
    project_id = json.loads(claim['payload']).get('projectId')
    with store._write() as conn:
        request = store._owned(conn, claim)
        if request is None or cancel.is_set():
            raise ValueError('Prepared source capture requires a current uncancelled lease')
        store._require_project_execution_controller(request)
        authority = require_prepared_authority(store, conn, claim['objective_id'], project_id, execution=True)
    grant = authority['contract']
    root_identity = tuple(grant['root'][key] for key in ('device', 'inode'))
    source = authority['project']['readRoot']

    last_check = 0.0

    def check():
        nonlocal last_check
        if cancel.is_set():
            raise ValueError('Prepared source capture was cancelled')
        now = time.monotonic()
        if now - last_check < .05:
            return True
        with store._connect() as conn:
            if store._owned(conn, claim) is None:
                raise ValueError('Prepared source capture lost its live lease')
            from eidolon_cli.organization_budget import budget_view
            if time.time() >= budget_view(conn, claim['objective_id'], store.settings)['deadlineTimestamp']:
                raise ValueError('Prepared source capture objective deadline expired')
            # This read never materializes dependency inventories. A full current
            # registration/author check occurs again inside the reservation txn.
            from eidolon_cli.organization_prepared_validation_setup import check_current_prepared_registration
            check_current_prepared_registration(conn, authority['registration'],
                organization_id=grant['organizationId'], owner_id=grant['ownerId'],
                policy_generation=authority['policyGeneration'],
                source_revision=authority['registration']['sourceRevision'])
            if sorted(store._project_execution_authors(conn, claim['objective_id'], authority['project'])) != authority['contributors']:
                raise ValueError('Prepared source contributors changed')
            current = conn.execute('SELECT round FROM objective_control WHERE objective_id=?',
                                   (claim['objective_id'],)).fetchone()
            if current is None or current[0] != authority['round']:
                raise ValueError('Prepared capture belongs to a superseded round')
        last_check = now
        return True

    with store._connect() as conn:
        snapshot = capture_effective_project_snapshot(conn, claim['objective_id'], source,
            grant['source']['files'], root_alias=grant['root']['alias'], expected_root_identity=root_identity,
            check_authorized=check, limits=ProjectSnapshotLimits(**grant['source']['limits']),
            deadline_monotonic=time.monotonic() + 120, cancel=cancel,
            reviewed_execution_only_hashes=grant['source']['executionOnlyHashes'])
    return authority, snapshot


def _same_snapshot(left, right):
    return (left.snapshot_sha256, left.preimage_sha256, left.managed_revisions_sha256) == (
        right.snapshot_sha256, right.preimage_sha256, right.managed_revisions_sha256)


def run_prepared_project_stage(store, claim, cancel):
    """Reserve a complete pair, then invoke the fixed trusted runner sequentially.

An interrupted invocation is never replayed here. Finish-only reconciliation is
separate and requires both independently validated durable terminal receipts.
"""
    import os
    import stat
    from eidolon_cli.organization_prepared_validation_ledger import (
        _history, _reserve_prepared_validation_proposal,
    )
    from eidolon_cli.organization_prepared_validation_events import (
        append_prepared_runtime_event, retained_runtime_events,
    )
    from eidolon_cli.organization_project_effective_snapshot import verify_effective_snapshot_lineage
    from eidolon_cli.organization_project_workspace_node import run_prepared_workspace_step
    from eidolon_cli.organization_project_workspace_receipt import inspect_prepared_runtime_history

    authority, snapshot = capture_for_claim(store, claim, cancel)
    project_id = authority['contract']['projectId']
    with store._write() as conn:
        request = store._owned(conn, claim)
        if request is None or cancel.is_set():
            raise ValueError('Prepared reservation requires its current uncancelled lease')
        current = require_prepared_authority(store, conn, claim['objective_id'], project_id, execution=True)
        if current != authority:
            raise ValueError('Prepared authority changed during source capture')
        if conn.execute('SELECT 1 FROM prepared_validation_sets WHERE request_id=?', (request['id'],)).fetchone():
            raise ValueError('Prepared request already reserved its pair; inspect durable outcomes instead of replaying')
        from eidolon_cli.organization_prepared_validation_flow import prepared_stage_availability
        prepared_stage_availability(store, conn, request)
        verify_effective_snapshot_lineage(conn, snapshot, authority['project']['readRoot'], check_authorized=lambda: True)
        context = validation_context(authority, snapshot, claim['objective_id'])
        history = _history(conn, claim['objective_id'], project_id)
        envelope = new_validation_envelope(context, request['id'], len(history) + 1)
        _reserve_prepared_validation_proposal(conn, envelope, snapshot=snapshot.effective, context=context,
            owner_ceiling=store.settings.max_project_runs, lease_token=request['token'])
        retain_source_proof(conn, envelope, snapshot)
        from eidolon_cli.organization_prepared_validation_review_scope import retain_review_scope
        retain_review_scope(store, conn, envelope, snapshot, authority)

    # The directory is exclusively a backend runtime staging parent. Workloads
    # only see their descriptor-pinned children, never the organization ledger.
    parent = store.path.parent / 'prepared-runtime'
    parent.mkdir(mode=0o700, exist_ok=True)
    info = parent.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Prepared runtime staging parent must be a private owned directory')
    grant = authority['contract']
    dependencies = grant['dependencies']
    last_check = 0.0

    def check_authorized(*, force=False):
        nonlocal last_check
        if cancel.is_set():
            raise ValueError('Prepared execution was cancelled')
        now = time.monotonic()
        if not force and now - last_check < .05:
            return True
        with store._connect() as conn:
            if store._owned(conn, claim) is None:
                raise ValueError('Prepared execution lost its live lease')
            from eidolon_cli.organization_prepared_validation_setup import check_current_prepared_registration
            check_current_prepared_registration(conn, authority['registration'],
                organization_id=grant['organizationId'], owner_id=grant['ownerId'],
                policy_generation=authority['policyGeneration'],
                source_revision=authority['registration']['sourceRevision'])
            from eidolon_cli.organization_budget import budget_view
            if time.time() >= budget_view(conn, claim['objective_id'], store.settings)['deadlineTimestamp']:
                raise ValueError('Prepared objective deadline expired')
            if sorted(store._project_execution_authors(conn, claim['objective_id'], authority['project'])) != authority['contributors']:
                raise ValueError('Prepared source contributors changed')
            current_round = conn.execute('SELECT round FROM objective_control WHERE objective_id=?',
                                         (claim['objective_id'],)).fetchone()
            if current_round is None or current_round[0] != authority['round']:
                raise ValueError('Prepared execution belongs to a superseded round')
        last_check = now
        return True

    def record_event(value, expected_resource_record):
        from eidolon_cli.organization_project_workspace_resources import normalize_runtime_event
        value = normalize_runtime_event(value)
        if canonical(value['resource']) != expected_resource_record:
            raise ValueError('Runtime observation differs from its exact private step invocation')
        # Cleanup and the terminal observation may outlive cancellation/lease
        # loss; they can only settle the already reserved private invocation.
        cleanup = value['kind'] in {'cleanup_observed', 'receipt'}
        with store._write() as conn:
            reserved = conn.execute('SELECT request_id,lease_token FROM prepared_validation_sets WHERE id=?',
                                    (envelope['setId'],)).fetchone()
            if reserved is None or tuple(reserved) != (claim['id'], claim['token']):
                raise ValueError('Runtime observation does not own the reserved private invocation')
            if not cleanup:
                if store._owned(conn, claim) is None or cancel.is_set():
                    raise ValueError('Prepared forward event lost its live backend lease')
                current = require_prepared_authority(store, conn, claim['objective_id'], project_id, execution=True)
                if current != authority:
                    raise ValueError('Prepared authority changed before a forward runtime event')
            append_prepared_runtime_event(conn, value)
        return True  # Durable acknowledgement is after the complete commit.

    for step in contract.validation_execution_order(envelope):
        check_authorized(force=True)
        current_authority, fresh = capture_for_claim(store, claim, cancel)
        if current_authority != authority or not _same_snapshot(snapshot, fresh):
            raise ValueError('Prepared source or managed revisions changed before a mandatory step')
        resource = {'version': 1, 'contract': 'prepared-workspace-resource-v1',
            'setId': envelope['setId'], 'runId': step['runId'], 'nonce': uuid.uuid4().hex + uuid.uuid4().hex,
            'authoritySha256': envelope['identity']['authoritySha256']}
        result = run_prepared_workspace_step(snapshot.effective, dependencies['directory'],
            dependencies['manifest'], dependencies['toolchain'], grant['profile'], step['id'],
            allow_local_docker=grant['runtime']['allowLocalDocker'], runtime_image=grant['runtime']['image'],
            check_authorized=check_authorized, cancel=cancel, runtime_identity=resource,
            record_event=lambda value, identity=canonical(resource): record_event(value, identity), staging_parent=parent,
            expected_dependency_identity=tuple(dependencies[key] for key in ('device', 'inode')))
        with store._connect() as conn:
            events = retained_runtime_events(conn, step['runId'])
            if not events:
                raise ValueError('Prepared runtime did not establish a durable dispatch; inspect platform prerequisites')
            terminal = next((item for item in events if item['kind'] == 'receipt'), None)
            if terminal is None:
                raise ValueError('Prepared runtime outcome is unconfirmed; automatic replay is prohibited')
            context = {'source': snapshot.effective, 'profile': grant['profile'], 'step_id': step['id'],
                'expected_dependency_identity': tuple(dependencies[key] for key in ('device', 'inode')),
                'trusted_receipt_sha256': terminal['data']['receiptSha256']}
            inspected = inspect_prepared_runtime_history(events, resource_identity=resource, receipt_context=context)
            if digest(result) != terminal['data']['receiptSha256']:
                raise ValueError('Runtime return differs from its durable terminal observation')
            if inspected['state'] != 'finish_only':
                return {}  # Finish reports the retained failure; second step stays charged.
    check_authorized(force=True)
    with store._write() as conn:
        if store._owned(conn, claim) is None or cancel.is_set():
            raise ValueError('Prepared pair lost its live lease before aggregate retention')
        current = require_prepared_authority(store, conn, claim['objective_id'], project_id, execution=True)
        if current != authority:
            raise ValueError('Prepared authority changed before aggregate retention')
        from eidolon_cli.organization_prepared_validation_ledger import retain_prepared_aggregate_claim
        retain_prepared_aggregate_claim(conn, envelope['setId'])
    return {}

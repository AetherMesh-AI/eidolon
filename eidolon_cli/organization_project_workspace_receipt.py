"""Strict retained host receipt checks and finish-only crash reconciliation.

A matching hash is NOT authentication: trusted_receipt_sha256 must come from an
independently trusted host event store, never from model output or the candidate
itself. The workload's outcomes remain claims and independent whole-set review
is still required. These pure helpers neither authorize acceptance nor dispatch.
"""
from datetime import datetime
import math

from eidolon_cli.organization_project_snapshot_bundle import ProjectSnapshotBundle
from eidolon_cli.organization_project_validation import digest
from eidolon_cli.organization_project_validation_profile import normalize_validation_profile
from eidolon_cli import organization_project_workspace_resources as resources

_SUCCESS_FIELDS = {'runner', 'runnerVersion', 'status', 'startedAt', 'endedAt', 'durationSeconds',
    'exitCode', 'isolation', 'cleanupConfirmed', 'stagingCleanupConfirmed', 'sourceWritesPerformed',
    'limitations', 'resourceIdentity', 'dependencyRootIdentity', 'containerLabels', 'containerName',
    'snapshotSha256', 'profileSha256', 'bundleSha256', 'stepId', 'stepDefinitionSha256', 'recipe',
    'checkKind', 'stagingIdentity', 'runtime', 'containerId', 'containerConfigSha256', 'stderr',
    'stdoutBytes', 'stderrBytes', 'outcome', 'terminalState'}
_OUTCOME_FIELDS = {'kind', 'status', 'recipe', 'configuration', 'evidenceOrigin', 'toolExitCode',
    'testCount', 'skippedCount', 'failedCount', 'sourceCount', 'selectedFiles', 'reportedFiles',
    'toolEntry', 'modules'}


def validate_prepared_workspace_receipt(value, *, resource_identity, source, profile, step_id,
        expected_dependency_identity, expected_runtime, staging_identity, container_id,
        container_config_sha256, trusted_receipt_sha256):
    """Check a successful host receipt against separately retained exact context.

This is consistency validation only. Supplying a candidate's own hash does not
turn it into authenticated evidence; the caller owns trusted channel provenance.
"""
    from eidolon_cli.organization_project_workspace_node import LIMITATIONS, _outcome
    value = resources._bounded_json(value)
    resources._exact(value, _SUCCESS_FIELDS, 'Successful prepared receipt')
    receipt_hash = resources._hash(trusted_receipt_sha256)
    if digest(value) != receipt_hash:
        raise ValueError('Receipt differs from the independently retained host receipt hash')
    identity = resources.normalize_runtime_identity(resource_identity)
    if type(source) is not ProjectSnapshotBundle:
        raise ValueError('Receipt requires an independently retained source snapshot')
    source = ProjectSnapshotBundle(source.root_alias, source.root_identity, source.files, source.limits)
    normalized = normalize_validation_profile(profile)
    step = next((item for item in normalized['steps'] if item['id'] == step_id), None)
    if step is None:
        raise ValueError('Receipt step is outside the exact profile')
    dependency_identity = resources.directory_identity(expected_dependency_identity)
    runtime = resources._runtime(resources._bounded_json(expected_runtime))
    staging = resources.normalize_staging_identity(staging_identity, identity)
    expected = {'runner': 'eidolon.prepared-workspace', 'runnerVersion': 1, 'status': 'passed',
        'exitCode': 0, 'cleanupConfirmed': True, 'stagingCleanupConfirmed': True,
        'sourceWritesPerformed': False, 'limitations': LIMITATIONS,
        'resourceIdentity': identity, 'dependencyRootIdentity': dict(zip(('device', 'inode'), dependency_identity)),
        'containerName': resources.container_name(identity), 'containerLabels': resources.container_labels(identity),
        'snapshotSha256': source.snapshot_sha256, 'profileSha256': digest(normalized),
        'bundleSha256': normalized['bundleSha256'], 'stepId': step['id'], 'stepDefinitionSha256': digest(step),
        'recipe': step['recipe'], 'checkKind': step['checkKind'], 'runtime': runtime,
        'stagingIdentity': staging, 'containerId': resources._hash(container_id),
        'containerConfigSha256': resources._hash(container_config_sha256),
        'isolation': {'backend': 'linux-docker-pinned', 'established': True, 'network': 'none',
                      'sourceReadOnly': True, 'runtimeReadOnly': True, 'workloadUid': 65534},
        'terminalState': {'Running': False, 'ExitCode': 0, 'OOMKilled': False}}
    # Canonical comparison rejects bool/int equivalence in nested metadata too.
    if any(digest(value[key]) != digest(item) for key, item in expected.items()):
        raise ValueError('Receipt mixes run/source/profile/bundle/authority/runtime/cleanup identities')
    if normalized['limits']['cpus'] != 1 or normalized['limits']['pids'] != 64:
        raise ValueError('Receipt profile does not match the supported runtime limits')
    resources._exact(value['outcome'], _OUTCOME_FIELDS, 'Workload outcome claim')
    if not _outcome(value['outcome'], step, source, normalized['workspace']):
        raise ValueError('Receipt lacks exact compiler coverage or UI file selection')
    for key in ('stdoutBytes', 'stderrBytes'):
        if type(value[key]) is not int or not 0 <= value[key] <= normalized['limits']['logBytes'] + 1048576:
            raise ValueError('Receipt output counter is invalid')
    if type(value['stderr']) is not str or len(value['stderr'].encode('utf-8')) > 4 * (normalized['limits']['logBytes'] + 1048576):
        raise ValueError('Receipt diagnostic output exceeds its bound')
    if (type(value['durationSeconds']) not in (int, float) or not math.isfinite(value['durationSeconds'])
            or not 0 <= value['durationSeconds'] <= 86400):
        raise ValueError('Receipt duration must be finite and bounded')
    for key in ('startedAt', 'endedAt'):
        if type(value[key]) is not str or len(value[key]) > 64:
            raise ValueError('Receipt timestamps require bounded aware ISO dates')
        try:
            timestamp = datetime.fromisoformat(value[key])
        except ValueError as error:
            raise ValueError('Receipt timestamp is invalid') from error
        if timestamp.tzinfo is None:
            raise ValueError('Receipt timestamp must include its timezone')
    return {'receiptSha256': receipt_hash, 'resourceIdentity': identity,
            'hostReceiptConsistent': True, 'workloadEvidenceOrigin': 'workload-claim',
            'requiresIndependentReview': True, 'acceptanceAuthorized': False,
            'sourceDeliveryAuthorized': False, 'replayAllowed': False}


def inspect_prepared_runtime_history(events, *, resource_identity, receipt_context=None):
    """Inspect trusted retained events; crash dispatches stay blocked, never replay.

The list must be the complete ordered stream from the trusted host ledger. This
function cannot authenticate a model-supplied history. A terminal failed receipt
is never upgraded by subsequent cleanup. A fully successful receipt is eligible
only for finishing downstream bookkeeping, still with independent review.
"""
    identity = resources.normalize_runtime_identity(resource_identity)
    if type(events) is not list or not 1 <= len(events) <= 10000:
        raise ValueError('Reconciliation requires the complete bounded retained event history')
    records = [resources.normalize_runtime_event(item) for item in events]
    if any(item['resource'] != identity for item in records) or records[0]['kind'] != 'dispatch_planned':
        raise ValueError('Runtime history mixes identities or omits its precommitted dispatch')
    seen, staging, runtime, container, config_hash, receipt, cleanup = set(), None, None, None, None, None, None
    intent = records[0]['data']['stagingIntent']
    dependencies = {'staging_allocated': 'dispatch_planned', 'container_create_planned': 'staging_allocated',
                    'container_observed': 'container_create_planned', 'container_start_planned': 'container_observed'}
    for item in records:
        kind, data = item['kind'], item['data']
        if kind != 'cleanup_observed' and kind in seen:
            raise ValueError('Runtime history repeats an irreversible phase or terminal receipt')
        if kind in dependencies and (dependencies[kind] not in seen or receipt is not None or cleanup is not None):
            raise ValueError('Runtime history has an unordered or post-terminal dispatch phase')
        if kind == 'staging_allocated':
            staging = data['staging']
            if {key: staging[key] for key in intent} != intent:
                raise ValueError('Staging allocation differs from precommitted intent')
        if kind == 'cleanup_observed' and staging is None and data['staging'] is not None:
            # A failed allocation-event acknowledgement may still leave an exact
            # allocation observed during authorized cleanup. It never authorizes
            # a create/start phase or promotes the terminal failure to success.
            observed_staging = data['staging']
            if {key: observed_staging[key] for key in intent} != intent:
                raise ValueError('Cleanup allocation differs from precommitted staging intent')
            staging = observed_staging
        if 'staging' in data and data['staging'] != staging:
            raise ValueError('Runtime history swaps the staging allocation')
        if 'runtime' in data:
            if runtime is not None and runtime != data['runtime']:
                raise ValueError('Runtime history swaps its image/bootstrap identity')
            runtime = data['runtime']
        if 'containerId' in data and data['containerId'] is not None:
            if 'container_create_planned' not in seen or (container is not None and container != data['containerId']):
                raise ValueError('Runtime history swaps an observed container identity')
            container = data['containerId']
        if kind == 'container_start_planned':
            config_hash = data['containerConfigSha256']
        if kind == 'cleanup_observed':
            cleanup = data
        if kind == 'receipt':
            if cleanup is None:
                raise ValueError('Terminal receipt omits a preceding cleanup observation')
            terminal = data['receipt']
            if (type(terminal.get('cleanupConfirmed')) is not bool
                    or type(terminal.get('stagingCleanupConfirmed')) is not bool
                    or terminal['cleanupConfirmed'] != cleanup['containerCleanupConfirmed']
                    or terminal['stagingCleanupConfirmed'] != cleanup['stagingCleanupConfirmed']
                    or terminal.get('containerId') != container
                    or terminal.get('stagingIdentity') != staging):
                raise ValueError('Terminal receipt contradicts its preceding exact cleanup observation')
            receipt = data
        seen.add(kind)
    result = {'state': 'interrupted', 'replayAllowed': False, 'acceptanceAuthorized': False,
              'sourceDeliveryAuthorized': False, 'resourceIdentity': identity,
              'containerCreateAttempted': 'container_create_planned' in seen,
              'containerId': container, 'stagingIdentity': staging, 'runtime': runtime,
              'cleanup': cleanup, 'receiptSha256': receipt['receiptSha256'] if receipt else None}
    result['containerState'] = ('not_attempted' if 'container_create_planned' not in seen else
        'cleanup_confirmed' if cleanup and cleanup['containerCleanupConfirmed'] else 'cleanup_unconfirmed')
    result['stagingState'] = ('allocation_unconfirmed' if staging is None else
        'cleanup_confirmed' if cleanup and cleanup['stagingCleanupConfirmed'] else 'cleanup_unconfirmed')
    if receipt is None:
        return result
    result['state'] = 'terminal_unverified'
    if receipt['receipt'].get('status') != 'passed':
        result['state'] = 'terminal_failed'
        return result
    if receipt_context is None:
        return result
    if (not {'container_observed', 'container_start_planned'} <= seen
            or cleanup['containerCleanupConfirmed'] is not True or cleanup['stagingCleanupConfirmed'] is not True):
        raise ValueError('Successful reconciliation lacks start/cleanup observations')
    context = dict(receipt_context)
    expected = {'resource_identity': identity, 'staging_identity': staging,
                'expected_runtime': runtime, 'container_id': container, 'container_config_sha256': config_hash}
    for key, item in expected.items():
        if key in context and context[key] != item:
            raise ValueError('Retained receipt context differs from the exact event stream')
        context[key] = item
    validated = validate_prepared_workspace_receipt(receipt['receipt'], **context)
    dispatch = records[0]['data']
    if any(receipt['receipt'][key] != dispatch[key] for key in
           ('snapshotSha256', 'profileSha256', 'bundleSha256', 'stepId', 'stepDefinitionSha256', 'recipe', 'checkKind', 'dependencyRootIdentity')):
        raise ValueError('Terminal receipt differs from precommitted source/profile/dependency/step scope')
    return {**result, **validated, 'state': 'finish_only'}

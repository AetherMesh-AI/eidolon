"""Adversarial synthetic receipt checks, not authenticated native evidence."""
from copy import deepcopy

import pytest

from eidolon_cli import organization_project_workspace_receipt as receipts
from eidolon_cli import organization_project_workspace_resources as resources
from eidolon_cli.organization_project_validation import digest
from eidolon_cli.organization_project_validation_profile import normalize_validation_profile
from eidolon_cli.organization_project_workspace_node import LIMITATIONS
from tests.eidolon_cli.test_organization_project_workspace_node import frame, profile, source
from tests.eidolon_cli.test_organization_project_workspace_resources import identity, image_identity


def fixture(recipe='workspace_typescript'):
    snapshot = source()
    value = profile()
    value['limits']['cpus'] = 1
    value = normalize_validation_profile(value)
    step = next(item for item in value['steps'] if item['recipe'] == recipe)
    _, outcome = frame(recipe)
    resource = identity()
    staging = {'path': '/private/' + resources.container_name(resource),
               'parentDevice': 1, 'parentInode': 2, 'device': 1, 'inode': 3}
    runtime = image_identity()
    receipt = {'runner': 'eidolon.prepared-workspace', 'runnerVersion': 1, 'status': 'passed',
        'startedAt': '2026-10-16T04:00:00+00:00', 'endedAt': '2026-10-16T04:00:01+00:00',
        'durationSeconds': 1.0, 'exitCode': 0, 'cleanupConfirmed': True, 'stagingCleanupConfirmed': True,
        'sourceWritesPerformed': False, 'limitations': LIMITATIONS, 'resourceIdentity': resource,
        'dependencyRootIdentity': {'device': 1, 'inode': 7}, 'containerLabels': resources.container_labels(resource),
        'containerName': resources.container_name(resource), 'snapshotSha256': snapshot.snapshot_sha256,
        'profileSha256': digest(value), 'bundleSha256': value['bundleSha256'], 'stepId': step['id'],
        'stepDefinitionSha256': digest(step), 'recipe': recipe, 'checkKind': step['checkKind'],
        'stagingIdentity': staging, 'runtime': runtime, 'containerId': 'e' * 64,
        'containerConfigSha256': 'f' * 64, 'stderr': '', 'stdoutBytes': 100, 'stderrBytes': 0,
        'outcome': outcome, 'terminalState': {'Running': False, 'ExitCode': 0, 'OOMKilled': False},
        'isolation': {'backend': 'linux-docker-pinned', 'established': True, 'network': 'none',
                      'sourceReadOnly': True, 'runtimeReadOnly': True, 'workloadUid': 65534}}
    context = {'resource_identity': deepcopy(resource), 'source': snapshot, 'profile': value, 'step_id': step['id'],
        'expected_dependency_identity': (1, 7), 'expected_runtime': deepcopy(runtime),
        'staging_identity': deepcopy(staging), 'container_id': 'e' * 64, 'container_config_sha256': 'f' * 64,
        'trusted_receipt_sha256': digest(receipt)}
    return receipt, context


def events_for(receipt):
    identity = receipt['resourceIdentity']
    staging = receipt['stagingIdentity']
    pairs = [
        ('dispatch_planned', {'containerName': receipt['containerName'], 'labels': receipt['containerLabels'],
            'image': receipt['runtime']['image'], 'stagingIntent': {key: staging[key] for key in
                ('path', 'parentDevice', 'parentInode')},
            **{key: receipt[key] for key in ('dependencyRootIdentity', 'snapshotSha256', 'profileSha256',
                'bundleSha256', 'stepId', 'stepDefinitionSha256', 'recipe', 'checkKind')}}),
        ('staging_allocated', {'staging': staging}),
        ('container_create_planned', {'staging': staging, 'runtime': receipt['runtime']}),
        ('container_observed', {'containerId': receipt['containerId'], 'runtime': receipt['runtime'], 'staging': staging}),
        ('container_start_planned', {'containerId': receipt['containerId'],
                                     'containerConfigSha256': receipt['containerConfigSha256']}),
        ('cleanup_observed', {'containerId': receipt['containerId'], 'containerCleanupConfirmed': True,
                              'stagingCleanupConfirmed': True, 'staging': staging}),
        ('receipt', {'receipt': receipt, 'receiptSha256': digest(receipt)}),
    ]
    return deepcopy([{'version': 1, 'contract': resources.EVENT_CONTRACT, 'kind': kind,
                      'resource': identity, 'data': data} for kind, data in pairs])


@pytest.mark.parametrize('recipe', ['workspace_typescript', 'workspace_vitest_ui'])
def test_strict_matching_receipt_is_only_consistent_and_never_acceptance_authority(recipe):
    candidate, context = fixture(recipe)
    result = receipts.validate_prepared_workspace_receipt(candidate, **context)
    assert result['hostReceiptConsistent'] is True
    assert result['workloadEvidenceOrigin'] == 'workload-claim'
    assert result['requiresIndependentReview'] is True
    assert result['acceptanceAuthorized'] is False
    assert result['replayAllowed'] is False


@pytest.mark.parametrize('field,value', [
    ('runnerVersion', True), ('exitCode', True), ('sourceWritesPerformed', True),
    ('cleanupConfirmed', False), ('stagingCleanupConfirmed', False), ('status', 'failed'),
    ('snapshotSha256', '0' * 64), ('profileSha256', '0' * 64), ('bundleSha256', '0' * 64),
    ('stepId', 'ui-tests'), ('stepDefinitionSha256', '0' * 64), ('containerId', '0' * 64),
    ('containerConfigSha256', '0' * 64), ('containerName', 'eidolon-prepared-' + '0' * 64),
    ('stderrBytes', True), ('durationSeconds', float('nan')), ('startedAt', 'no timestamp'),
    ('unrecognized', 'extra payload'),
])
def test_candidate_cannot_rehash_itself_into_exact_retained_evidence(field, value):
    candidate, context = fixture()
    candidate[field] = value
    # First an arbitrary modified candidate fails against the trusted original.
    with pytest.raises(ValueError):
        receipts.validate_prepared_workspace_receipt(candidate, **context)
    # Even a trusted sink accidentally retaining a malformed claim cannot bypass
    # structural/scope checks. This is synthetic, never native authentication.
    if field != 'durationSeconds':
        context['trusted_receipt_sha256'] = digest(candidate)
        with pytest.raises(ValueError):
            receipts.validate_prepared_workspace_receipt(candidate, **context)


@pytest.mark.parametrize('field', ['setId', 'runId', 'nonce', 'authoritySha256'])
def test_receipt_cannot_mix_resource_ownership(field):
    candidate, context = fixture()
    candidate['resourceIdentity'][field] = 'changed' if field.endswith('Id') else '0' * 64
    context['trusted_receipt_sha256'] = digest(candidate)
    with pytest.raises(ValueError, match='identities'):
        receipts.validate_prepared_workspace_receipt(candidate, **context)


@pytest.mark.parametrize('field', ['image', 'imageId', 'seccompSha256', 'bootstrapSha256'])
def test_receipt_cannot_mix_runtime_artifacts(field):
    candidate, context = fixture()
    candidate['runtime'][field] = '0' * 64
    context['trusted_receipt_sha256'] = digest(candidate)
    with pytest.raises(ValueError):
        receipts.validate_prepared_workspace_receipt(candidate, **context)


@pytest.mark.parametrize('target,key,value', [
    ('terminalState', 'ExitCode', False), ('terminalState', 'Running', 0),
    ('isolation', 'established', 1), ('isolation', 'network', 'host'),
    ('isolation', 'sourceReadOnly', False), ('isolation', 'runtimeReadOnly', False),
    ('dependencyRootIdentity', 'inode', 9), ('stagingIdentity', 'inode', 9),
    ('containerLabels', 'ai.eidolon.prepared.run', 'other-run'),
])
def test_nested_security_identity_types_and_cleanup_are_exact(target, key, value):
    candidate, context = fixture()
    candidate[target][key] = value
    context['trusted_receipt_sha256'] = digest(candidate)
    with pytest.raises(ValueError):
        receipts.validate_prepared_workspace_receipt(candidate, **context)


@pytest.mark.parametrize('recipe', ['workspace_typescript', 'workspace_vitest_ui'])
@pytest.mark.parametrize('mutation', ['selected', 'reported', 'unknown-field', 'trusted-origin'])
def test_successful_receipt_requires_exact_complete_workload_coverage(recipe, mutation):
    candidate, context = fixture(recipe)
    if mutation in ('selected', 'reported'):
        candidate['outcome'][mutation + 'Files'] = []
    elif mutation == 'unknown-field':
        candidate['outcome']['authenticated'] = True
    else:
        candidate['outcome']['evidenceOrigin'] = 'trusted-host'
    context['trusted_receipt_sha256'] = digest(candidate)
    with pytest.raises(ValueError):
        receipts.validate_prepared_workspace_receipt(candidate, **context)


def test_missing_cleanup_or_self_supplied_hash_fields_are_rejected():
    candidate, context = fixture()
    del candidate['stagingCleanupConfirmed']
    candidate['receiptSha256'] = digest(candidate)
    context['trusted_receipt_sha256'] = digest(candidate)
    with pytest.raises(ValueError):
        receipts.validate_prepared_workspace_receipt(candidate, **context)


@pytest.mark.parametrize('prefix', [1, 2, 3, 4, 5, 6])
def test_crash_at_every_dispatch_phase_blocks_replay_and_retains_exact_known_target(prefix):
    candidate, _ = fixture()
    records = events_for(candidate)[:prefix]
    result = receipts.inspect_prepared_runtime_history(records, resource_identity=identity())
    assert result['state'] == 'interrupted'
    assert result['replayAllowed'] is False
    assert result['containerId'] == ('e' * 64 if prefix >= 4 else None)
    assert result['stagingIdentity'] == (candidate['stagingIdentity'] if prefix >= 2 else None)


def test_successful_retained_receipt_is_finish_only_after_strict_context_validation():
    candidate, context = fixture()
    records = events_for(candidate)
    assert receipts.inspect_prepared_runtime_history(records, resource_identity=identity())['state'] == 'terminal_unverified'
    result = receipts.inspect_prepared_runtime_history(records, resource_identity=identity(), receipt_context=context)
    assert result['state'] == 'finish_only'
    assert result['replayAllowed'] is False
    assert result['acceptanceAuthorized'] is False
    assert result['receiptSha256'] == digest(candidate)


@pytest.mark.parametrize('mutation', ['reorder', 'duplicate', 'missing-allocation', 'swapped-allocation',
    'swapped-container', 'mixed-authority', 'changed-receipt-hash', 'missing-cleanup', 'latest-cleanup-unknown'])
def test_incomplete_forged_or_unordered_history_cannot_finish(mutation):
    candidate, context = fixture()
    records = events_for(candidate)
    if mutation == 'reorder':
        records[3], records[4] = records[4], records[3]
    elif mutation == 'duplicate':
        records.insert(3, deepcopy(records[2]))
    elif mutation == 'missing-allocation':
        records.pop(1)
    elif mutation == 'swapped-allocation':
        records[2]['data']['staging']['inode'] = 88
    elif mutation == 'swapped-container':
        records[4]['data']['containerId'] = '0' * 64
    elif mutation == 'mixed-authority':
        records[3]['resource']['authoritySha256'] = '0' * 64
    elif mutation == 'changed-receipt-hash':
        records[-1]['data']['receiptSha256'] = '0' * 64
    elif mutation == 'missing-cleanup':
        records.pop(-2)
    else:
        later = deepcopy(records[-2])
        later['data']['stagingCleanupConfirmed'] = False
        records.append(later)
    with pytest.raises(ValueError):
        receipts.inspect_prepared_runtime_history(records, resource_identity=identity(), receipt_context=context)


def test_failed_terminal_never_upgraded_by_later_cleanup():
    candidate, context = fixture()
    candidate['status'] = 'cleanup_failed'
    candidate['cleanupConfirmed'] = False
    context['trusted_receipt_sha256'] = digest(candidate)
    records = events_for(candidate)
    records[-2]['data']['containerCleanupConfirmed'] = False
    records.append(deepcopy(records[-2]))
    records[-1]['data']['containerCleanupConfirmed'] = True
    result = receipts.inspect_prepared_runtime_history(records, resource_identity=identity(), receipt_context=context)
    assert result['state'] == 'terminal_failed'
    assert result['replayAllowed'] is False


def test_missing_resource_observation_after_create_remains_unknown_without_terminal_receipt():
    candidate, _ = fixture()
    records = events_for(candidate)[:3]
    state = receipts.inspect_prepared_runtime_history(records, resource_identity=identity())
    assert state['state'] == 'interrupted' and state['containerId'] is None
    assert state['containerState'] == 'cleanup_unconfirmed' and state['replayAllowed'] is False
    assert state['stagingIdentity'] == candidate['stagingIdentity']
    state = receipts.inspect_prepared_runtime_history(records[:1], resource_identity=identity())
    assert state['containerState'] == 'not_attempted'
    assert state['stagingState'] == 'allocation_unconfirmed'


def test_cleanup_can_retain_exact_allocation_after_failed_allocation_ack_without_authorizing_create():
    candidate, _ = fixture()
    records = events_for(candidate)
    cleanup = deepcopy(records[-2])
    cleanup['data'].update(containerId=None, containerCleanupConfirmed=False)
    state = receipts.inspect_prepared_runtime_history([records[0], cleanup], resource_identity=identity())
    assert state['state'] == 'interrupted' and state['replayAllowed'] is False
    assert state['containerState'] == 'not_attempted' and state['stagingState'] == 'cleanup_confirmed'
    assert state['stagingIdentity'] == candidate['stagingIdentity']
    with pytest.raises(ValueError):
        receipts.inspect_prepared_runtime_history([records[0], cleanup, records[2]], resource_identity=identity())


def test_later_cleanup_cannot_repair_a_contradictory_successful_terminal_receipt():
    candidate, context = fixture()
    records = events_for(candidate)
    records[-2]['data']['containerCleanupConfirmed'] = False
    later = deepcopy(records[-2])
    later['data']['containerCleanupConfirmed'] = True
    records.append(later)
    with pytest.raises(ValueError, match='contradicts'):
        receipts.inspect_prepared_runtime_history(records, resource_identity=identity(), receipt_context=context)

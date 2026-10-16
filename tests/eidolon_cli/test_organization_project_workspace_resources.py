"""Resource/event boundary tests; synthesized records are not native proof."""
from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_project_workspace_node as runtime
from eidolon_cli import organization_project_workspace_resources as resources
from eidolon_cli.organization_project_node import IMAGE, PROFILE_SHA256
from tests.eidolon_cli.test_organization_project_workspace_node import profile, source


def identity():
    return {'version': 1, 'contract': resources.RESOURCE_CONTRACT, 'setId': 'set-one',
            'runId': 'run-types', 'nonce': 'a' * 64, 'authoritySha256': 'b' * 64}


def image_identity():
    return {'image': IMAGE, 'imageId': 'sha256:' + 'c' * 64,
            'seccompSha256': PROFILE_SHA256, 'bootstrapSha256': 'd' * 64}


def container_record():
    return {'Id': 'e' * 64, 'Name': '/' + resources.container_name(identity()),
            'Image': image_identity()['imageId'], 'Config': {'Labels': resources.container_labels(identity())}}


@pytest.mark.parametrize('change', [{'nonce': 'a' * 63}, {'version': True}, {'runId': 'set-one'},
    {'authoritySha256': 'B' * 64}, {'runId': '../run'}, {'unknown': True}])
def test_resource_identity_rejects_ambiguous_or_aliased_fields(change):
    with pytest.raises(ValueError):
        resources.normalize_runtime_identity(identity() | change)


def test_resource_names_and_labels_bind_set_run_nonce_and_authority():
    value = identity()
    normalized = resources.normalize_runtime_identity(value)
    value['runId'] = 'mutated'
    assert normalized['runId'] == 'run-types'
    assert resources.container_name(normalized) == 'eidolon-prepared-' + normalized['nonce']
    record = container_record()
    assert resources.verify_container_identity(record, normalized, image_id=image_identity()['imageId'],
                                               container_id='e' * 64) == 'e' * 64


@pytest.mark.parametrize('field', ['Id', 'Name', 'Image', 'set', 'run', 'nonce', 'authority', 'extra-label'])
def test_swapped_resources_or_any_changed_label_never_get_removed(monkeypatch, field):
    record = container_record()
    if field in {'Id', 'Name', 'Image'}:
        record[field] = 'f' * 64
    elif field == 'extra-label':
        record['Config']['Labels']['unknown'] = 'extra'
    else:
        record['Config']['Labels']['ai.eidolon.prepared.' + field] = 'swapped'
    monkeypatch.setattr(runtime, '_json_command', lambda *args: [record])
    monkeypatch.setattr(runtime, '_docker', lambda *args: pytest.fail('unowned resource must not be removed'))
    assert runtime._cleanup_container('e' * 64, resources.container_name(identity()),
                                     image_identity()['imageId'], 'config', identity()) == (None, False)


def test_cleanup_uses_exact_id_with_no_execution_authority_poll(monkeypatch):
    record = container_record()
    monkeypatch.setattr(runtime, '_json_command', lambda *args: [record])
    calls = []
    def remove(arguments, config, budget):
        assert budget.cancel is None
        calls.append(arguments)
        return 0, {}, {}, None
    monkeypatch.setattr(runtime, '_docker', remove)
    assert runtime._cleanup_container(resources.container_name(identity()), resources.container_name(identity()),
                image_identity()['imageId'], 'config', identity()) == ('e' * 64, True)
    assert calls == [['rm', '--force', 'e' * 64]]


@pytest.mark.linux_only
def test_staging_allocation_has_exact_precommitted_parent_and_cleanup_identity(tmp_path):
    value = identity()
    intent = resources.staging_intent(tmp_path, value)
    assert not Path(intent['path']).exists()
    allocation = resources.allocate_staging(intent, value)
    assert resources.verify_staging_identity(allocation, value) == allocation
    path = Path(allocation['path'])
    (path / 'sealed').mkdir()
    (path / 'sealed' / 'file').write_text('retained')
    (path / 'sealed').chmod(0o555)
    assert resources.cleanup_owned_staging(allocation, value)
    assert not path.exists()


@pytest.mark.linux_only
@pytest.mark.parametrize('replacement', ['directory', 'symlink', 'parent'])
def test_swapped_staging_is_retained_and_never_deleted(tmp_path, replacement):
    parent = tmp_path / 'private'
    parent.mkdir(mode=0o700)
    value = identity()
    allocation = resources.allocate_staging(resources.staging_intent(parent, value), value)
    path = Path(allocation['path'])
    if replacement == 'parent':
        parent.rename(tmp_path / 'old-parent')
        parent.mkdir(mode=0o700)
        path.mkdir(mode=0o700)
    else:
        path.rename(parent / 'old-root')
        if replacement == 'directory':
            path.mkdir(mode=0o700)
        else:
            target = tmp_path / 'target'
            target.mkdir()
            path.symlink_to(target, target_is_directory=True)
    sentinel = path / 'sentinel'
    sentinel.write_text('do not delete')
    with pytest.raises((ValueError, OSError)):
        resources.cleanup_owned_staging(allocation, value)
    assert sentinel.read_text() == 'do not delete'


@pytest.mark.linux_only
def test_staging_parent_swap_before_allocation_and_existing_path_fail_closed(tmp_path):
    parent = tmp_path / 'private'
    parent.mkdir(mode=0o700)
    intent = resources.staging_intent(parent, identity())
    parent.rename(tmp_path / 'old-parent')
    parent.mkdir(mode=0o700)
    with pytest.raises(ValueError, match='parent identity'):
        resources.allocate_staging(intent, identity())
    fresh = resources.staging_intent(parent, identity())
    Path(fresh['path']).mkdir(mode=0o700)
    (Path(fresh['path']) / 'sentinel').write_text('keep')
    with pytest.raises(FileExistsError):
        resources.allocate_staging(fresh, identity())
    assert (Path(fresh['path']) / 'sentinel').read_text() == 'keep'


@pytest.mark.parametrize('ack', [False, None, 1, 'true'])
def test_event_sink_requires_exact_durable_ack(ack):
    data = {'containerId': None, 'containerCleanupConfirmed': False,
            'stagingCleanupConfirmed': False, 'staging': None}
    with pytest.raises(ValueError, match='durably committed'):
        resources.commit_runtime_event(lambda event: ack, identity(), 'cleanup_observed', data)


def test_event_sink_failure_and_mutation_cannot_change_retained_runner_state():
    data = {'containerId': None, 'containerCleanupConfirmed': False,
            'stagingCleanupConfirmed': False, 'staging': None}
    expected = deepcopy(data)
    def mutate(event):
        event['resource']['runId'] = 'different'
        event['data']['containerCleanupConfirmed'] = True
        return True
    resources.commit_runtime_event(mutate, identity(), 'cleanup_observed', data)
    assert data == expected
    def fail(event):
        raise OSError('durability unavailable')
    with pytest.raises(ValueError, match='durably committed'):
        resources.commit_runtime_event(fail, identity(), 'cleanup_observed', data)


def boundary_only_runtime(monkeypatch):
    """Permit preflight only on native Linux; never create/start a container."""
    checked = {'manifest': {'files': [], 'workspaceLinks': [], 'packageManifests': [],
                            'selectedWorkspaces': ['apps/desktop'], 'toolchain': {}},
               'bundleSha256': 'a' * 64, 'inventorySha256': 'f' * 64, 'lockfileSha256': '0' * 64}
    monkeypatch.setattr(runtime, 'normalize_prepared_dependency_manifest', lambda *a, **kw: checked)
    monkeypatch.setattr(runtime, '_tool_entry', lambda *a: 'node_modules/typescript/lib/typescript.js')
    monkeypatch.setattr(runtime, '_stage_dependencies', lambda *a: None)
    real_path = Path
    def path(value):
        if str(value) == '/usr/bin/docker':
            return SimpleNamespace(is_file=lambda: True, stat=lambda: SimpleNamespace(st_uid=0, st_mode=0o755))
        if str(value) == '/var/run/docker.sock':
            return SimpleNamespace(is_socket=lambda: True)
        return real_path(value)
    monkeypatch.setattr(runtime, 'Path', path)
    def inspect(arguments, *args):
        if arguments[0] == 'info':
            return {'OSType': 'linux', 'CgroupVersion': '2', 'Architecture': 'x86_64'}
        if arguments[:2] == ['image', 'inspect']:
            return [{'Os': 'linux', 'Architecture': 'amd64', 'RepoDigests': [IMAGE], 'Config': {},
                     'Id': image_identity()['imageId']}]
        pytest.fail('Only pre-dispatch metadata should be inspected')
    monkeypatch.setattr(runtime, '_json_command', inspect)
    monkeypatch.setattr(runtime, '_docker', lambda *args: pytest.fail('No container operation is authorized'))
    value = profile()
    value['limits']['cpus'] = 1
    return value


@pytest.mark.linux_only
@pytest.mark.parametrize('rejected', ['dispatch_planned', 'staging_allocated', 'container_create_planned'])
def test_durable_ack_failure_blocks_all_later_creation_and_retains_observations(monkeypatch, tmp_path, rejected):
    value = boundary_only_runtime(monkeypatch)
    events = []
    def record(event):
        events.append(event)
        return event['kind'] != rejected
    result = runtime.run_prepared_workspace_step(source(), '/unread', {'packageManifests': []}, {},
        value, 'renderer-types', allow_local_docker=True, check_authorized=lambda: True,
        runtime_identity=identity(), record_event=record, staging_parent=tmp_path,
        expected_dependency_identity=(1, 2))
    assert result['status'] == 'blocked'
    assert result['cleanupConfirmed'] is False
    assert 'containerId' not in result
    assert result['stagingCleanupConfirmed'] is (rejected != 'dispatch_planned')
    if rejected != 'dispatch_planned':
        assert [event['kind'] for event in events][-2:] == ['cleanup_observed', 'receipt']
        assert events[-1]['data']['receipt']['status'] == 'blocked'
        from eidolon_cli.organization_project_workspace_receipt import inspect_prepared_runtime_history
        retained = [event for event in events if event['kind'] != rejected]
        state = inspect_prepared_runtime_history(retained, resource_identity=identity())
        assert state['state'] == 'terminal_failed'
        assert state['containerState'] == 'not_attempted' and state['stagingState'] == 'cleanup_confirmed'
    assert not Path(resources.staging_intent(tmp_path, identity())['path']).exists()


@pytest.mark.linux_only
def test_staging_swap_in_event_sink_blocks_create_and_refuses_replacement_cleanup(monkeypatch, tmp_path):
    value = boundary_only_runtime(monkeypatch)
    def record(event):
        if event['kind'] == 'container_create_planned':
            path = Path(event['data']['staging']['path'])
            path.rename(tmp_path / 'old-allocation')
            path.mkdir(mode=0o700)
            (path / 'sentinel').write_text('replacement')
        return True
    result = runtime.run_prepared_workspace_step(source(), '/unread', {'packageManifests': []}, {},
        value, 'renderer-types', allow_local_docker=True, check_authorized=lambda: True,
        runtime_identity=identity(), record_event=record, staging_parent=tmp_path,
        expected_dependency_identity=(1, 2))
    assert result['status'] == 'staging_cleanup_failed'
    assert result['stagingCleanupConfirmed'] is False
    assert (Path(result['retainedStagingDirectory']) / 'sentinel').read_text() == 'replacement'


def test_durable_mode_does_not_replace_actual_owner_docker_opt_in(monkeypatch, tmp_path):
    monkeypatch.setattr(resources, 'staging_intent', lambda *args: pytest.fail('must stop before staging'))
    result = runtime.run_prepared_workspace_step(None, '/unread', {}, {}, {}, 'missing',
        check_authorized=lambda: True, runtime_identity=identity(), record_event=lambda e: True,
        staging_parent=tmp_path, expected_dependency_identity=(1, 2))
    assert result['status'] == 'blocked'
    assert 'Owner must explicitly enable' in result['reason']


@pytest.mark.linux_only
def test_reconcile_unattempted_container_only_removes_exact_owned_staging(monkeypatch, tmp_path):
    value = identity()
    allocation = resources.allocate_staging(resources.staging_intent(tmp_path, value), value)
    (Path(allocation['path']) / 'sentinel').write_text('owned')
    monkeypatch.setattr(runtime, '_docker', lambda *a: pytest.fail('unattempted cleanup must not access Docker'))
    events = []
    result = runtime.cleanup_prepared_workspace_resource(value, staging_identity=allocation,
        expected_runtime=None, container_create_attempted=False, record_event=lambda event: events.append(event) or True)
    assert result['state'] == 'cleanup_confirmed'
    assert result['containerCleanupConfirmed'] is False and result['stagingCleanupConfirmed'] is True
    assert result['replayAllowed'] is False
    assert events[0]['kind'] == 'cleanup_observed'
    assert not Path(allocation['path']).exists()


@pytest.mark.linux_only
def test_reconcile_rejects_swapped_staging_before_container_access(monkeypatch, tmp_path):
    value = identity()
    allocation = resources.allocate_staging(resources.staging_intent(tmp_path, value), value)
    path = Path(allocation['path'])
    path.rename(tmp_path / 'original')
    path.mkdir(mode=0o700)
    (path / 'sentinel').write_text('unowned')
    monkeypatch.setattr(runtime, '_docker', lambda *a: pytest.fail('swapped staging must not access Docker'))
    events = []
    result = runtime.cleanup_prepared_workspace_resource(value, staging_identity=allocation,
        expected_runtime=image_identity(), container_id='e' * 64, allow_local_docker=True,
        record_event=lambda event: events.append(event) or True)
    assert result['state'] == 'cleanup_unconfirmed'
    assert not result['containerCleanupConfirmed'] and not result['stagingCleanupConfirmed']
    assert (path / 'sentinel').read_text() == 'unowned'
    assert events[0]['data']['staging'] == allocation


@pytest.mark.parametrize('missing', ['runtime_identity', 'record_event', 'staging_parent', 'expected_dependency_identity'])
def test_partial_durable_interface_never_silently_falls_back_to_legacy(monkeypatch, tmp_path, missing):
    monkeypatch.setattr(runtime, '_docker', lambda *args: pytest.fail('incomplete durable identity must not execute'))
    kwargs = {'runtime_identity': identity(), 'record_event': lambda event: True,
              'staging_parent': tmp_path, 'expected_dependency_identity': (1, 2)}
    kwargs[missing] = None
    result = runtime.run_prepared_workspace_step(source(), '/unread', {}, {}, profile(), 'renderer-types',
        allow_local_docker=True, check_authorized=lambda: True, **kwargs)
    assert result['status'] == 'blocked' and 'containerId' not in result


@pytest.mark.linux_only
def test_revocation_after_durable_allocation_stops_forward_work_but_cleans_owned_staging(monkeypatch, tmp_path):
    value = boundary_only_runtime(monkeypatch)
    authorized, events = [True], []
    def record(event):
        events.append(event)
        if event['kind'] == 'staging_allocated':
            authorized[0] = False
        return True
    result = runtime.run_prepared_workspace_step(source(), '/unread', {'packageManifests': []}, {},
        value, 'renderer-types', allow_local_docker=True, check_authorized=lambda: authorized[0],
        runtime_identity=identity(), record_event=record, staging_parent=tmp_path,
        expected_dependency_identity=(1, 2))
    assert result['status'] == 'blocked' and result['stagingCleanupConfirmed'] is True
    assert [event['kind'] for event in events] == ['dispatch_planned', 'staging_allocated', 'cleanup_observed', 'receipt']
    assert not Path(events[1]['data']['staging']['path']).exists()

"""Experimental prepared workspace execution, not production grant dispatch.

Every call needs explicit owner runtime opt-in and a current authority callback.
No installation, image pulling, package-script dispatch or host fallback exists.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import uuid

from agent.redact import redact_sensitive_text

from eidolon_cli.organization_project_dependency_bundle import (
    copy_verified_dependency_bundle, normalize_prepared_dependency_manifest,
)
from eidolon_cli.organization_project_node import (
    ASSETS as NODE_ASSETS, IMAGE, PROFILE_SHA256, _create_arguments,
    _docker, _json_command, _verify_container,
)
from eidolon_cli.organization_project_runner import _Budget, _Cancelled, _Deadline, _Unsupported
from eidolon_cli.organization_project_snapshot_bundle import ProjectSnapshotBundle
from eidolon_cli.organization_project_validation import digest
from eidolon_cli.organization_project_validation_profile import _path, normalize_validation_profile
from eidolon_cli import organization_project_workspace_resources as resources

ASSETS = Path(__file__).with_name('project_workspace_runtime')
LIMITATIONS = ('Experimental exact prepared workspace execution. No installation or source publication. '
              'Project configuration, dependencies and test reports are untrusted workload code/data. '
              'Successful execution does not prove meaningful or honest assertions; independent review is required. '
              'Only the selected renderer typecheck and exact UI test files are covered.')


def _authority(check_authorized):
    if not callable(check_authorized) or check_authorized() is not True:
        raise ValueError('Current explicit workspace execution authority is required')


class _AuthorityCancellation:
    """Polling adapter: revocation or failed authority reads stop the workload."""
    def __init__(self, cancel, check_authorized):
        self.cancel = cancel
        self.check_authorized = check_authorized
        self.revoked = False

    def is_set(self):
        if self.revoked or (self.cancel is not None and self.cancel.is_set()):
            return True
        try:
            self.revoked = self.check_authorized() is not True
        except Exception:
            self.revoked = True
        return self.revoked


def _stage_dependencies(directory, destination, checked, lockfile, manifests, budget, expected_identity=None):
    staged = copy_verified_dependency_bundle(directory, destination, checked['manifest'],
        lockfile_bytes=lockfile, package_manifests=manifests,
        expected_toolchain=checked['manifest']['toolchain'],
        cancel=budget.cancel, deadline=budget.deadline, expected_source_identity=expected_identity)
    if any(staged.get(key) != checked[key]
           for key in ('bundleSha256', 'inventorySha256', 'lockfileSha256')):
        raise ValueError('Staged dependency identity differs from the exact bound profile')


def _safe_log(data):
    return redact_sensitive_text(data.decode('utf-8', 'replace'), force=True,
                                 file_read=True, redact_url_credentials=True)


def _tool_entry(step, checked):
    tool, entry = (('typescript', 'node_modules/typescript/lib/typescript.js')
                   if step['recipe'] == 'workspace_typescript'
                   else ('vitest', 'node_modules/vitest/dist/node.js'))
    entries = {item['path'] for item in checked['manifest']['files']}
    if entry not in entries or tool not in checked['manifest']['toolchain']['tools']:
        raise ValueError('Exact prepared tool entry point or declared toolchain is absent')
    return entry


def _cleanup_container(candidate, name, image_id, docker_config, identity=None):
    """Only remove the exact labeled container; malformed inspection is uncertain."""
    try:
        cleanup = _Budget(None, 5, time.monotonic())
        records = _json_command(['inspect', candidate], docker_config, cleanup)
        if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
            return None, False
        record = records[0]
        identifier = record.get('Id')
        config = record.get('Config')
        labels = config.get('Labels') if isinstance(config, dict) else None
        if (not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{64}', identifier)
                or record.get('Name') != '/' + name or record.get('Image') != image_id
                or not isinstance(labels, dict) or labels.get('ai.eidolon.execution') != name):
            return None, False
        if re.fullmatch(r'[0-9a-f]{64}', candidate) and identifier != candidate:
            return None, False
        if identity is not None:
            resources.verify_container_identity(record, identity, image_id=image_id,
                container_id=candidate if re.fullmatch(r'[0-9a-f]{64}', candidate) else None)
        code, _, _, forced = _docker(['rm', '--force', identifier], docker_config, cleanup)
        return identifier, code == 0 and forced is None
    except (ValueError, TypeError, AttributeError, KeyError, IndexError,
            _Unsupported, _Deadline, OSError, subprocess.SubprocessError):
        return None, False


def _cleanup_tree(base):
    for directory, _, _ in os.walk(base):
        Path(directory).chmod(0o700)
    shutil.rmtree(base)


def _stage_source(base, source, budget):
    root = base / 'source' / source.root_alias
    root.mkdir(parents=True, mode=0o755)
    for item in source.files:
        budget.check()
        target = root / item.path
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        with target.open('xb') as stream:
            stream.write(item.content)
        target.chmod(item.mode)
    return root


def _assemble_dependencies(root, prepared, manifest, budget):
    # Both trees are private verified copies. Source snapshots exclude every
    # node_modules component, so dependency placement cannot replace source.
    for item in manifest['files']:
        budget.check()
        target = root / item['path']
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        if target.exists() or target.is_symlink():
            raise ValueError('Prepared dependency collides with source')
        # Moving the verified inode avoids a second multi-GiB allocation.
        source_path = prepared / item['path']
        source_path.parent.chmod(0o700)
        source_path.rename(target)
    for link in manifest['workspaceLinks']:
        budget.check()
        target = root / link['target']
        if not target.is_dir() or not (target / 'package.json').is_file():
            raise ValueError('Declared workspace link lacks its exact source manifest')
        location = root / link['path']
        location.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        if location.exists() or location.is_symlink():
            raise ValueError('Declared workspace link collides with staged files')
        location.symlink_to(os.path.relpath(target, location.parent), target_is_directory=True)
    for directory, _, _ in os.walk(root, followlinks=False):
        Path(directory).chmod(0o555)


def _selected_sources(source, step):
    return [item.path for item in source.files
            if item.path.startswith(tuple(path + '/' for path in step['sourceDirectories']))
            and item.path.endswith(('.ts', '.tsx')) and not item.path.endswith('.d.ts')]


def _outcome(frame, step, source, workspace):
    """Reject incomplete, wrong-scope or malformed workload result claims."""
    if (not isinstance(frame, dict) or frame.get('kind') != 'result'
            or frame.get('recipe') != step['recipe'] or frame.get('status') != 'passed'
            or frame.get('configuration') != step['config']
            or frame.get('evidenceOrigin') != 'workload-claim' or frame.get('toolExitCode') != 0
            or type(frame.get('toolExitCode')) is not int):
        return False
    for key in ('testCount', 'skippedCount', 'failedCount', 'sourceCount'):
        if type(frame.get(key)) is not int or not 0 <= frame[key] <= 1_000_000:
            return False
    if frame['failedCount']:
        return False
    prefix = '/source/' + source.root_alias + '/'
    selected = frame.get('selectedFiles')
    reported = frame.get('reportedFiles')
    if any(not isinstance(paths, list) or not paths or len(paths) > 10_000
           or any(not isinstance(path, str) for path in paths)
           or len(paths) != len(set(paths)) for paths in (selected, reported)):
        return False
    if step['recipe'] == 'workspace_typescript':
        expected = [prefix + path for path in _selected_sources(source, step)]
        allowed = {prefix + item.path for item in source.files
                   if item.path.endswith(('.ts', '.tsx')) and not item.path.endswith('.d.ts')}
        return (frame.get('toolEntry') == 'node_modules/typescript/lib/typescript.js'
                and frame['testCount'] == 0 and frame['skippedCount'] == 0
                and frame['sourceCount'] == len(reported) and frame['sourceCount'] > 0
                and sorted(selected) == sorted(expected) and set(expected) <= set(reported) <= allowed
                and frame.get('modules') == [])
    expected = [prefix + path for path in step['testFiles']]
    modules = frame.get('modules')
    if (frame.get('toolEntry') != 'node_modules/vitest/dist/node.js'
            or frame['sourceCount'] != 0 or frame['testCount'] < 1
            or sorted(selected) != sorted(expected) or sorted(reported) != sorted(expected)
            or not isinstance(modules, list) or len(modules) != len(expected)):
        return False
    for module in modules:
        if (not isinstance(module, dict) or set(module) != {'path', 'passed', 'skipped', 'failed', 'state'}
                or not isinstance(module['path'], str) or module['path'] not in expected
                or module['state'] != 'passed'
                or any(type(module[key]) is not int or not 0 <= module[key] <= 1_000_000
                       for key in ('passed', 'skipped', 'failed'))
                or module['passed'] < 1 or module['failed'] != 0):
            return False
    return (len({module['path'] for module in modules}) == len(expected)
            and sum(module['passed'] for module in modules) == frame['testCount']
            and sum(module['skipped'] for module in modules) == frame['skippedCount'])


def _failure_summary(frame, step):
    """Bounded diagnostic counts only; never eligible as successful evidence."""
    if (not isinstance(frame, dict) or frame.get('kind') != 'result'
            or frame.get('status') != 'failed' or frame.get('recipe') != step['recipe']
            or frame.get('configuration') != step['config']
            or frame.get('evidenceOrigin') != 'workload-claim'):
        return None
    counts = ('testCount', 'skippedCount', 'failedCount', 'sourceCount')
    if any(type(frame.get(key)) is not int or not 0 <= frame[key] <= 1_000_000 for key in counts):
        return None
    code = frame.get('toolExitCode')
    if code is not None and (type(code) is not int or not 0 <= code <= 255):
        return None
    return {'evidenceOrigin': 'workload-claim', 'toolExitCode': code,
            **{key: frame[key] for key in counts}}


def run_prepared_workspace_step(source, dependency_directory, dependency_manifest, expected_toolchain,
                                profile, step_id, *, allow_local_docker=False, runtime_image=IMAGE,
                                check_authorized=None, cancel=None, runtime_identity=None,
                                record_event=None, staging_parent=None, expected_dependency_identity=None):
    """Execute one fixed step; an aggregate successful validation set is separate.

This deliberately is not wired into the organization scheduler yet. Exact
store integration and multi-step independent review must land before activation.
Durable mode requires all four resource/event/staging/dependency arguments. The
trusted caller must precommit the supplied resource identity and return True
from record_event only after durable commit; neither callback nor identity can
replace actual owner Docker opt-in or fresh authority checks.
"""
    clock = time.monotonic()
    result = {'runner': 'eidolon.prepared-workspace', 'runnerVersion': 1, 'status': 'blocked',
              'startedAt': datetime.now(timezone.utc).isoformat(), 'exitCode': None,
              'isolation': {'backend': 'linux-docker-pinned', 'established': False},
              'cleanupConfirmed': False, 'stagingCleanupConfirmed': False, 'sourceWritesPerformed': False,
              'limitations': LIMITATIONS}
    base = None
    created = False
    container = None
    identity = None
    staged = None
    event_started = False
    durable = any(item is not None for item in
                  (runtime_identity, record_event, staging_parent, expected_dependency_identity))
    try:
        if allow_local_docker is not True or runtime_image != IMAGE:
            raise ValueError('Owner must explicitly enable the exact pinned local Docker runtime')
        _authority(check_authorized)
        if durable:
            identity = resources.normalize_runtime_identity(runtime_identity)
            expected_dependency_identity = resources.directory_identity(expected_dependency_identity)
            if not callable(record_event) or staging_parent is None:
                raise ValueError('Durable runtime requires its event sink and private staging parent')
            result['resourceIdentity'] = identity
            result['dependencyRootIdentity'] = dict(zip(('device', 'inode'), expected_dependency_identity))
            result['containerLabels'] = resources.container_labels(identity)
        if type(source) is not ProjectSnapshotBundle:
            raise ValueError('Workspace execution requires an exact immutable source snapshot')
        source = ProjectSnapshotBundle(source.root_alias, source.root_identity, source.files, source.limits)
        # Runtime control frames use the same 512-byte exact relative paths as
        # profiles. Generic snapshots may intentionally permit larger names.
        for item in source.files:
            _path(item.path)
        normalized = normalize_validation_profile(profile)
        step = next((item for item in normalized['steps'] if item['id'] == step_id), None)
        if step is None:
            raise ValueError('Requested step is absent from the exact profile')
        limits = normalized['limits']
        if limits['cpus'] != 1 or limits['pids'] != 64:
            raise _Unsupported('Initial prepared workspace runtime supports exactly one CPU and 64 PIDs')
        grant = SimpleNamespace(memory_mb=limits['memoryMiB'], scratch_mb=limits['scratchMiB'],
                                output_bytes=limits['logBytes'], timeout_seconds=limits['wallSeconds'],
                                runtime_image=runtime_image)
        budget = _Budget(_AuthorityCancellation(cancel, check_authorized), grant.timeout_seconds, clock)
        budget.check()
        files = {item.path: item.content for item in source.files}
        if step['config'] not in files or any(path not in files for path in step['testFiles']):
            raise ValueError('Validation configuration or selected test is missing from the snapshot')
        lockfile = files.get('package-lock.json')
        manifests = {item['path']: files[item['path']] for item in dependency_manifest['packageManifests']}
        checked = normalize_prepared_dependency_manifest(dependency_manifest, lockfile_bytes=lockfile,
                    package_manifests=manifests, expected_toolchain=expected_toolchain)
        if checked['bundleSha256'] != normalized['bundleSha256']:
            raise ValueError('Prepared dependencies do not match the bound validation profile')
        if normalized['workspace'] not in checked['manifest']['selectedWorkspaces']:
            raise ValueError('Requested workspace is outside the prepared dependency graph')
        entry = _tool_entry(step, checked)
        result.update(snapshotSha256=source.snapshot_sha256, profileSha256=digest(normalized),
                      bundleSha256=checked['bundleSha256'], stepId=step_id,
                      stepDefinitionSha256=digest(step), recipe=step['recipe'], checkKind=step['checkKind'])
        if (sys.platform != 'linux' or platform.machine() not in ('x86_64', 'amd64')
                or not Path('/usr/bin/docker').is_file() or not Path('/var/run/docker.sock').is_socket()):
            raise _Unsupported('Prepared workspace requires explicitly enabled Linux x86-64 Docker; no fallback ran')
        program = Path('/usr/bin/docker').stat()
        if program.st_uid != 0 or program.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise _Unsupported('Docker client must be a trusted non-writable system executable')
        profile_bytes = (NODE_ASSETS / 'seccomp.json').read_bytes()
        if hashlib.sha256(profile_bytes).hexdigest() != PROFILE_SHA256:
            raise _Unsupported('Reviewed seccomp profile is damaged')
        seccomp = json.loads(profile_bytes)
        if durable:
            intent = resources.staging_intent(staging_parent, identity)
            result['containerName'] = resources.container_name(identity)
            resources.commit_runtime_event(record_event, identity, 'dispatch_planned', {
                'containerName': result['containerName'], 'labels': result['containerLabels'],
                'image': runtime_image, 'stagingIntent': intent,
                'dependencyRootIdentity': result['dependencyRootIdentity'],
                **{key: result[key] for key in ('snapshotSha256', 'profileSha256', 'bundleSha256',
                    'stepId', 'stepDefinitionSha256', 'recipe', 'checkKind')}})
            event_started = True
            _authority(check_authorized)
            budget.check()
            staged = resources.allocate_staging(intent, identity)
            base = Path(staged['path'])
            result['stagingIdentity'] = staged
            resources.commit_runtime_event(record_event, identity, 'staging_allocated', {'staging': staged})
            resources.verify_staging_identity(staged, identity)
            _authority(check_authorized)
            budget.check()
        else:
            base = Path(tempfile.mkdtemp(prefix='eidolon-workspace-'))
        docker_config = base / 'docker'
        docker_config.mkdir()
        (docker_config / 'config.json').write_text('{}')
        info = _json_command(['info', '--format', '{{json .}}'], docker_config, budget)
        if info.get('OSType') != 'linux' or info.get('CgroupVersion') != '2' or info.get('Architecture') not in ('x86_64', 'amd64'):
            raise _Unsupported('Prepared workspace requires Linux cgroup v2')
        image = _json_command(['image', 'inspect', runtime_image], docker_config, budget)[0]
        if (image.get('Os') != 'linux' or image.get('Architecture') != 'amd64'
                or runtime_image not in image.get('RepoDigests', []) or image.get('Config', {}).get('Volumes')):
            raise _Unsupported('Pinned runtime image does not match')
        _authority(check_authorized)
        prepared = base / 'dependencies'
        _stage_dependencies(dependency_directory, prepared, checked, lockfile, manifests, budget, expected_dependency_identity)
        root = _stage_source(base, source, budget)
        _assemble_dependencies(root, prepared, checked['manifest'], budget)
        assets = {name: (ASSETS / name).read_bytes() for name in ('supervisor.mjs', 'harness.mjs', 'protocol.mjs')}
        result['runtime'] = {'image': runtime_image, 'imageId': image['Id'], 'seccompSha256': PROFILE_SHA256,
                            'bootstrapSha256': digest({name: hashlib.sha256(data).hexdigest() for name, data in assets.items()})}
        (base / 'runner').mkdir(mode=0o755)
        for name, data in assets.items():
            (base / 'runner' / name).write_bytes(data)
            (base / 'runner' / name).chmod(0o444)
        (base / 'seccomp.json').write_bytes(profile_bytes)
        config = {'root': source.root_alias, 'recipe': step['recipe'], 'workspace': normalized['workspace'],
                  'configuration': step['config'], 'tests': step['testFiles'],
                  'selectedSources': _selected_sources(source, step), 'toolEntry': entry,
                  'timeoutMs': max(1, int((clock + grant.timeout_seconds - time.monotonic()) * 1000)),
                  'memoryBytes': grant.memory_mb * 1048576, 'scratchBytes': grant.scratch_mb * 1048576,
                  'outputBytes': grant.output_bytes, 'preparedToolchain': checked['manifest']['toolchain']}
        (base / 'runner' / 'config.json').write_text(json.dumps(config))
        (base / 'runner' / 'config.json').chmod(0o444)
        _authority(check_authorized)
        budget.check()
        name = resources.container_name(identity) if durable else 'eidolon-workspace-' + uuid.uuid4().hex
        result['containerName'] = name
        arguments = _create_arguments(name, base, grant)
        if durable:
            resources.verify_staging_identity(staged, identity)
            resources.commit_runtime_event(record_event, identity, 'container_create_planned',
                {'staging': staged, 'runtime': result['runtime']})
            # Add immutable exact labels before the image/command boundary.
            arguments[1:1] = [part for key, value in resources.container_labels(identity).items()
                             if key != 'ai.eidolon.execution' for part in ('--label', key + '=' + value)]
            _authority(check_authorized)
            budget.check()
            resources.verify_staging_identity(staged, identity)
        created = True  # An uncertain create response must retain cleanup uncertainty.
        try:
            code, output, _, forced = _docker(arguments, docker_config, budget)
            if forced == 'cancelled':
                raise _Cancelled()
            if forced == 'timed_out':
                raise _Deadline()
            if code or forced:
                raise _Unsupported('Container creation is unconfirmed')
            container = output['stdout'].decode().strip()
            if not re.fullmatch(r'[0-9a-f]{64}', container):
                raise _Unsupported('Container identity is invalid')
            result['containerId'] = container
            record = _json_command(['inspect', container], docker_config, budget)[0]
            if durable:
                resources.verify_container_identity(record, identity, image_id=image['Id'], container_id=container)
                resources.commit_runtime_event(record_event, identity, 'container_observed',
                    {'containerId': container, 'runtime': result['runtime'], 'staging': staged})
            _verify_container(record, name, base, grant, image['Id'], seccomp)
            result['containerConfigSha256'] = digest({'Config': record['Config'], 'HostConfig': record['HostConfig'], 'Mounts': record['Mounts']})
            namespaces = {key: os.readlink('/proc/self/ns/' + key) for key in ('mnt', 'pid', 'net', 'ipc', 'uts', 'cgroup')}
            if durable:
                resources.verify_staging_identity(staged, identity)
                resources.commit_runtime_event(record_event, identity, 'container_start_planned',
                    {'containerId': container, 'containerConfigSha256': result['containerConfigSha256']})
            _authority(check_authorized)
            budget.check()
            if durable:
                resources.verify_staging_identity(staged, identity)
            code, output, totals, forced = _docker(['start', '--attach', container], docker_config, budget, grant.output_bytes + 1048576)
            result.update(exitCode=code, stderr=_safe_log(output['stderr']),
                          stdoutBytes=totals['stdout'], stderrBytes=totals['stderr'])
            if forced == 'cancelled':
                raise _Cancelled()
            if forced == 'timed_out':
                raise _Deadline()
            frames = [json.loads(line) for line in output['stdout'].splitlines()]
            if any(not isinstance(frame, dict) for frame in frames):
                raise ValueError('Runtime returned malformed control frames')
            proof = frames[0] if frames else {}
            if (proof.get('kind') == 'isolation'
                    and all(type(proof.get(key)) is int for key in ('pid', 'supervisorUid', 'workloadUid'))
                    and proof.get('pid') == 1
                    and proof.get('supervisorUid') == 0 and proof.get('workloadUid') == 65534
                    and proof.get('supervisorCapabilities') == ['SETUID', 'SETGID']
                    and proof.get('workloadCapabilities') == []
                    and isinstance(proof.get('runtimeToolchain'), dict)
                    and all(proof['runtimeToolchain'].get(key) == checked['manifest']['toolchain'][key]
                            for key in ('platform', 'arch', 'libc', 'nodeVersion', 'nodeAbi'))
                    and isinstance(proof.get('namespaces'), dict)
                    and all(isinstance(proof['namespaces'].get(key), str)
                            and re.fullmatch(key + r':\[[0-9]+\]', proof['namespaces'][key])
                            and proof['namespaces'][key] != identity for key, identity in namespaces.items())):
                result['isolation'].update(established=True, network='none', sourceReadOnly=True,
                                           runtimeReadOnly=True, workloadUid=65534)
            result['status'] = forced or 'failed'
            if not forced and len(frames) == 2 and result['isolation']['established']:
                summary = _failure_summary(frames[1], step)
                if summary is not None:
                    result['failureClaim'] = summary
            if not forced and len(frames) == 2 and result['isolation']['established'] and code == 0:
                terminal_record = _json_command(['inspect', container], docker_config, _Budget(None, 5, time.monotonic()))[0]
                if durable:
                    resources.verify_container_identity(terminal_record, identity, image_id=image['Id'], container_id=container)
                terminal = terminal_record['State']
                if (_outcome(frames[1], step, source, normalized['workspace']) and terminal.get('Running') is False
                        and type(terminal.get('ExitCode')) is int and terminal['ExitCode'] == 0
                        and terminal.get('OOMKilled') is False):
                    result.update(status='passed', outcome=frames[1])
                    if durable:
                        result['terminalState'] = {'Running': False, 'ExitCode': 0, 'OOMKilled': False}
            _authority(check_authorized)
        finally:
            candidate = container if container and re.fullmatch(r'[0-9a-f]{64}', container) else name
            identifier, result['cleanupConfirmed'] = _cleanup_container(candidate, name, image['Id'], docker_config, identity)
            if identifier is not None:
                result['containerId'] = identifier
            if not result['cleanupConfirmed']:
                result.update(status='cleanup_failed', reason='Container cleanup is unconfirmed; no replay is safe')
    except (_Cancelled, _Deadline) as error:
        if result['status'] != 'cleanup_failed':
            result.update(status='cancelled' if isinstance(error, _Cancelled) else 'timed_out')
    except (_Unsupported, OSError, subprocess.SubprocessError) as error:
        if result['status'] != 'cleanup_failed':
            result.update(status='unsupported', reason=str(error)[:2000])
    except (ValueError, TypeError, AttributeError, KeyError, IndexError, UnicodeError, RecursionError) as error:
        if result['status'] != 'cleanup_failed':
            result.update(status='blocked', reason=str(error)[:2000])
    finally:
        if base is not None:
            if not created or result['cleanupConfirmed']:
                try:
                    if durable:
                        resources.cleanup_owned_staging(staged, identity)
                    else:
                        _cleanup_tree(base)
                    result['stagingCleanupConfirmed'] = True
                except (OSError, ValueError):
                    result.update(status='staging_cleanup_failed', stagingCleanupConfirmed=False,
                                  reason='Private source staging cleanup failed', retainedStagingDirectory=str(base))
            else:
                result['retainedStagingDirectory'] = str(base)
        result.update(endedAt=datetime.now(timezone.utc).isoformat(), durationSeconds=round(time.monotonic() - clock, 6))
        if durable and event_started:
            try:
                resources.commit_runtime_event(record_event, identity, 'cleanup_observed', {
                    'containerId': result.get('containerId'),
                    'containerCleanupConfirmed': result['cleanupConfirmed'],
                    'stagingCleanupConfirmed': result['stagingCleanupConfirmed'], 'staging': staged})
                resources.commit_runtime_event(record_event, identity, 'receipt',
                    {'receipt': result, 'receiptSha256': digest(result)})
            except ValueError:
                result.update(status='persistence_failed',
                              reason='Terminal host receipt was not durably committed; no replay is safe')
    return result


def cleanup_prepared_workspace_resource(runtime_identity, *, staging_identity, expected_runtime,
        container_id=None, container_create_attempted=True, container_cleanup_confirmed=False,
        record_event, allow_local_docker=False):
    """Reconcile only an already authorized, durably retained owned resource.

The caller must supply a complete trusted event history's exact identities. No
fresh execution/root authority is required for narrow owned cleanup following
revocation. No creation, start, PID kill, name-prefix search or replay occurs.
Unknown disappearance is not proof of cleanup; a prior confirmed host cleanup
observation can be supplied to finish removing its retained staging tree.
"""
    identity = resources.normalize_runtime_identity(runtime_identity)
    staging = resources.normalize_staging_identity(staging_identity, identity)
    runtime = resources._runtime(resources._bounded_json(expected_runtime)) if expected_runtime is not None else None
    if type(container_create_attempted) is not bool or type(container_cleanup_confirmed) is not bool:
        raise ValueError('Reconciliation requires exact dispatch and cleanup flags')
    if container_id is not None:
        resources._hash(container_id)
    if not container_create_attempted and (container_id is not None or container_cleanup_confirmed):
        raise ValueError('Unattempted container creation cannot have an observed ID or cleanup proof')
    if container_cleanup_confirmed and container_id is None:
        raise ValueError('Prior confirmed cleanup requires its exact retained container ID')
    if not callable(record_event):
        raise ValueError('Reconciliation requires the trusted durable event sink')
    observed_id, container_done, staging_done = container_id, container_cleanup_confirmed, False
    reason = None
    try:
        resources.verify_staging_identity(staging, identity)
        if container_create_attempted and not container_done:
            if allow_local_docker is not True or runtime is None:
                raise ValueError('Owned-container cleanup requires its retained explicit Docker opt-in and image identity')
            if (sys.platform != 'linux' or platform.machine() not in ('x86_64', 'amd64')
                    or not Path('/usr/bin/docker').is_file() or not Path('/var/run/docker.sock').is_socket()):
                raise _Unsupported('Exact Docker cleanup is unavailable; resource remains retained')
            program = Path('/usr/bin/docker').stat()
            if program.st_uid != 0 or program.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                raise _Unsupported('Docker client is not a trusted system executable')
            name = resources.container_name(identity)
            found, container_done = _cleanup_container(container_id or name, name, runtime['imageId'],
                                                        Path(staging['path']) / 'docker', identity)
            if found is not None:
                observed_id = found
        if not container_create_attempted or container_done:
            staging_done = resources.cleanup_owned_staging(staging, identity)
    except (OSError, ValueError, _Unsupported) as error:
        reason = str(error)[:2000]
    observation = {'containerId': observed_id, 'containerCleanupConfirmed': container_done,
                   'stagingCleanupConfirmed': staging_done, 'staging': staging}
    resources.commit_runtime_event(record_event, identity, 'cleanup_observed', observation)
    return {**observation, 'replayAllowed': False, 'acceptanceAuthorized': False,
            'state': 'cleanup_confirmed' if staging_done and (not container_create_attempted or container_done)
                     else 'cleanup_unconfirmed', 'reason': reason}

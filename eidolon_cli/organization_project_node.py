"""Experimental fixed Node adapter; not registered as an execution recipe yet.

The production grant parser deliberately cannot select this backend until native
adversarial verification and independent review have completed. No image pulls,
software installation, Docker access changes or host execution fallback occur.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid

from eidolon_cli.organization_project_runner import (
    _Budget, _Cancelled, _Deadline, _Unsupported, _read_process, _relative_path,
    _snapshot, snapshot_digest,
)
from eidolon_cli.organization_project_validation import digest

IMAGE = 'node@sha256:a5f4366e834695d5f363b920c104953a535d3a9864d1bdf74566a0ebe01daee5'
PROFILE_SHA256 = '4445461a8780818be1c3c495c01256a86365356d2cd5d58233fe4d7dab0b5a38'
ASSETS = Path(__file__).with_name('project_node_runtime')
LIMITATIONS = ('Only exact selected files and the pinned Node runtime are available. No project dependency '
              'installation or package-script dispatch. Tests may execute programs inside the bounded container. '
              'Project code can falsify assertions or test reporting; passing claims require independent review. '
              'Docker daemon and the root-owned fixed supervisor are trusted; project code runs as UID 65534. '
              'No source publication, deployment or original-source writes occur.')


@dataclass(frozen=True)
class NodeTestGrant:
    recipe: str = 'node_test'
    allow_local_docker: bool = False
    runtime_image: str = IMAGE
    root: str = 'root0'
    test_directory: str = '.'
    pattern: str = '*.test.cjs'
    timeout_seconds: int = 20
    memory_mb: int = 512
    scratch_mb: int = 16
    output_bytes: int = 16384


def normalize_node_grant(value):
    if isinstance(value, NodeTestGrant):
        value = asdict(value)
    if not isinstance(value, dict) or set(value) - set(NodeTestGrant.__dataclass_fields__):
        raise ValueError('Node recipe accepts only fixed runtime and bounded limits')
    values = {**asdict(NodeTestGrant()), **value}
    if values['recipe'] != 'node_test' or values['runtime_image'] != IMAGE:
        raise ValueError('Only the reviewed digest-pinned Node test runtime is accepted')
    if values['allow_local_docker'] is not True:
        raise ValueError('Owner must explicitly enable local Docker access for this recipe')
    if not isinstance(values['root'], str) or not re.fullmatch(r'root(?:0|[1-9][0-9]*)', values['root']):
        raise ValueError('Node root must be an owner-configured read-root alias')
    if not _relative_path(values['test_directory'], dot=True):
        raise ValueError('Node test directory must be a canonical relative path')
    if not isinstance(values['pattern'], str) or not re.fullmatch(r'[A-Za-z0-9_*?.-]{1,100}\.test\.(?:cjs|mjs|js)', values['pattern']):
        raise ValueError('Node pattern must select bounded .test.js, .test.cjs or .test.mjs filenames')
    for key, low, high in [('timeout_seconds', 2, 120), ('memory_mb', 256, 1024),
                           ('scratch_mb', 1, 64), ('output_bytes', 1024, 65536)]:
        if type(values[key]) is not int or not low <= values[key] <= high:
            raise ValueError(f'Node {key} must be an integer between {low} and {high}')
    return NodeTestGrant(**values)


def _docker(args, config, budget, limit=1048576):
    budget.check()
    process = subprocess.Popen(['/usr/bin/docker', '--config', str(config), '--host', 'unix:///var/run/docker.sock', *args],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd='/',
        env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, start_new_session=True, close_fds=True)
    return _read_process(process, {}, limit, budget, stop_on_output=True)


def _json_command(args, config, budget):
    code, output, _, forced = _docker(args, config, budget)
    if forced == 'cancelled':
        raise _Cancelled()
    if forced == 'timed_out':
        raise _Deadline()
    if forced or code:
        raise _Unsupported('Required Docker inspection failed: ' + output['stderr'].decode('utf-8', 'replace')[:1000])
    return json.loads(output['stdout'])


def _create_arguments(name, base, grant):
    memory = grant.memory_mb * 1048576
    return ['create', '--pull=never', '--name', name, '--label', 'ai.eidolon.execution=' + name,
        '--runtime=runc', '--user=0:0', '--init=false', '--read-only', '--network=none', '--ipc=none',
        '--cgroupns=private', '--cap-drop=ALL', '--cap-add=SETUID', '--cap-add=SETGID',
        '--security-opt=no-new-privileges=true', '--security-opt=seccomp=' + str(base / 'seccomp.json'),
        '--pids-limit=64', '--cpus=1', '--memory=' + str(memory), '--memory-swap=' + str(memory),
        '--restart=no', '--no-healthcheck', '--log-driver=none', '--ulimit=core=0:0', '--ulimit=nofile=256:256',
        '--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=' + str(grant.scratch_mb * 1048576) + ',nr_inodes=4096,mode=1777',
        '--mount=type=bind,src=' + str(base / 'source') + ',dst=/source,readonly',
        '--mount=type=bind,src=' + str(base / 'runner') + ',dst=/runner,readonly',
        '--workdir=/runner', '--env=NODE_OPTIONS=', '--env=NODE_PATH=', '--entrypoint=/usr/local/bin/node',
        grant.runtime_image, '--disable-sigusr1', '/runner/supervisor.mjs']


def _verify_container(record, name, base, grant, image_id, profile):
    host, config = record['HostConfig'], record['Config']
    expected = {'Privileged': False, 'ReadonlyRootfs': True, 'NetworkMode': 'none', 'IpcMode': 'none',
        'PidMode': '', 'UTSMode': '', 'CgroupnsMode': 'private', 'PidsLimit': 64, 'NanoCpus': 1000000000,
        'Memory': grant.memory_mb * 1048576, 'MemorySwap': grant.memory_mb * 1048576, 'Init': False,
        'Runtime': 'runc', 'PublishAllPorts': False}
    if any(host.get(key) != value for key, value in expected.items()):
        raise _Unsupported('Container isolation configuration mismatch: ' + json.dumps({key: host.get(key) for key, value in expected.items() if host.get(key) != value}, sort_keys=True))
    if (record['Image'] != image_id or config['User'] != '0:0' or config.get('Labels', {}).get('ai.eidolon.execution') != name
            or config.get('Entrypoint') != ['/usr/local/bin/node']
            or config.get('Cmd') != ['--disable-sigusr1', '/runner/supervisor.mjs']
            or config.get('Healthcheck', {}).get('Test') != ['NONE']
            or set(host.get('CapAdd') or ()) != {'CAP_SETUID', 'CAP_SETGID'} or host.get('CapDrop') != ['ALL']
            or host.get('LogConfig', {}).get('Type') != 'none' or host.get('RestartPolicy', {}).get('Name') != 'no'
            or host.get('Devices') or host.get('DeviceRequests') or host.get('VolumesFrom') or host.get('PortBindings')
            or host.get('Privileged') or config.get('Tty')):
        raise _Unsupported('Container runtime identity or privileges changed: ' + json.dumps({'imageMatches': record['Image'] == image_id, 'user': config.get('User'), 'entrypoint': config.get('Entrypoint'), 'cmd': config.get('Cmd'), 'healthcheck': config.get('Healthcheck'), 'capAdd': host.get('CapAdd'), 'capDrop': host.get('CapDrop'), 'logType': host.get('LogConfig', {}).get('Type'), 'restart': host.get('RestartPolicy'), 'devices': host.get('Devices'), 'deviceRequests': host.get('DeviceRequests'), 'volumesFrom': host.get('VolumesFrom'), 'portBindings': host.get('PortBindings'), 'tty': config.get('Tty')}, sort_keys=True))
    options = host.get('SecurityOpt', [])
    if 'no-new-privileges=true' not in options or len(options) != 2:
        raise _Unsupported('Container security options do not match')
    seccomp = [option.split('=', 1)[1] for option in options if option.startswith('seccomp=')]
    if len(seccomp) != 1 or json.loads(seccomp[0]) != profile:
        raise _Unsupported('Container seccomp profile does not match its reviewed identity')
    mounts = record.get('Mounts', [])
    if len(mounts) != 2 or any(not any(m.get('Type') == 'bind' and m.get('Source') == str(base / source)
        and m.get('Destination') == target and m.get('RW') is False and m.get('Propagation') == 'rprivate'
        for m in mounts) for source, target in [('source', '/source'), ('runner', '/runner')]):
        raise _Unsupported('Container mounts do not match the copied snapshot and trusted runtime')


def run_node_tests(files, grant, cancel=None):
    """Experimental direct entry point for native verification; not organization dispatch."""
    clock = time.monotonic()
    result = {'runner': 'eidolon.isolated-node-test', 'runnerVersion': 1, 'status': 'blocked',
        'startedAt': datetime.now(timezone.utc).isoformat(), 'exitCode': None, 'testCount': 0, 'skippedCount': 0,
        'isolation': {'backend': 'linux-docker-pinned', 'established': False}, 'runtime': None,
        'snapshotSha256': None, 'sourceWritesPerformed': False, 'limitations': LIMITATIONS,
        'stdout': '', 'stderr': '', 'cleanupConfirmed': False}
    container = None
    try:
        grant = normalize_node_grant(grant)
        files = _snapshot(files)
        if any(item['path'].split('/')[0] != grant.root for item in files):
            raise ValueError('Every Node snapshot file must belong to its granted root')
        root = grant.root + ('/' + grant.test_directory if grant.test_directory != '.' else '')
        tests = ['/source/' + item['path'] for item in files if str(Path(item['path']).parent) == root
                 and fnmatch.fnmatchcase(Path(item['path']).name, grant.pattern)]
        if not tests:
            raise ValueError('The exact snapshot contains no selected Node test files')
        result.update(grant=asdict(grant), selectedTests=tests, snapshotSha256=snapshot_digest(files), files=[{k: f[k] for k in ('path', 'sha256', 'revision')} for f in files])
        budget = _Budget(cancel, grant.timeout_seconds, clock)
        budget.check()
        if sys.platform != 'linux' or platform.machine() not in ('x86_64', 'amd64') or not Path('/usr/bin/docker').is_file() or not Path('/var/run/docker.sock').is_socket():
            raise _Unsupported('Requires an explicitly enabled preinstalled local Linux x86-64 Docker runtime; no fallback was run')
        program = Path('/usr/bin/docker').stat()
        if program.st_uid != 0 or program.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise _Unsupported('Docker client must be a trusted non-writable system executable')
        profile_bytes = (ASSETS / 'seccomp.json').read_bytes()
        if hashlib.sha256(profile_bytes).hexdigest() != PROFILE_SHA256:
            raise _Unsupported('Reviewed seccomp profile is damaged')
        profile = json.loads(profile_bytes)
        with tempfile.TemporaryDirectory(prefix='eidolon-node-') as directory:
            base = Path(directory)
            config = base / 'docker'; config.mkdir()
            (config / 'config.json').write_text('{}')
            info = _json_command(['info', '--format', '{{json .}}'], config, budget)
            if info.get('OSType') != 'linux' or info.get('CgroupVersion') != '2' or info.get('Architecture') not in ('x86_64', 'amd64'):
                raise _Unsupported('Local Docker requires Linux cgroup v2 enforcement')
            image = _json_command(['image', 'inspect', grant.runtime_image], config, budget)[0]
            if image.get('Architecture') != 'amd64' or grant.runtime_image not in image.get('RepoDigests', []) or image.get('Config', {}).get('Volumes'):
                raise _Unsupported('Pinned runtime image identity or volume policy does not match')
            assets = {name: (ASSETS / name).read_bytes() for name in ('supervisor.mjs', 'harness.mjs')}
            result['runtime'] = {'image': grant.runtime_image, 'imageId': image['Id'], 'architecture': image['Architecture'], 'seccompSha256': PROFILE_SHA256,
                'bootstrapSha256': digest({name: hashlib.sha256(data).hexdigest() for name, data in assets.items()})}
            for child in ('source', 'runner'):
                (base / child).mkdir(mode=0o755)
            for item in files:
                target = base / 'source' / item['path']; target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(item['content'], encoding='utf-8'); target.chmod(0o444)
            for name, data in assets.items():
                (base / 'runner' / name).write_bytes(data); (base / 'runner' / name).chmod(0o444)
            (base / 'seccomp.json').write_bytes(profile_bytes)
            (base / 'runner' / 'config.json').write_text(json.dumps({'tests': tests, 'root': grant.root,
                'timeoutMs': grant.timeout_seconds * 1000, 'memoryBytes': grant.memory_mb * 1048576,
                'scratchBytes': grant.scratch_mb * 1048576, 'outputBytes': grant.output_bytes}))
            name = 'eidolon-test-' + uuid.uuid4().hex
            result['containerName'] = name
            try:
                code, output, _, forced = _docker(_create_arguments(name, base, grant), config, budget)
                if forced == 'cancelled':
                    raise _Cancelled()
                if forced == 'timed_out':
                    raise _Deadline()
                if code or forced:
                    raise _Unsupported('Container creation unconfirmed; inspect retained container identity before retrying')
                container = output['stdout'].decode().strip()
                if not re.fullmatch(r'[0-9a-f]{64}', container):
                    raise _Unsupported('Container creation returned an invalid identity')
                result['containerId'] = container
                record = _json_command(['inspect', container], config, budget)[0]
                _verify_container(record, name, base, grant, image['Id'], profile)
                result['containerConfigSha256'] = digest({'Config': record['Config'], 'HostConfig': record['HostConfig'], 'Mounts': record['Mounts']})
                parent_ns = {name: os.readlink('/proc/self/ns/' + name) for name in ('mnt', 'pid', 'net', 'ipc', 'uts', 'cgroup')}
                code, output, totals, forced = _docker(['start', '--attach', container], config, budget, grant.output_bytes + 4096)
                result.update(exitCode=code, stderr=output['stderr'].decode('utf-8', 'replace'), stdoutBytes=totals['stdout'], stderrBytes=totals['stderr'])
                lines = output['stdout'].splitlines()
                proof = json.loads(lines[0]) if lines and lines[0].startswith(b'{') else {}
                if proof.get('kind') == 'isolation':
                    if (proof.get('pid') != 1 or proof.get('supervisorUid') != 0 or proof.get('workloadUid') != 65534
                            or proof.get('supervisorCapabilities') != ['SETUID', 'SETGID'] or proof.get('workloadCapabilities') != []
                            or any(not re.fullmatch(name + r':\[[0-9]+\]', proof.get('namespaces', {}).get(name, ''))
                                   or proof['namespaces'][name] == identity for name, identity in parent_ns.items())):
                        raise _Unsupported('Live namespace or workload identity did not match')
                    result['isolation'].update(established=True, sourceReadOnly=True, runtimeReadOnly=True,
                        network='none', processLimit=64, supervisorUid=0, workloadUid=65534, seccompSha256=PROFILE_SHA256)
                if forced:
                    result['status'] = forced
                else:
                    frames = [json.loads(line) for line in lines]
                    result.update(status='failed', reason='Runtime did not return an exact complete isolation and terminal receipt')
                    if len(frames) == 2 and result['isolation']['established']:
                        outcome = frames[1]
                        if outcome.get('kind') == 'timeout' and code == 124:
                            result['status'] = 'timed_out'
                        elif outcome.get('kind') == 'output_limit' and code == 126:
                            result['status'] = 'output_limit'
                        elif outcome.get('kind') == 'result':
                            result.update(testCount=outcome.get('testCount', 0), skippedCount=outcome.get('skippedCount', 0))
                            terminal = _json_command(['inspect', container], config, _Budget(None, 5, time.monotonic()))[0]['State']
                            if code == 0 and outcome.get('status') == 'passed' and type(result['testCount']) is int and result['testCount'] > 0 and terminal.get('Running') is False and terminal.get('ExitCode') == 0 and terminal.get('OOMKilled') is False:
                                result['status'] = 'passed'
                                result.pop('reason', None)
            finally:
                cleanup = _Budget(None, 5, time.monotonic())
                try:
                    # A daemon-side create may succeed after its client loses the
                    # reply. Resolve only this random name, then validate all
                    # ownership fields before targeting an immutable ID.
                    candidate = container if container and re.fullmatch(r'[0-9a-f]{64}', container) else name
                    record = _json_command(['inspect', candidate], config, cleanup)[0]
                    identifier = record.get('Id', '')
                    owned = (re.fullmatch(r'[0-9a-f]{64}', identifier)
                             and record.get('Name') == '/' + name
                             and record.get('Image') == image['Id']
                             and record.get('Config', {}).get('Labels', {}).get('ai.eidolon.execution') == name)
                    if owned:
                        result['containerId'] = identifier
                        code, _, _, forced = _docker(['rm', '--force', identifier], config, cleanup)
                        result['cleanupConfirmed'] = code == 0 and forced is None
                except (ValueError, TypeError, KeyError, IndexError, _Unsupported, _Deadline, OSError, subprocess.SubprocessError):
                    # Even "not found" after a lost create reply is not proof
                    # that an asynchronous daemon operation cannot finish later.
                    result['cleanupConfirmed'] = False
                if not result['cleanupConfirmed']:
                    result.update(status='cleanup_failed', reason='Container creation or cleanup is unconfirmed; inspect retained identity before replay')
    except _Cancelled:
        if result['status'] != 'cleanup_failed':
            result.update(status='cancelled', reason='Cancelled before project execution')
    except _Deadline:
        if result['status'] != 'cleanup_failed':
            result.update(status='timed_out', reason='Overall preparation or execution deadline exceeded')
    except (ValueError, TypeError, KeyError, IndexError, UnicodeError) as error:

        if result['status'] != 'cleanup_failed':
            result.update(status='blocked', reason=str(error)[:2000])
    except (_Unsupported, OSError, subprocess.SubprocessError) as error:

        if result['status'] != 'cleanup_failed':
            result.update(status='unsupported', reason=str(error)[:2000])
    finally:
        result.update(endedAt=datetime.now(timezone.utc).isoformat(), durationSeconds=round(time.monotonic() - clock, 6))
    return result

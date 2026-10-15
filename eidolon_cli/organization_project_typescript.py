"""Experimental fixed TypeScript check; production recipe dispatch remains disabled."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
from eidolon_cli.organization_project_node import (
    IMAGE, PROFILE_SHA256, ASSETS as NODE_ASSETS,
    _docker, _json_command, _create_arguments, _verify_container,
)
from eidolon_cli.organization_project_compiler_bundle import copy_verified_compiler, compiler_inventory
from eidolon_cli.organization_project_runner import (
    _Budget, _Cancelled, _Deadline, _Unsupported, _relative_path, _snapshot, snapshot_digest,
)
from eidolon_cli.organization_project_validation import canonical, digest

ASSETS = Path(__file__).with_name('project_typescript_runtime')
LIMITATIONS = 'Typecheck only: fixed TypeScript compiler, selected source closure and standard libraries. No dependency install, plugins, package scripts, build output or source writes.'

@dataclass(frozen=True)
class TypecheckGrant:
    recipe: str = 'typescript_check'
    allow_local_docker: bool = False
    runtime_image: str = IMAGE
    compiler_directory: str = ''
    root: str = 'root0'
    configuration: str = 'tsconfig.json'
    timeout_seconds: int = 30
    memory_mb: int = 512
    scratch_mb: int = 16
    output_bytes: int = 16384

def normalize_typecheck_grant(value):
    if isinstance(value, TypecheckGrant):
        value = asdict(value)
    if not isinstance(value, dict) or set(value) - set(TypecheckGrant.__dataclass_fields__):
        raise ValueError('Typecheck accepts only fixed compiler configuration and bounded limits')
    values = {**asdict(TypecheckGrant()), **value}
    if values['recipe'] != 'typescript_check' or values['runtime_image'] != IMAGE or values['allow_local_docker'] is not True:
        raise ValueError('Explicit owner Docker and pinned compiler runtime authorization is required')
    directory = values['compiler_directory']
    if not isinstance(directory, str) or not directory.startswith('/') or len(directory) > 4096 or any(part in ('', '.', '..') for part in directory.split('/')[1:]) or '\\' in directory:
        raise ValueError('Compiler directory must be explicitly configured and canonical absolute')
    if not isinstance(values['root'], str) or not re.fullmatch(r'root(?:0|[1-9][0-9]*)', values['root']):
        raise ValueError('Typecheck requires an owner-configured root alias')
    if not _relative_path(values['configuration']) or not values['configuration'].endswith('.json'):
        raise ValueError('Typecheck configuration must be an exact relative JSON file')
    for key, low, high in [('timeout_seconds', 2, 120), ('memory_mb', 256, 1024), ('scratch_mb', 1, 64), ('output_bytes', 1024, 65536)]:
        if type(values[key]) is not int or not low <= values[key] <= high:
            raise ValueError('Typecheck resource bound is invalid: ' + key)
    return TypecheckGrant(**values)


def _allowed_input_descriptors(files):
    allowed = {'/source/' + item['path']: {**{k: item[k] for k in ('path', 'sha256', 'revision')}, 'path': '/source/' + item['path'], 'kind': 'source', 'revision': str(item['revision'])} for item in files}
    allowed.update({'/runner/compiler/' + item['path']: {**item, 'path': '/runner/compiler/' + item['path'], 'kind': 'compiler_library'} for item in compiler_inventory()['files'] if item['path'].startswith('lib/') and item['path'].endswith('.d.ts')})
    return allowed


def valid_compiler_outcome(result, files, grant):
    try:
        inputs = result['actualInputs']
        configuration = result['effectiveConfiguration']
        allowed = _allowed_input_descriptors(files)
        source_config = next(item['content'] for item in files if item['path'] == grant.root + '/' + grant.configuration)
        document = json.loads(source_config)
        if not isinstance(document, dict) or set(document) - {'compilerOptions'}:
            return False
        supplied = document.get('compilerOptions', {})
        supported = {'strict', 'noUnusedLocals', 'noUnusedParameters', 'exactOptionalPropertyTypes', 'noUncheckedIndexedAccess'}
        if not isinstance(supplied, dict) or set(supplied) - supported or any(type(value) is not bool for value in supplied.values()):
            return False
        expected_configuration = {'strict': True, **supplied, 'noEmit': True, 'noCheck': False,
            'incremental': False, 'composite': False, 'skipLibCheck': False, 'skipDefaultLibCheck': False,
            'libReplacement': False, 'types': [], 'target': 9, 'module': 99, 'moduleResolution': 100,
            'allowImportingTsExtensions': True}
        required = {'/source/' + item['path'] for item in files if item['path'].endswith('.ts')}
        required.add('/source/' + grant.root + '/' + grant.configuration)
        paths = [item['path'] for item in inputs]
        count = sum(item['path'].endswith('.ts') and not item['path'].endswith('.d.ts') for item in files)
        return (type(result['inputCount']) is int and result['inputCount'] == count and count > 0
            and type(result['diagnosticCount']) is int and result['diagnosticCount'] == 0
            and isinstance(inputs, list) and len(inputs) <= 204 and len(paths) == len(set(paths))
            and required <= set(paths) and set(paths) == set(allowed) and all(allowed.get(item['path']) == item for item in inputs)
            and digest(inputs) == result['actualInputsSha256']
            and canonical(configuration) == canonical(expected_configuration)
            and digest(configuration) == result['effectiveConfigurationSha256'])
    except (KeyError, TypeError, ValueError, StopIteration):
        return False

class _CopyBudget:
    """Share the overall cancellation/deadline through every compiler read."""
    def __init__(self, budget):
        self.budget = budget

    def is_set(self):
        self.budget.check()
        return False


def run_typecheck(files, grant, cancel=None):
    """Execute the exact selected snapshot under the explicit owner grant."""
    clock = time.monotonic()
    result = {'runner': 'eidolon.isolated-typescript-check', 'runnerVersion': 1, 'status': 'blocked',
        'startedAt': datetime.now(timezone.utc).isoformat(), 'exitCode': None, 'checkKind': 'project_typecheck', 'inputCount': 0,
        'isolation': {'backend': 'linux-docker-pinned', 'established': False}, 'runtime': None,
        'snapshotSha256': None, 'sourceWritesPerformed': False, 'limitations': LIMITATIONS,
        'stdout': '', 'stderr': '', 'cleanupConfirmed': False}
    container = None
    try:
        grant = normalize_typecheck_grant(grant)
        files = _snapshot(files)
        if any(item['path'].split('/')[0] != grant.root for item in files):
            raise ValueError('Every TypeScript snapshot file must belong to its granted root')
        configuration = grant.root + '/' + grant.configuration
        if configuration not in {item['path'] for item in files}:
            raise ValueError('Exact TypeScript configuration must be selected')
        if not any(item['path'].endswith('.ts') and not item['path'].endswith('.d.ts') for item in files):
            raise ValueError('Exact snapshot must contain TypeScript source')
        result.update(grant=asdict(grant), snapshotSha256=snapshot_digest(files), files=[{k: f[k] for k in ('path', 'sha256', 'revision')} for f in files])
        if len(canonical(list(_allowed_input_descriptors(files).values())).encode('utf-8')) > 49152:
            raise ValueError('TypeScript input descriptor manifest exceeds its 49152-byte control budget')
        budget = _Budget(cancel, grant.timeout_seconds, clock)
        budget.check()
        if sys.platform != 'linux' or platform.machine() not in ('x86_64', 'amd64') or not Path('/usr/bin/docker').is_file() or not Path('/var/run/docker.sock').is_socket():
            raise _Unsupported('Requires an explicitly enabled preinstalled local Linux x86-64 Docker runtime; no fallback was run')
        program = Path('/usr/bin/docker').stat()
        if program.st_uid != 0 or program.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise _Unsupported('Docker client must be a trusted non-writable system executable')
        profile_bytes = (NODE_ASSETS / 'seccomp.json').read_bytes()
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
            if image.get('Os') != 'linux' or image.get('Architecture') != 'amd64' or grant.runtime_image not in image.get('RepoDigests', []) or image.get('Config', {}).get('Volumes'):
                raise _Unsupported('Pinned runtime image identity or volume policy does not match')
            assets = {name: (ASSETS / name).read_bytes() for name in ('supervisor.mjs', 'harness.mjs', 'compiler-inventory.json')}
            result['runtime'] = {'image': grant.runtime_image, 'imageId': image['Id'], 'architecture': image['Architecture'], 'seccompSha256': PROFILE_SHA256,
                'bootstrapSha256': digest({name: hashlib.sha256(data).hexdigest() for name, data in assets.items()})}
            for child in ('source', 'runner'):
                (base / child).mkdir(mode=0o755)
            for item in files:
                target = base / 'source' / item['path']; target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(item['content'], encoding='utf-8'); target.chmod(0o444)
            for name, data in assets.items():
                (base / 'runner' / name).write_bytes(data); (base / 'runner' / name).chmod(0o444)
            compiler = copy_verified_compiler(grant.compiler_directory, base / 'runner' / 'compiler', _CopyBudget(budget))
            budget.check()
            result['runtime']['compilerInventorySha256'] = compiler['inventorySha256']
            (base / 'seccomp.json').write_bytes(profile_bytes)
            (base / 'runner' / 'config.json').write_text(json.dumps({'files': [{**item, 'revision': str(item['revision'])} for item in result['files']], 'configuration': configuration, 'root': grant.root,
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
                code, output, totals, forced = _docker(['start', '--attach', container], config, budget, grant.output_bytes + 65536)
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
                            result.update({key: outcome.get(key) for key in ('inputCount', 'diagnosticCount', 'effectiveConfiguration', 'effectiveConfigurationSha256', 'actualInputs', 'actualInputsSha256')})
                            terminal = _json_command(['inspect', container], config, _Budget(None, 5, time.monotonic()))[0]['State']
                            if code == 0 and outcome.get('status') == 'passed' and valid_compiler_outcome(result, files, grant) and terminal.get('Running') is False and terminal.get('ExitCode') == 0 and terminal.get('OOMKilled') is False:
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

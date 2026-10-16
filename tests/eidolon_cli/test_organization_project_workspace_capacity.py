"""Native capacity/uncertain-cleanup gates; absent opt-in explicitly skips them.

Kernel cgroup counters are host observations. Scratch errno/statfs and test
results remain workload claims. These tests do not establish durable store replay
protection, and their identity-checked finalizer does not upgrade a failed receipt.
"""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import errno
import json
import os
from pathlib import Path
import re
import stat
import threading
import time
import uuid

import pytest

from eidolon_cli import organization_project_workspace_node as runtime
from eidolon_cli.organization_project_compiler_bundle import _directory
from tests.eidolon_cli.test_organization_project_workspace_abuse import attach_frames, process_identity
from tests.eidolon_cli.test_organization_project_workspace_native import changed, prepared as prepared

CONTROL_BYTES = 1048576
LOG_BYTES = 8192
WALL_SECONDS = 120
CLAIM_PREFIX = 'EIDOLON_CAPACITY_WORKLOAD_CLAIM '


def diagnostic(result):
    return {key: result.get(key) for key in ('status', 'reason', 'cleanupConfirmed', 'exitCode', 'isolation')}


def counters(data):
    if len(data) > 4096:
        raise ValueError('Kernel counter record exceeds its bound')
    result = {}
    for line in data.decode('ascii', 'strict').splitlines():
        fields = line.split()
        if (len(fields) != 2 or not re.fullmatch('[a-z_]+', fields[0])
                or not fields[1].isdigit() or fields[0] in result):
            raise ValueError('Invalid kernel counter record')
        result[fields[0]] = int(fields[1])
    if not result:
        raise ValueError('Missing kernel counters')
    return result


def cgroup_path(data):
    if len(data) > 4096:
        raise ValueError('Host PID cgroup path exceeds its bound')
    lines = data.decode('ascii', 'strict').splitlines()
    if len(lines) != 1 or not lines[0].startswith('0::/'):
        raise ValueError('Host PID does not identify one unified cgroup')
    path = lines[0][3:]
    if (path == '/' or any(part in ('', '.', '..') or not re.fullmatch('[A-Za-z0-9_.:@-]+', part)
                           for part in path.split('/')[1:])):
        raise ValueError('Host PID cgroup path is not exact')
    return path


@pytest.mark.parametrize('data', [b'', b'max 1\nmax 2\n', b'max -1\n', b'max nope\n', b'x' * 4097])
def test_kernel_counter_parser_rejects_ambiguous_or_missing_evidence(data):
    with pytest.raises(ValueError):
        counters(data)


@pytest.mark.parametrize('data', [b'0::/\n', b'0::/../container\n', b'0::/container//child\n',
                                 b'1:memory:/container\n', b'0::/container\n0::/other\n', b'0::/' + b'x' * 4097])
def test_host_cgroup_parser_rejects_wrong_or_ambiguous_scope(data):
    with pytest.raises(ValueError):
        cgroup_path(data)


def test_kernel_evidence_parsers_preserve_exact_scope_and_counts():
    assert counters(b'max 2\noom 1\noom_kill 1\n') == {'max': 2, 'oom': 1, 'oom_kill': 1}
    assert cgroup_path(b'0::/system.slice/docker-abc.scope\n') == '/system.slice/docker-abc.scope'


class CgroupReader:
    """Open kernel files before pressure; never write cgroup control settings."""
    names = ('memory.max', 'memory.swap.max', 'memory.events', 'pids.max', 'pids.current', 'pids.events')

    def __init__(self, pid):
        with (Path('/proc') / str(pid) / 'cgroup').open('rb') as stream:
            path = cgroup_path(stream.read(4097))
        self.stack = ExitStack()
        try:
            root = self.stack.enter_context(_directory(Path('/sys/fs/cgroup') / path.lstrip('/')))
            self.descriptors = {}
            for name in self.names:
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root)
                self.stack.callback(os.close, descriptor)
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError('Cgroup observation is not a kernel regular file')
                self.descriptors[name] = descriptor
        except BaseException:
            self.stack.close()
            raise

    def read(self):
        values = {}
        for name, descriptor in self.descriptors.items():
            data = os.pread(descriptor, 4097, 0)
            if len(data) > 4096:
                raise ValueError('Kernel observation exceeds its bound')
            if name.endswith('.events'):
                values[name] = counters(data)
            elif not data.strip().isdigit():
                raise ValueError('Kernel resource bound is not finite')
            else:
                values[name] = int(data.strip())
        return values

    def close(self):
        self.stack.close()


class Observation:
    def __init__(self, monkeypatch, marker, tmp_path, *, resource=None, cleanup_fault=None):
        self.original = runtime._docker
        self.marker, self.resource, self.cleanup_fault = marker, resource, cleanup_fault
        self.stop, self.started = threading.Event(), threading.Event()
        self.thread = None
        self.errors, self.processes = [], {}
        self.baseline, self.latest = None, None
        self.peak = {'memory_max': 0, 'oom_kill': 0, 'pids_max': 0, 'pids_current': 0}
        self.identifier = self.name = self.image_id = self.base = self.base_identity = None
        self.attach = self.terminal = None
        self.create_count = self.start_count = self.fault_count = 0
        self.removed = self.absent_after_removal = False
        self.recovery = tmp_path / 'capacity-recovery-docker'
        self.recovery.mkdir(mode=0o700)
        (self.recovery / 'config.json').write_text('{}')
        monkeypatch.setattr(runtime, '_docker', self.docker)

    def command(self, arguments, configuration=None, limit=65536):
        return self.original(arguments, configuration or self.recovery,
                             runtime._Budget(None, 5, time.monotonic()), limit)

    def inspect_owned(self):
        code, output, _, forced = self.command(['inspect', self.identifier])
        if code != 0 or forced is not None:
            raise AssertionError('Cannot inspect the exact test-owned container')
        records = json.loads(output['stdout'])
        assert isinstance(records, list) and len(records) == 1
        record = records[0]
        assert record['Id'] == self.identifier and record['Name'] == '/' + self.name
        assert record['Config']['Labels']['ai.eidolon.execution'] == self.name
        assert record['Config']['Image'] == runtime.IMAGE
        if self.image_id is not None:
            assert record['Image'] == self.image_id
        else:
            assert re.fullmatch('sha256:[0-9a-f]{64}', record['Image'])
            self.image_id = record['Image']
        return record

    def is_absent(self):
        code, output, _, forced = self.command(['container', 'ls', '--all', '--no-trunc',
            '--filter', 'id=' + self.identifier, '--format', '{{.ID}}'], limit=4096)
        assert code == 0 and forced is None, 'Container absence is unconfirmed'
        rows = output['stdout'].decode('ascii', 'strict').splitlines()
        assert all(row == self.identifier for row in rows), 'Unexpected container in exact-ID query'
        return not rows

    def sample(self, reader):
        values = reader.read()
        if self.baseline is None:
            assert values['memory.max'] == 1024 * 1048576
            assert values['memory.swap.max'] == 0 and values['pids.max'] == 64
            self.baseline = values
        assert values['pids.current'] <= 64
        self.latest = values
        for key, value in {
            'memory_max': values['memory.events']['max'], 'oom_kill': values['memory.events']['oom_kill'],
            'pids_max': values['pids.events']['max'], 'pids_current': values['pids.current'],
        }.items():
            self.peak[key] = max(self.peak[key], value)

    def observe(self, configuration):
        reader = None
        next_top = 0
        deadline = time.monotonic() + WALL_SECONDS
        try:
            while not self.stop.is_set() and time.monotonic() < deadline:
                if time.monotonic() >= next_top:
                    code, output, _, forced = self.original(['top', self.identifier, '-eo', 'pid,uid,args'],
                        configuration, runtime._Budget(None, 2, time.monotonic()), limit=65536)
                    next_top = time.monotonic() + 0.1
                    if code == 0 and forced is None:
                        for line in output['stdout'].decode('utf-8', 'strict').splitlines():
                            row = line.strip().split(maxsplit=2)
                            if len(row) != 3 or not row[0].isdigit() or row[1] != '65534':
                                continue
                            if row[2] != self.marker and not re.fullmatch(
                                    re.escape(self.marker) + r'-child-[0-9]+ 120', row[2]):
                                continue
                            pid, identity = int(row[0]), process_identity(int(row[0]))
                            if identity is None or identity[0] != 65534:
                                continue
                            self.processes[pid] = identity
                            if row[2] == self.marker and not self.started.is_set():
                                if self.resource in {'memory', 'pids'}:
                                    reader = CgroupReader(pid)
                                    self.sample(reader)
                                self.started.set()
                if reader is not None:
                    self.sample(reader)
                self.stop.wait(0.025)
            if reader is not None:
                self.sample(reader)
        except OSError as error:
            # Cgroup teardown is expected after exit. Missing counters cannot
            # satisfy the pressure assertions: they require an observed delta.
            if error.errno not in {errno.ENOENT, errno.ENODEV} or self.baseline is None:
                self.errors.append(type(error).__name__ + ':' + str(error.errno))
        except Exception as error:
            self.errors.append(type(error).__name__)
        finally:
            if reader is not None:
                reader.close()

    def stop_observer(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
            assert not self.thread.is_alive(), 'Capacity observer did not stop'

    def docker(self, arguments, configuration, budget, limit=1048576):
        if arguments[0] == 'create':
            self.create_count += 1
            assert self.create_count == 1, 'No second execution is permitted in this test'
            self.name = arguments[arguments.index('--name') + 1]
            self.base = Path(configuration).parent
            info = self.base.stat()
            self.base_identity = info.st_dev, info.st_ino, info.st_uid
        if arguments[:2] == ['start', '--attach']:
            self.start_count += 1
            assert self.start_count == 1 and arguments[2] == self.identifier
            self.thread = threading.Thread(target=self.observe, args=(configuration,), daemon=True)
            self.thread.start()
        if arguments[:2] == ['rm', '--force'] and self.cleanup_fault and self.terminal is not None:
            assert arguments[2] == self.identifier and self.fault_count == 0
            self.fault_count += 1
            # The immutable ID was checked after attach; production cleanup
            # rechecks it before reaching this exact removal call as well.
            if self.cleanup_fault == 'lost_acknowledgment':
                removed = self.original(arguments, configuration, budget, limit)
                assert removed[0] == 0 and removed[3] is None
                self.removed = True
                self.absent_after_removal = self.is_absent()
            return 1, {'stdout': b'', 'stderr': b'injected cleanup uncertainty'}, {'stdout': 0, 'stderr': 28}, None
        answer = self.original(arguments, configuration, budget, limit)
        if arguments[0] == 'create' and answer[0] == 0 and answer[3] is None:
            self.identifier = answer[1]['stdout'].decode('ascii').strip()
            assert re.fullmatch('[0-9a-f]{64}', self.identifier)
        if arguments[:2] == ['start', '--attach']:
            self.attach = answer
            self.stop_observer()
            self.terminal = self.inspect_owned()['State']
        if arguments[:2] == ['rm', '--force'] and answer[0] == 0 and answer[3] is None:
            assert arguments[2] == self.identifier
            self.removed = True
            self.absent_after_removal = self.is_absent()
        return answer

    def verify_processes_gone(self):
        deadline = time.monotonic() + 5
        while any(process_identity(pid) == identity for pid, identity in self.processes.items()):
            assert time.monotonic() < deadline, 'Observed test workload survived removal'
            time.sleep(0.05)

    def finalize(self):
        self.stop_observer()
        if self.identifier is None:
            return
        if not self.is_absent():
            self.inspect_owned()
            code, _, _, forced = self.command(['rm', '--force', self.identifier])
            assert code == 0 and forced is None
            assert self.is_absent()
        self.verify_processes_gone()
        # Retained staging is removed only after exact test-container absence;
        # this independent test repair never changes the runtime receipt.
        if self.base is not None and self.base.exists():
            info = self.base.lstat()
            assert stat.S_ISDIR(info.st_mode) and not self.base.is_symlink()
            assert (info.st_dev, info.st_ino, info.st_uid) == self.base_identity
            assert info.st_uid == os.getuid() and self.base.name.startswith('eidolon-workspace-')
            runtime._cleanup_tree(self.base)


@pytest.mark.parametrize('field', ['id', 'name', 'image', 'label', 'reference'])
def test_test_finalizer_never_removes_a_different_container_or_staging(monkeypatch, tmp_path, field):
    identifier, name, image_id = 'a' * 64, 'eidolon-workspace-owned', 'sha256:' + 'b' * 64
    record = {'Id': identifier, 'Name': '/' + name, 'Image': image_id,
              'Config': {'Image': runtime.IMAGE, 'Labels': {'ai.eidolon.execution': name}}}
    changes = {
        'id': lambda: record.update(Id='c' * 64),
        'name': lambda: record.update(Name='/another'),
        'image': lambda: record.update(Image='sha256:' + 'c' * 64),
        'label': lambda: record['Config']['Labels'].update({'ai.eidolon.execution': 'another'}),
        'reference': lambda: record['Config'].update(Image='node:unreviewed'),
    }
    changes[field]()
    calls = []

    def fake_docker(arguments, *_args):
        calls.append(arguments)
        if arguments[0] == 'container':
            data = (identifier + '\n').encode()
        elif arguments[0] == 'inspect':
            data = json.dumps([record]).encode()
        else:
            pytest.fail('Mismatched ownership must not reach removal')
        return 0, {'stdout': data, 'stderr': b''}, {'stdout': len(data), 'stderr': 0}, None

    monkeypatch.setattr(runtime, '_docker', fake_docker)
    observation = Observation(monkeypatch, 'marker', tmp_path)
    observation.identifier, observation.name, observation.image_id = identifier, name, image_id
    observation.base = tmp_path / name
    observation.base.mkdir()
    preserved = observation.base / 'preserved'
    preserved.write_bytes(b'test-owned staging remains while ownership is uncertain')
    info = observation.base.stat()
    observation.base_identity = info.st_dev, info.st_ino, info.st_uid
    with pytest.raises(AssertionError):
        observation.finalize()
    assert preserved.read_bytes() == b'test-owned staging remains while ownership is uncertain'
    assert [call[0] for call in calls] == ['container', 'inspect']


@contextmanager
def run_probe(prepared, monkeypatch, tmp_path, body, *, resource=None, cleanup_fault=None):
    original, directory, manifest, profile = prepared
    profile = deepcopy(profile)
    profile['limits'].update(wallSeconds=WALL_SECONDS, scratchMiB=32, logBytes=LOG_BYTES)
    if resource in {'memory', 'pids', 'bytes', 'inodes'}:
        profile['limits']['memoryMiB'] = 1024
    marker = 'eid-cap-' + uuid.uuid4().hex[:12]
    probe = ("\nimport capacityFs from 'node:fs';\nimport capacityAssert from 'node:assert/strict';\n"
             f"process.title = {json.dumps(marker)};\n"
             "await new Promise((resolve) => setTimeout(resolve, 10000));\n"
             + body.replace('__CAPACITY_MARKER__', json.dumps(marker)) + '\n').encode()
    source = changed(original, 'apps/desktop/vitest.config.ts', probe)
    observation = Observation(monkeypatch, marker, tmp_path, resource=resource, cleanup_fault=cleanup_fault)
    started = time.monotonic()
    try:
        result = runtime.run_prepared_workspace_step(source, directory, manifest, manifest['toolchain'],
            profile, 'ui-tests', allow_local_docker=True, check_authorized=lambda: True)
        observation.stop_observer()
        report = diagnostic(result)
        assert observation.started.is_set(), (observation.errors, report)
        assert observation.errors == [], (observation.errors, report)
        assert observation.create_count == observation.start_count == 1
        assert time.monotonic() - started < WALL_SECONDS + 30, report
        assert result['snapshotSha256'] == source.snapshot_sha256 != original.snapshot_sha256
        assert observation.attach is not None
        code, output, totals, forced = observation.attach
        assert len(output['stdout']) <= CONTROL_BYTES and totals['stdout'] <= CONTROL_BYTES
        assert len(output['stderr']) <= LOG_BYTES and totals['stderr'] <= LOG_BYTES
        frames = attach_frames(output['stdout'], forced)
        assert frames and frames[0]['kind'] == 'isolation', report
        assert frames[0]['supervisorUid'] == 0 and frames[0]['workloadUid'] == 65534
        assert frames[0]['supervisorCapabilities'] == ['SETUID', 'SETGID']
        assert frames[0]['workloadCapabilities'] == []
        assert forced is None and result['isolation']['established'] is True, report
        if cleanup_fault is None:
            assert result['status'] == 'failed' and result['cleanupConfirmed'] is True, report
            assert 'outcome' not in result and code != 0
            assert observation.removed and observation.absent_after_removal
            observation.verify_processes_gone()
        yield result, observation, frames
    finally:
        observation.finalize()


@pytest.mark.linux_only
def test_actual_memory_limit_is_reached_after_observed_workspace_start(prepared, monkeypatch, tmp_path):
    body = """
const capacityBuffers = [];
for (let index = 0; index < 256; index++) {
  capacityBuffers.push(Buffer.alloc(8 * 1024 * 1024, 1));
  await new Promise((resolve) => setTimeout(resolve, 25));
}
throw new Error('Memory pressure did not stop the bounded allocation probe');
"""
    with run_probe(prepared, monkeypatch, tmp_path, body, resource='memory') as (_, observation, _):
        before = observation.baseline['memory.events']
        assert observation.peak['memory_max'] > before['max'], 'No host-observed memory limit event'
        assert (observation.peak['oom_kill'] > before['oom_kill']
                or observation.terminal['OOMKilled'] is True), 'No host-observed OOM termination'


@pytest.mark.linux_only
def test_actual_pid_limit_is_reached_without_losing_descendant_cleanup(prepared, monkeypatch, tmp_path):
    body = """
const capacityProcesses = await import('node:child_process');
let capacityError;
for (let index = 0; index < 80; index++) {
  const child = capacityProcesses.spawn('/usr/bin/sleep', ['120'], {
    argv0: __CAPACITY_MARKER__ + '-child-' + index, stdio: 'ignore'
  });
  const error = await new Promise((resolve) => {
    child.once('spawn', () => resolve(null)); child.once('error', resolve);
  });
  if (error) { capacityError = error; break; }
}
capacityAssert.equal(capacityError?.code, 'EAGAIN');
await new Promise((resolve) => setTimeout(resolve, 2000));
throw new Error('Expected bounded PID pressure reached');
"""
    with run_probe(prepared, monkeypatch, tmp_path, body, resource='pids') as (_, observation, _):
        assert observation.peak['pids_max'] > observation.baseline['pids.events']['max']
        assert observation.peak['pids_current'] <= 64
        assert len(observation.processes) > 1, 'No child independently observed before removal'


@pytest.mark.linux_only
@pytest.mark.parametrize('kind', ['bytes', 'inodes'])
def test_actual_scratch_capacity_is_bounded_with_explicit_workload_claims(prepared, monkeypatch, tmp_path, kind):
    actions = {
        'bytes': """
const descriptor = capacityFs.openSync('/tmp/capacity-bytes', 'wx');
const block = Buffer.alloc(1024 * 1024, 1);
try {
  for (let index = 0; index < 64; index++) capacityCount += capacityFs.writeSync(descriptor, block);
} catch (error) { capacityError = error; }
finally { capacityFs.closeSync(descriptor); }
""",
        'inodes': """
capacityFs.mkdirSync('/tmp/capacity-inodes');
try {
  for (let index = 0; index < 4097; index++) {
    capacityFs.writeFileSync('/tmp/capacity-inodes/' + index, '', { flag: 'wx' }); capacityCount++;
  }
} catch (error) { capacityError = error; }
""",
    }
    body = ("let capacityError; let capacityCount = 0;\n" + actions[kind]
            + "capacityAssert.equal(capacityError?.code, 'ENOSPC');\n"
            + "const capacityStat = capacityFs.statfsSync('/tmp');\n"
            + "process.stderr.write(" + json.dumps('\n' + CLAIM_PREFIX) + " + JSON.stringify({\n"
            + "  evidenceOrigin: 'workload-claim', kind: " + json.dumps(kind)
            + ", code: capacityError.code, count: capacityCount, freeBlocks: capacityStat.bfree,\n"
            + "  blockSize: capacityStat.bsize, freeInodes: capacityStat.ffree, totalInodes: capacityStat.files\n"
            + "}) + '\\n');\nthrow new Error('Expected bounded scratch pressure reached');")
    with run_probe(prepared, monkeypatch, tmp_path, body, resource=kind) as (_, observation, _):
        claims = [json.loads(line[len(CLAIM_PREFIX):]) for line in
                  observation.attach[1]['stderr'].decode().splitlines() if line.startswith(CLAIM_PREFIX)]
        assert len(claims) == 1
        claim = claims[0]
        assert claim['evidenceOrigin'] == 'workload-claim' and claim['kind'] == kind and claim['code'] == 'ENOSPC'
        if kind == 'bytes':
            assert 0 < claim['count'] <= 32 * 1048576 and claim['freeBlocks'] == 0
        else:
            assert 0 < claim['count'] <= 4096 and claim['freeInodes'] == 0 and claim['totalInodes'] <= 4096
            assert claim['freeBlocks'] * claim['blockSize'] > 16 * 1048576


@pytest.mark.linux_only
@pytest.mark.parametrize('fault', ['refused', 'lost_acknowledgment'])
def test_actual_unknown_cleanup_fails_closed_without_second_execution(prepared, monkeypatch, tmp_path, fault):
    with run_probe(prepared, monkeypatch, tmp_path, '', cleanup_fault=fault) as (result, observation, frames):
        report = diagnostic(result)
        assert observation.attach[0] == 0 and len(frames) == 2, report
        assert frames[1]['kind'] == 'result' and frames[1]['status'] == 'passed'
        assert frames[1]['evidenceOrigin'] == 'workload-claim'
        assert result['status'] == 'cleanup_failed' and result['cleanupConfirmed'] is False, report
        assert result['outcome']['evidenceOrigin'] == 'workload-claim'
        assert observation.fault_count == 1
        assert result['retainedStagingDirectory'] == str(observation.base) and observation.base.is_dir()
        assert observation.is_absent() is (fault == 'lost_acknowledgment')
        assert observation.create_count == observation.start_count == 1
    assert observation.create_count == observation.start_count == 1
    assert observation.is_absent() and not observation.base.exists()
    # Explicit test repair did not upgrade the uncertain receipt. Durable
    # restart/replay gating belongs to the future prepared-workspace store path.
    assert result['status'] == 'cleanup_failed' and result['cleanupConfirmed'] is False

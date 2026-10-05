"""Live security contracts. Success is never substituted by a mocked process.

Dedicated native Linux CI sets EIDOLON_REQUIRE_PROJECT_SANDBOX=1 so unavailable
kernel/bubblewrap isolation fails that lane. Restricted containers explicitly
exercise the unsupported path and skip only the native success contracts.
"""
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import threading
import time

import pytest

from eidolon_cli.organization_project_runner import (
    ProjectExecutionGrant, normalize_execution_grant, probe_project_runner,
    run_project_tests, snapshot_digest,
)


def snapshot(content, path='root0/test_project.py', revision=1):
    return [{'path': path, 'content': content, 'sha256': hashlib.sha256(content.encode()).hexdigest(),
             'revision': revision}]


GOOD_TEST = 'import unittest\nclass Example(unittest.TestCase):\n    def test_sum(self): self.assertEqual(1 + 2, 3)\n'


@pytest.fixture(scope='module')
def live_probe():
    return probe_project_runner()


@pytest.fixture
def isolated_linux(live_probe):
    if live_probe['status'] != 'passed':
        explanation = live_probe.get('reason', '') + ' ' + live_probe['stderr']
        if os.environ.get('EIDOLON_REQUIRE_PROJECT_SANDBOX') == '1':
            pytest.fail('Required native sandbox unavailable: ' + explanation)
        pytest.skip('Real OS sandbox is unavailable on this host: ' + explanation)
    return live_probe


@pytest.mark.parametrize('override', [
    {'recipe': 'shell'}, {'argv': ['/bin/sh', '-c', 'true']}, {'environment': {'HOME': '/root'}},
    {'root': '/tmp'}, {'test_directory': '../outside'}, {'test_directory': 'a//b'},
    {'pattern': '../../*.py'}, {'pattern': '-h.py'}, {'timeout_seconds': True},
    {'cpu_seconds': 0}, {'memory_mb': 100000}, {'scratch_mb': -1}, {'output_bytes': 0},
])
def test_grant_rejects_unbounded_or_arbitrary_execution(override):
    with pytest.raises(ValueError):
        normalize_execution_grant(override)


def test_snapshot_hash_binds_paths_bytes_and_revisions():
    files = snapshot(GOOD_TEST) + snapshot('answer = 42\n', 'root0/answer.py')
    before = snapshot_digest(files)
    assert before == snapshot_digest(list(reversed(files)))
    assert before != snapshot_digest([{**item, 'revision': item['revision'] + 1} for item in files])
    assert before != snapshot_digest(snapshot(GOOD_TEST, 'root0/test_other.py') + files[1:])
    bad = [{**files[0], 'content': GOOD_TEST + '# changed\n'}]
    with pytest.raises(ValueError, match='hash'):
        snapshot_digest(bad)


@pytest.mark.linux_only
def test_copied_trusted_loader_preserves_execute_but_not_write_permissions(tmp_path):
    from eidolon_cli.organization_project_runner import _RuntimeManifest, _copy_runtime_file
    # A temporary ELF-shaped fixture tests the copy contract without reading
    # implementation source or executing a host/project binary.
    loader = tmp_path / 'ld-linux-test.so.2'
    loader.write_bytes(b'\x7fELFfixture')
    loader.chmod(0o755)
    root = tmp_path / 'runtime'
    root.mkdir()
    manifest = _RuntimeManifest(root)
    copied = root / 'lib64' / loader.name
    _copy_runtime_file(loader, copied, manifest)
    assert copied.stat().st_mode & 0o777 == 0o555
    assert copied.read_bytes() == loader.read_bytes()
    assert manifest[0]['mode'] == 0o555
    library = tmp_path / 'libordinary.so'
    library.write_bytes(b'\x7fELFlibrary')
    library.chmod(0o644)
    _copy_runtime_file(library, root / library.name, manifest)
    assert (root / library.name).stat().st_mode & 0o777 == 0o444


@pytest.mark.parametrize('path', ['root0/../test.py', '/root0/test.py', 'root0//test.py',
                                  'root0/a\\test.py', 'root0/~test.py', 'root0/test\x00.py'])
def test_bad_snapshot_never_executes(path):
    result = run_project_tests(snapshot(GOOD_TEST, path), ProjectExecutionGrant())
    assert result['status'] == 'blocked'
    assert result['exitCode'] is None
    assert result['isolation']['established'] is False


def test_snapshot_conflicts_limits_and_root_are_rejected():
    cases = [snapshot(GOOD_TEST) * 2,
             snapshot('text', 'root0/a') + snapshot(GOOD_TEST, 'root0/a/test.py'),
             snapshot(GOOD_TEST, 'root1/test.py'),
             snapshot('x' * (524288 + 1)),
             [{**snapshot(GOOD_TEST)[0], 'revision': True}],
             [{**snapshot(GOOD_TEST)[0], 'sha256': '0' * 64}]]
    for files in cases:
        result = run_project_tests(files, ProjectExecutionGrant())
        assert result['status'] == 'blocked'
        assert result['exitCode'] is None


def test_precancelled_request_retains_exact_identity_without_running():
    cancel = threading.Event()
    cancel.set()
    files = snapshot(GOOD_TEST)
    result = run_project_tests(files, ProjectExecutionGrant(), cancel)
    assert result['status'] == 'cancelled'
    assert result['exitCode'] is None
    assert result['snapshotSha256'] == snapshot_digest(files)
    assert result['runtime'] is None


@pytest.mark.linux_only
def test_trusted_preparation_tools_have_output_and_time_bounds():
    from eidolon_cli.organization_project_runner import _Budget, _Deadline, _Unsupported, _host_program
    with pytest.raises(_Unsupported, match='output bound'):
        _host_program('/usr/bin/python3', ['-I', '-S', '-c', 'import os; os.write(1, b"x" * 2000000)'],
                      _Budget(None, 5, time.monotonic()))
    started = time.monotonic()
    with pytest.raises(_Deadline):
        _host_program('/usr/bin/python3', ['-I', '-S', '-c', 'import time; time.sleep(30)'],
                      _Budget(None, 1, started))
    assert time.monotonic() - started < 5


@pytest.mark.linux_only
def test_live_probe_is_honest_about_actual_os_isolation(live_probe):
    assert live_probe['status'] in {'passed', 'unsupported'}
    assert live_probe['startedAt'] <= live_probe['endedAt']
    assert live_probe['durationSeconds'] > 0
    if live_probe['status'] == 'passed':
        assert live_probe['isolation']['established']
        assert live_probe['testCount'] == 1
        assert live_probe['exitCode'] == 0
    else:
        assert not live_probe['isolation']['established']
        assert live_probe['testCount'] == 0
        assert live_probe['reason']
    json.dumps(live_probe, allow_nan=False)


@pytest.mark.macos_only
def test_macos_fails_closed_without_a_host_fallback():
    result = probe_project_runner()
    assert result['status'] == 'unsupported'
    assert 'macOS' in result['reason']
    assert result['runtime'] is None
    assert result['command'] == []


@pytest.mark.windows_only
def test_windows_fails_closed_without_a_host_fallback():
    result = probe_project_runner()
    assert result['status'] == 'unsupported'
    assert result['runtime'] is None
    assert result['command'] == []


@pytest.mark.linux_only
def test_real_unittest_uses_exact_snapshot_and_runtime(isolated_linux):
    files = snapshot('value = 42\n', 'root0/answer.py') + snapshot(
        'import unittest\nimport answer\nclass Example(unittest.TestCase):\n'
        '    def test_answer(self): self.assertEqual(answer.value, 42)\n')
    result = run_project_tests(files, ProjectExecutionGrant())
    assert result['status'] == 'passed', result
    assert result['snapshotSha256'] == snapshot_digest(files)
    assert result['files'] == [{k: item[k] for k in ('path', 'sha256', 'revision')}
                               for item in sorted(files, key=lambda item: item['path'])]
    assert result['runtime']['executableSha256'] == hashlib.sha256(Path('/usr/bin/python3').read_bytes()).hexdigest()
    assert len(result['runtime']['runtimeSha256']) == 64
    assert result['runtime']['stdlibOnly']
    assert result['command'][:5] == ['/usr/bin/python3', '-I', '-S', '-B', '/runner.py']
    assert result['testCount'] == 1
    assert result['sourceWritesPerformed'] is False
    assert 'OK' in result['stderr']
    assert result['isolation']['seccomp'] and result['isolation']['noNewPrivileges']


@pytest.mark.linux_only
def test_secrets_network_host_files_and_source_writes_are_inaccessible(isolated_linux, tmp_path, monkeypatch):
    secret = tmp_path / 'owner-secret'
    secret.write_text('not available to project code')
    hidden_write = tmp_path / 'hidden-write'
    monkeypatch.setenv('EIDOLON_RUNNER_TEST_SECRET', 'do-not-forward-this')
    content = f'''import os, pathlib, socket, unittest
class Isolation(unittest.TestCase):
    def test_no_environment_or_host_paths(self):
        self.assertNotIn('EIDOLON_RUNNER_TEST_SECRET', os.environ)
        self.assertNotIn('PYTHONPATH', os.environ)
        for path in [{str(secret)!r}, '/etc/passwd', '/etc/shadow', '/proc/1/root/etc/passwd',
                     '/home', '/root', '/run/docker.sock', '/var/run/docker.sock']:
            self.assertFalse(pathlib.Path(path).exists(), path)
        with self.assertRaises(OSError): pathlib.Path({str(hidden_write)!r}).write_text('escape')
    def test_no_network(self):
        for family in (socket.AF_INET, socket.AF_INET6, socket.AF_UNIX):
            with self.assertRaises(OSError): socket.socket(family, socket.SOCK_STREAM)
    def test_immutable_project_and_runtime(self):
        for path in ['/project/root0/test_project.py', '/usr/lib/overwrite', '/runner.py',
                     '/scratch/new-file', '/tmp/new-file']:
            with self.assertRaises(OSError): pathlib.Path(path).write_text('tamper')
        with self.assertRaises(OSError): os.symlink('/etc/passwd', '/scratch/link')
    def test_private_bounded_scratch(self):
        path = pathlib.Path('/scratch/work.dat')
        path.write_text('private test scratch')
        self.assertEqual(path.read_text(), 'private test scratch')
        with path.open('wb') as file:
            with self.assertRaises(OSError): file.truncate(2 * 1024 * 1024)
'''
    files = snapshot(content)
    result = run_project_tests(files, replace(ProjectExecutionGrant(), scratch_mb=1))
    assert result['status'] == 'passed', result
    assert result['testCount'] == 4
    assert secret.read_text() == 'not available to project code'
    assert not hidden_write.exists()
    assert files == snapshot(content)


@pytest.mark.linux_only
def test_no_fork_thread_exec_new_namespace_or_mount(isolated_linux):
    content = '''import ctypes, errno, os, subprocess, threading, unittest
class Processes(unittest.TestCase):
    def test_no_children(self):
        with self.assertRaises(OSError): os.fork()
        with self.assertRaises(OSError): subprocess.run(['/usr/bin/python3', '-c', 'print(1)'])
        with self.assertRaises(RuntimeError): threading.Thread(target=lambda: None).start()
    def test_no_exec(self):
        with self.assertRaises(OSError): os.execve('/usr/bin/python3', ['python3', '-c', 'print(1)'], {})
    def test_no_namespace_mount_or_ptrace(self):
        libc = ctypes.CDLL(None, use_errno=True)
        for action in [lambda: libc.unshare(0x10000000),
                       lambda: libc.mount(b'none', b'/scratch', b'tmpfs', 0, None),
                       lambda: libc.ptrace(0, 0, 0, 0)]:
            self.assertEqual(action(), -1)
            self.assertEqual(ctypes.get_errno(), errno.EPERM)
'''
    result = run_project_tests(snapshot(content), ProjectExecutionGrant())
    assert result['status'] == 'passed', result
    assert result['testCount'] == 3


@pytest.mark.linux_only
@pytest.mark.parametrize('content', [
    'import unittest\nclass Fail(unittest.TestCase):\n    def test_wrong(self): self.assertEqual(1, 2)\n',
    'value = 1\n',
    'import os\nos._exit(0)\n',
    'import os, sys\nos.write(int(sys.argv[1]), b"invalid-result\\n")\nos._exit(0)\n',
    'import os, sys\nos.write(int(sys.argv[1]), b"[" * 2000 + b"]" * 2000 + b"\\n")\nos._exit(0)\n',
    'import unittest\n@unittest.skip("skip")\nclass Skip(unittest.TestCase):\n    def test_skip(self): pass\n',
])
def test_failures_empty_suites_and_incomplete_results_do_not_pass(isolated_linux, content):
    result = run_project_tests(snapshot(content), ProjectExecutionGrant())
    assert result['status'] == 'failed', result
    assert result['isolation']['established']


@pytest.mark.linux_only
def test_output_is_bounded_and_wall_timeout_reaps_execution(isolated_linux):
    content = '''import ctypes, os, time, unittest
class Flood(unittest.TestCase):
    def test_flood(self):
        os.setsid()
        self.assertEqual(ctypes.CDLL(None).prctl(1, 0, 0, 0, 0), 0)
        os.write(1, b'detached and cleared parent-death signal\\n')
        os.write(1, b'a' * 100000)
        os.write(2, b'b' * 100000)
        time.sleep(60)
'''
    started = time.monotonic()
    result = run_project_tests(snapshot(content), replace(ProjectExecutionGrant(), timeout_seconds=3, output_bytes=1024))
    assert result['status'] == 'timed_out', result
    assert result['isolation']['established']
    assert result['stdout'].startswith('detached and cleared parent-death signal')
    assert result['stdoutTruncated'] and result['stderrTruncated']
    assert len(result['stdout'].encode()) <= 1024
    assert len(result['stderr'].encode()) <= 1024
    assert time.monotonic() - started < 15
    assert result['exitCode'] is not None


@pytest.mark.linux_only
def test_live_cancellation_finishes_without_descendants(isolated_linux):
    content = 'import time, unittest\nclass Slow(unittest.TestCase):\n    def test_wait(self): time.sleep(60)\n'
    cancel = threading.Event()
    timer = threading.Timer(2, cancel.set)
    timer.start()
    started = time.monotonic()
    try:
        result = run_project_tests(snapshot(content), replace(ProjectExecutionGrant(), timeout_seconds=15), cancel)
    finally:
        timer.cancel()
        timer.join()
    assert result['status'] == 'cancelled', result
    assert time.monotonic() - started < 15
    # The probe already proved the real sandbox is available; when preparation
    # finishes before cancellation this also verifies the live kill/reap path.
    if result['isolation']['established']:
        assert result['exitCode'] is not None


@pytest.mark.linux_only
def test_address_space_and_cpu_are_enforced(isolated_linux):
    memory = '''import unittest
class Memory(unittest.TestCase):
    def test_bound(self):
        with self.assertRaises(MemoryError): bytearray(512 * 1024 * 1024)
'''
    result = run_project_tests(snapshot(memory), replace(ProjectExecutionGrant(), memory_mb=64))
    assert result['status'] == 'passed', result
    cpu = 'import unittest\nclass CPU(unittest.TestCase):\n    def test_spin(self):\n        while True: pass\n'
    result = run_project_tests(snapshot(cpu), replace(ProjectExecutionGrant(), cpu_seconds=1, timeout_seconds=10))
    assert result['status'] == 'failed', result
    assert result['exitCode'] != 0
    assert result['durationSeconds'] < 10

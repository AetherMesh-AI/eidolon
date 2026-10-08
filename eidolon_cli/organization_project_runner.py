"""Fail-closed, OS-isolated execution of exact managed Python test snapshots.

The initial recipe is intentionally small: system Python's stdlib, one process,
no network, no dependencies, no shell and one bounded writable scratch file.
Temporary directories alone, environment filtering and command allowlists are
not isolation. This runner requires real Linux user/mount/PID/network namespaces,
read-only copied runtime/source mounts, and an irreversible seccomp filter.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time

from eidolon_cli.organization_project_validation import canonical, digest

_MAX_FILES = 64
_MAX_BYTES = 524288
_ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
_NAMESPACES = ('user', 'mnt', 'pid', 'net', 'ipc', 'uts')
_LIMITATIONS = (
    'Only the copied, explicitly selected files are tested. Fixed Python unittest or pytest recipe; '
    'no project dependency installation, subprocesses, threads, shell, network or file creation. '
    'Scratch is one writable /scratch/work.dat file. A passed test process is not '
    'independent proof of correctness: test code can falsify assertions or test reporting. '
    'This does not publish, deploy or modify the original source project.'
)


@dataclass(frozen=True)
class ProjectExecutionGrant:
    recipe: str = 'python_unittest'
    root: str = 'root0'
    test_directory: str = '.'
    pattern: str = 'test*.py'
    timeout_seconds: int = 10
    cpu_seconds: int = 5
    memory_mb: int = 256
    scratch_mb: int = 16
    output_bytes: int = 16384


def _relative_path(value, *, dot=False):
    if dot and value == '.':
        return True
    return (isinstance(value, str) and 0 < len(value) <= 1024 and '\\' not in value
            and not any(ord(char) < 32 or ord(char) == 127 for char in value)
            and all(part not in ('', '.', '..') and not part.startswith('~') for part in value.split('/')))


def normalize_execution_grant(value):
    """Validate config without executing programs or probing host capabilities."""
    if isinstance(value, ProjectExecutionGrant):
        value = asdict(value)
    if not isinstance(value, dict) or set(value) - set(ProjectExecutionGrant.__dataclass_fields__):
        raise ValueError('Execution grant must select only the supported fixed test recipe and limits')
    values = {**asdict(ProjectExecutionGrant()), **value}
    if values['recipe'] not in ('python_unittest', 'python_pytest'):
        raise ValueError('Only the fixed python_unittest and python_pytest execution recipes are supported')
    if not isinstance(values['root'], str) or not re.fullmatch(r'root(?:0|[1-9][0-9]*)', values['root']):
        raise ValueError('Execution root must be a canonical read-root alias')
    if not _relative_path(values['test_directory'], dot=True):
        raise ValueError('Execution test_directory must be a bounded canonical relative path')
    if (not isinstance(values['pattern'], str) or not re.fullmatch(r'[A-Za-z0-9_*?.-]{1,100}\.py', values['pattern'])
            or values['pattern'].startswith('-')):
        raise ValueError('Execution pattern must be a bounded Python filename pattern')
    limits = {'timeout_seconds': (1, 120), 'cpu_seconds': (1, 60), 'memory_mb': (64, 1024),
              'scratch_mb': (1, 64), 'output_bytes': (1024, 65536)}
    for key, (low, high) in limits.items():
        if type(values[key]) is not int or not low <= values[key] <= high:
            raise ValueError(f'Execution {key} must be an integer between {low} and {high}')
    return ProjectExecutionGrant(**values)


def _snapshot(files):
    if not isinstance(files, list) or not 1 <= len(files) <= _MAX_FILES:
        raise ValueError('Execution snapshot requires 1–64 selected files')
    result, seen, total = [], set(), 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {'path', 'content', 'sha256', 'revision'}:
            raise ValueError('Execution files require exact path, content, sha256 and revision fields')
        path = item['path']
        if (not _relative_path(path) or '/' not in path
                or not re.fullmatch(r'root(?:0|[1-9][0-9]*)', path.split('/')[0]) or path in seen):
            raise ValueError('Execution snapshot requires unique canonical read-root alias paths')
        if not isinstance(item['content'], str):
            raise ValueError('Execution content must be UTF-8 text')
        if len(item['content']) > _MAX_BYTES:
            raise ValueError('Execution snapshot exceeds the 524288-byte combined limit')
        try:
            encoded = item['content'].encode('utf-8')
        except UnicodeError:
            raise ValueError('Execution content must be UTF-8 text') from None
        if (not isinstance(item['sha256'], str) or hashlib.sha256(encoded).hexdigest() != item['sha256']
                or type(item['revision']) is not int or not 0 <= item['revision'] <= 2**63 - 1):
            raise ValueError('Execution snapshot hash or revision does not match its exact bytes')
        seen.add(path)
        total += len(encoded)
        if total > _MAX_BYTES:
            raise ValueError('Execution snapshot exceeds the 524288-byte combined limit')
        result.append(dict(item))
    for path in seen:
        if any('/'.join(path.split('/')[:n]) in seen for n in range(1, len(path.split('/')))):
            raise ValueError('Execution snapshot has conflicting file and directory paths')
    return sorted(result, key=lambda item: item['path'])


def snapshot_digest(files):
    """Hash the exact UTF-8 content identity and revision of the complete snapshot."""
    return digest([{key: item[key] for key in ('path', 'sha256', 'revision')} for item in _snapshot(files)])


class _Unsupported(Exception):
    pass


class _Cancelled(Exception):
    pass


class _Deadline(Exception):
    pass


class _Budget:
    def __init__(self, cancel, timeout, started):
        self.cancel = cancel
        self.deadline = started + timeout

    def check(self):
        if self.cancel is not None and self.cancel.is_set():
            raise _Cancelled()
        if time.monotonic() >= self.deadline:
            raise _Deadline()


def _host_program(path, args, budget):
    budget.check()
    try:
        process = subprocess.Popen([path, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=_ENV, cwd='/', stdin=subprocess.DEVNULL, start_new_session=True)
        code, output, _, forced = _read_process(process, {}, 1024 * 1024, budget, stop_on_output=True)
    except OSError as error:
        raise _Unsupported('Required trusted runtime tool is unavailable: ' + str(error)[:500]) from None
    if forced == 'cancelled':
        raise _Cancelled()
    if forced == 'timed_out':
        raise _Deadline()
    if forced is not None or code:
        raise _Unsupported('Required trusted runtime tool failed or exceeded its output bound: '
                           + output['stderr'].decode('utf-8', 'replace')[:1000])
    return output['stdout'].decode('utf-8', 'strict')


def _platform_tools(budget):
    if sys.platform != 'linux':
        raise _Unsupported('Isolated project execution currently requires Linux bubblewrap and libseccomp. '
                           'macOS sandbox-exec is deprecated and is not an equivalent supported backend; no host fallback was run.')
    bwrap, python, ldd = '/usr/bin/bwrap', '/usr/bin/python3', '/usr/bin/ldd'
    for path in (bwrap, python, ldd):
        if not Path(path).is_file() or not os.access(path, os.X_OK):
            raise _Unsupported('Required trusted system runtime is missing: ' + path)
    help_text = _host_program(bwrap, ['--help'], budget)
    for required in ('--disable-userns', '--assert-userns-disabled', '--remount-ro'):
        if required not in help_text:
            raise _Unsupported('Installed bubblewrap does not support required isolation option ' + required)
    return bwrap, python, ldd


def _copy_runtime_file(source, destination, manifest, *, executable=False):
    # Copy bytes, never bind mutable host runtime directories or follow project
    # symlinks. Only the trusted system executable, stdlib and ELF dependencies
    # selected below are eligible; home, /etc and site-packages are never mounted.
    resolved = source.resolve(strict=True)
    fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 32 * 1024 * 1024:
            raise _Unsupported('Runtime entry is not a bounded regular file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            content = stream.read(32 * 1024 * 1024 + 1)
        if len(content) > 32 * 1024 * 1024:
            raise _Unsupported('Runtime entry exceeds its byte limit')
    finally:
        os.close(fd)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    # The kernel opens the ELF interpreter as an executable too. Removing the
    # system dynamic loader's execute bits prevents Python itself from starting.
    # Preserve execute permission only for already-executable trusted binaries;
    # project files use a separate, unconditionally read-only snapshot path.
    executable = executable or (source.suffix != '.py' and bool(info.st_mode & 0o111))
    mode = 0o555 if executable else 0o444
    destination.chmod(mode)
    manifest.append({'path': '/' + str(destination.relative_to(manifest.root)),
                     'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content), 'mode': mode})


class _RuntimeManifest(list):
    def __init__(self, root):
        super().__init__()
        self.root = root


def _prepare_runtime(base, python, ldd, budget, recipe="python_unittest"):
    info = json.loads(_host_program(python, ['-I', '-S', '-c',
        'import json,sys,sysconfig; print(json.dumps({"version":sys.version,"versionInfo":list(sys.version_info[:3]),"stdlib":sysconfig.get_path("stdlib"),'
        '"multiarch":sysconfig.get_config_var("MULTIARCH")}))'], budget))
    stdlib = Path(info['stdlib'])
    if not stdlib.is_relative_to('/usr/lib') or not re.fullmatch(r'python3\.[0-9]+', stdlib.name):
        raise _Unsupported('Only a system Python stdlib under /usr/lib is supported')
    multiarch = info['multiarch']
    if not isinstance(multiarch, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', multiarch):
        raise _Unsupported('Cannot identify trusted system library architecture')
    seccomp = Path('/usr/lib') / multiarch / 'libseccomp.so.2'
    if not seccomp.is_file():
        raise _Unsupported('Required system libseccomp.so.2 is unavailable')
    runtime = base / 'runtime'
    runtime.mkdir()
    manifest = _RuntimeManifest(runtime)
    files = [Path(python), seccomp]
    excluded = {'site-packages', 'dist-packages', '__pycache__', 'test', 'tests', 'ensurepip', 'idlelib', 'tkinter'}
    for directory, subdirs, names in os.walk(stdlib, followlinks=False):
        budget.check()
        subdirs[:] = sorted(name for name in subdirs if name not in excluded and not Path(directory, name).is_symlink())
        for name in sorted(names):
            source = Path(directory, name)
            if (source.suffix in ('.py', '.so') and not source.is_symlink()
                    and not name.startswith(('_test', '_tkinter'))):
                files.append(source)
    if len(files) > 3000:
        raise _Unsupported('System Python runtime exceeds supported file count')
    elf_files = [str(path) for path in files if path.suffix != '.py']
    dependencies = _host_program(ldd, elf_files, budget)
    if 'not found' in dependencies:
        raise _Unsupported('System Python runtime has missing ELF dependencies')
    for line in dependencies.splitlines():
        match = re.search(r'(?:=>\s+|^\s*)(/[^\s]+)\s+\(0x[0-9a-f]+\)', line)
        if match:
            path = Path(match[1])
            if not (path.is_relative_to('/lib') or path.is_relative_to('/lib64') or path.is_relative_to('/usr/lib')):
                raise _Unsupported('Runtime dependency is outside trusted system library directories')
            files.append(path)
    for source in sorted(set(files)):
        budget.check()
        _copy_runtime_file(source, runtime / str(source).lstrip('/'), manifest, executable=source == Path(python))
        if sum(item['bytes'] for item in manifest) > 128 * 1024 * 1024:
            raise _Unsupported('System Python runtime exceeds supported aggregate byte limit')
    dependencies = []
    if recipe == 'python_pytest':
        from eidolon_cli.organization_project_pytest import PytestBundleUnavailable, pytest_bundle
        try:
            package_files, dependencies = pytest_bundle(info['versionInfo'], budget)
        except PytestBundleUnavailable as error:
            raise _Unsupported(str(error)) from None
        for package_source, relative in package_files:
            budget.check()
            _copy_runtime_file(package_source, runtime / 'runner-packages' / relative, manifest)
        (runtime / 'pytest.ini').write_text('[pytest]\n', encoding='ascii')
        (runtime / 'pytest.ini').chmod(0o444)
        manifest.append({'path': '/pytest.ini', 'sha256': hashlib.sha256(b'[pytest]\n').hexdigest(),
                         'bytes': len(b'[pytest]\n'), 'mode': 0o444})
    helper = Path(__file__).with_name('organization_project_bootstrap.py')
    _copy_runtime_file(helper, runtime / 'runner.py', manifest)
    # Read-only root bind mounts cannot create missing mountpoints afterward.
    for name in ('project', 'scratch', 'dev', 'proc'):
        (runtime / name).mkdir()
    for name in ('null', 'zero', 'random', 'urandom'):
        (runtime / 'dev' / name).touch()
    (runtime / 'scratch' / 'work.dat').touch()
    if sum(item['bytes'] for item in manifest) > 128 * 1024 * 1024:
        raise _Unsupported('System Python runtime exceeds supported aggregate byte limit')
    identity = {'executable': python, 'executableSha256': next(item['sha256'] for item in manifest if item['path'] == python),
                'version': info['version'], 'versionInfo': info['versionInfo'], 'runtimeSha256': digest(sorted(manifest, key=lambda item: item['path'])),
                'fileCount': len(manifest), 'bytes': sum(item['bytes'] for item in manifest),
                'stdlibOnly': recipe == 'python_unittest', 'dependencies': dependencies}
    if dependencies:
        identity['dependencyFilesSha256'] = digest(sorted(
            (item for item in manifest if item['path'].startswith('/runner-packages/')),
            key=lambda item: item['path']))
    return runtime, str(seccomp), identity


def _sandbox_command(bwrap, runtime, source, scratch, grant, status_fd, seccomp):
    argv = [bwrap, '--unshare-user', '--unshare-ipc', '--unshare-pid', '--unshare-net', '--unshare-uts',
            '--disable-userns', '--assert-userns-disabled', '--die-with-parent', '--cap-drop', 'ALL',
            '--uid', '65534', '--gid', '65534', '--hostname', 'eidolon-project', '--clearenv',
            '--ro-bind', str(runtime), '/', '--ro-bind', str(source), '/project',
            '--dir', '/scratch', '--bind', str(scratch), '/scratch/work.dat',
            '--dir', '/dev', '--proc', '/proc', '--remount-ro', '/proc']
    for name in ('null', 'zero', 'random', 'urandom'):
        argv.extend(['--ro-bind', '/dev/' + name, '/dev/' + name])
    environment = {'PATH': '/usr/bin', 'HOME': '/scratch', 'TMPDIR': '/scratch', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
    for key, value in environment.items():
        argv.extend(['--setenv', key, value])
    command = ['/usr/bin/python3', '-I', '-S', '-B', '/runner.py', str(status_fd), seccomp, canonical(asdict(grant))]
    argv.extend(['--remount-ro', '/', '--chdir', '/project/' + grant.root, '--', *command])
    return argv, command, environment


def _kill(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        # poll()/exit race: a process that has already exited needs no signal.
        return


def _read_process(process, extra_streams, output_limit, budget, *, stop_on_output=False):
    streams = {'stdout': process.stdout, 'stderr': process.stderr, **extra_streams}
    output = {name: bytearray() for name in streams}
    totals = {name: 0 for name in streams}
    forced = None
    stopped_at = None
    with selectors.DefaultSelector() as selector:
        for name, stream in streams.items():
            os.set_blocking(stream if isinstance(stream, int) else stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        try:
            while selector.get_map() or process.poll() is None:
                if forced is None:
                    if budget.cancel is not None and budget.cancel.is_set():
                        forced = 'cancelled'
                    elif time.monotonic() >= budget.deadline:
                        forced = 'timed_out'
                    if forced:
                        _kill(process)
                        stopped_at = time.monotonic()
                for key, _ in selector.select(timeout=0.05):
                    data = os.read(key.fd, 8192)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    name = key.data
                    limit = 8192 if name == 'status' else output_limit
                    totals[name] += len(data)
                    output[name].extend(data[:max(0, limit - len(output[name]))])
                    if stop_on_output and totals[name] > limit and forced is None:
                        forced = 'output_limit'
                        _kill(process)
                        stopped_at = time.monotonic()
                if process.poll() is not None and not selector.get_map():
                    break
                if stopped_at is not None and time.monotonic() - stopped_at > 5:
                    # Never wait indefinitely on a leaked inherited writer. A
                    # complete native sandbox must close its pipes when PID 1
                    # dies; failure to do so is not a successful execution.
                    _kill(process)
                    forced = 'cleanup_failed'
                    break
            return process.wait(timeout=5), output, totals, forced
        finally:
            if process.poll() is None:
                _kill(process)
                process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()


def _capture(argv, status_pipe, grant, budget):
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=_ENV, cwd='/', close_fds=True, pass_fds=(status_pipe[1],), start_new_session=True)
    writer, status_pipe[1] = status_pipe[1], None
    os.close(writer)
    return _read_process(process, {'status': status_pipe[0]}, grant.output_bytes, budget)


def _decode_report(line):
    try:
        value = json.loads(line)
        return value if isinstance(value, dict) else {}
    except (ValueError, UnicodeError, RecursionError):
        return {}


def _apply_process_result(receipt, exit_code, output, totals, forced, parent_namespaces):
    receipt.update(exitCode=exit_code, stdout=output['stdout'].decode('utf-8', 'replace'),
                   stderr=output['stderr'].decode('utf-8', 'replace'),
                   stdoutTruncated=totals['stdout'] > len(output['stdout']),
                   stderrTruncated=totals['stderr'] > len(output['stderr']),
                   stdoutBytes=totals['stdout'], stderrBytes=totals['stderr'])
    reports = output['status'].splitlines()
    # Project output cannot erase the already-observed startup isolation facts
    # by appending malformed or deeply nested completion messages.
    started = _decode_report(reports[0]) if reports else {}
    namespaces = started.get('namespaces', {})
    isolated = (started.get('phase') == 'isolated' and isinstance(namespaces, dict)
                and set(namespaces) == set(_NAMESPACES)
                and all(isinstance(namespaces[name], str) and namespaces[name] != parent_namespaces[name]
                        for name in _NAMESPACES)
                and started.get('uid') == started.get('gid') == 65534
                and all(started.get(name) is True for name in ('seccompInstalled', 'namespaceCreationDenied',
                        'noNewPrivileges', 'sourceReadOnly', 'runtimeReadOnly'))
                and started.get('capabilities') == 'none')
    receipt['isolation']['established'] = isolated
    if isolated:
        receipt['isolation'].update(namespaces=namespaces, uid=65534, gid=65534,
                                    limits=started.get('limits'), seccomp=True, seccompInstalled=True,
                                    namespaceCreationDenied=True, noNewPrivileges=True)
    if forced:
        reasons = {'cancelled': 'Execution was cancelled.',
                   'timed_out': 'Execution exceeded its wall-clock deadline.',
                   'cleanup_failed': 'Execution cleanup did not close its process streams within the bounded deadline.'}
        receipt.update(status='unsupported' if forced == 'cleanup_failed' else forced, reason=reasons[forced])
        return
    if not isolated:
        receipt.update(status='unsupported', reason='The required OS isolation could not be established; no successful test execution was accepted.')
        return
    final = _decode_report(reports[-1]) if len(reports) == 2 and totals['status'] <= 8192 else {}
    count, skipped = final.get('testCount'), final.get('skippedCount')
    valid = (final.get('phase') == 'completed' and type(count) is int and 0 <= count <= 1000000
             and type(skipped) is int and 0 <= skipped <= count and type(final.get('passed')) is bool)
    if valid:
        receipt.update(testCount=count, skippedCount=skipped)
    passed = valid and count > skipped and final['passed'] and exit_code == 0
    receipt.update(status='passed' if passed else 'failed',
                   reason='Nonempty Python test suite completed successfully.' if passed else
                   'Test execution failed, ran no unskipped tests, exceeded a resource limit, or did not return a complete result.')


def run_project_tests(files, grant, cancel=None):
    """Execute only a fixed recipe and return an auditable bounded terminal receipt.

The caller owns owner authorization, exact grant/file selection, policy revocation,
and persistence. Neither project content nor a model supplies the command line.
Unsupported hosts and failed setup never fall back to host Python execution.
"""
    started, clock = datetime.now(timezone.utc).isoformat(), time.monotonic()
    receipt = {'runner': 'eidolon.isolated-python-unittest', 'runnerVersion': 1,
               'status': 'blocked', 'exitCode': None, 'snapshotSha256': None, 'files': [], 'command': [],
               'runtime': None, 'startedAt': started, 'endedAt': None, 'durationSeconds': 0,
               'stdout': '', 'stderr': '', 'stdoutTruncated': False, 'stderrTruncated': False,
               'stdoutBytes': 0, 'stderrBytes': 0, 'testCount': 0, 'skippedCount': 0,
               'isolation': {'backend': 'linux-bubblewrap-seccomp', 'established': False},
               'sourceWritesPerformed': False, 'limitations': _LIMITATIONS}
    try:
        grant = normalize_execution_grant(grant)
        receipt['runner'] = 'eidolon.isolated-' + grant.recipe.replace('_', '-')
        files = _snapshot(files)
        if any(item['path'].split('/')[0] != grant.root for item in files):
            raise ValueError('Every selected execution file must belong to the granted root')
        receipt['files'] = [{key: item[key] for key in ('path', 'sha256', 'revision')} for item in files]
        receipt['snapshotSha256'] = snapshot_digest(files)
        receipt['grant'] = asdict(grant)
        budget = _Budget(cancel, grant.timeout_seconds, clock)
        budget.check()
        bwrap, python, ldd = _platform_tools(budget)
        with tempfile.TemporaryDirectory(prefix='eidolon-project-') as directory:
            base = Path(directory)
            runtime, seccomp, identity = _prepare_runtime(base, python, ldd, budget, grant.recipe)
            receipt['runtime'] = identity
            source = base / 'source'
            source.mkdir()
            for item in files:
                budget.check()
                target = source / item['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(item['content'].encode('utf-8'))
                target.chmod(0o444)
            scratch = base / 'work.dat'
            scratch.touch(mode=0o666)
            scratch.chmod(0o666)
            status_pipe = list(os.pipe())
            try:
                argv, command, environment = _sandbox_command(bwrap, runtime, source, scratch, grant, status_pipe[1], seccomp)
                receipt.update(command=command, environment=environment)
                receipt['isolation'].update(sourceReadOnly=True, runtimeReadOnly=True, network='none',
                    scratchMode='single_file', scratchPath='/scratch/work.dat', scratchBytes=grant.scratch_mb * 1024 * 1024,
                    processLimit=1, childProcessesAllowed=False, hostPathsMounted=False,
                    requestedNamespaces=list(_NAMESPACES), capabilities='none')
                parent_namespaces = {name: os.readlink('/proc/self/ns/' + name) for name in _NAMESPACES}
                budget.check()
                exit_code, output, totals, forced = _capture(argv, status_pipe, grant, budget)
                _apply_process_result(receipt, exit_code, output, totals, forced, parent_namespaces)
            finally:
                for descriptor in status_pipe:
                    if descriptor is not None:
                        os.close(descriptor)
    except _Cancelled:
        receipt.update(status='cancelled', reason='Execution was cancelled before project code started.')
    except _Deadline:
        receipt.update(status='timed_out', reason='Execution exceeded its overall wall-clock deadline during preparation.')
    except (ValueError, TypeError, UnicodeError) as error:
        receipt.update(status='blocked', reason=str(error)[:2000])
    except (_Unsupported, OSError, subprocess.SubprocessError) as error:
        receipt.update(status='unsupported', reason=str(error)[:2000])
    finally:
        receipt.update(endedAt=datetime.now(timezone.utc).isoformat(), durationSeconds=round(time.monotonic() - clock, 6))
    return receipt


def probe_project_runner():
    """Run a real harmless test through the same isolation path; never simulate success."""
    content = 'import unittest\nclass Probe(unittest.TestCase):\n    def test_runtime(self): self.assertEqual(2 + 2, 4)\n'
    return run_project_tests([{'path': 'root0/test_probe.py', 'content': content,
                               'sha256': hashlib.sha256(content.encode()).hexdigest(), 'revision': 0}],
                             ProjectExecutionGrant())

"""Trusted single-process Python test entry point, copied into the isolated runtime.

This file must remain stdlib-only. It executes only after bubblewrap has created
the namespaces and read-only mounts. No project import precedes the hard limits.
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
import resource
import sys
import unittest


def _restrict(seccomp_path, config):
    limits = {
        resource.RLIMIT_CPU: config['cpu_seconds'],
        resource.RLIMIT_AS: config['memory_mb'] * 1024 * 1024,
        resource.RLIMIT_FSIZE: config['scratch_mb'] * 1024 * 1024,
        resource.RLIMIT_NOFILE: 64,
        resource.RLIMIT_NPROC: 1,
        resource.RLIMIT_CORE: 0,
        resource.RLIMIT_MEMLOCK: 0,
        resource.RLIMIT_MSGQUEUE: 0,
        resource.RLIMIT_SIGPENDING: 32,
    }
    for kind, value in limits.items():
        resource.setrlimit(kind, (value, value))
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0):  # PR_SET_NO_NEW_PRIVS, irreversible.
        raise OSError(ctypes.get_errno(), 'Cannot enable no_new_privs')
    library = ctypes.CDLL(seccomp_path, use_errno=True)
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    context = library.seccomp_init(0x7fff0000)  # SCMP_ACT_ALLOW
    if not context:
        raise OSError('Cannot initialize seccomp')
    try:
        # Names are resolved by libseccomp for the native architecture. Its
        # architecture guard also prevents switching to a compat syscall ABI.
        for name in (
            'clone', 'clone3', 'fork', 'vfork', 'execve', 'execveat', 'unshare', 'setns',
            'mount', 'umount2', 'pivot_root', 'chroot', 'ptrace', 'process_vm_readv',
            'process_vm_writev', 'bpf', 'perf_event_open', 'userfaultfd', 'io_uring_setup',
            'keyctl', 'add_key', 'request_key', 'open_by_handle_at', 'reboot',
            'kexec_load', 'kexec_file_load', 'swapon', 'swapoff', 'memfd_create', 'memfd_secret',
            'socket', 'socketpair', 'shmget', 'shmat', 'shmdt', 'shmctl', 'semget',
            'semop', 'semtimedop', 'semctl', 'msgget', 'msgsnd', 'msgrcv', 'msgctl',
            'mq_open', 'pidfd_open', 'pidfd_getfd', 'pidfd_send_signal', 'flock',
        ):
            number = library.seccomp_syscall_resolve_name(name.encode('ascii'))
            if number != -1 and library.seccomp_rule_add(context, 0x50000 | errno.EPERM, number, 0):
                raise OSError('Cannot install required syscall restriction: ' + name)
        if library.seccomp_load(context):
            raise OSError('Cannot enable seccomp')
    finally:
        library.seccomp_release(context)



def _pytest_suite(root, config):
    # Import trusted framework before adding the project; no host environment,
    # plugin entry points, sitecustomize, .pth files or project ini is loaded.
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    sys.path.insert(0, '/runner-packages')
    import pytest

    class Results:
        count = 0
        skipped = 0

        def pytest_runtest_logreport(self, report):
            if report.when == 'call' or report.skipped:
                self.count += 1
                self.skipped += int(report.skipped)

    result = Results()
    sys.path.insert(1, root)
    code = pytest.main([
        '-c', '/pytest.ini', '--rootdir', root, '--confcutdir', root,
        '--assert=plain', '--capture=sys', '-v', '-p', 'no:cacheprovider', '-p', 'no:logging',
        '-o', 'python_files=' + config['pattern'], '-o', 'pythonpath=',
        '--', os.path.join(root, config['test_directory']),
    ], plugins=[result])
    return result.count, result.skipped, code == 0 and result.count > result.skipped


def main():
    status_fd = int(sys.argv[1])
    seccomp_path = sys.argv[2]
    config = json.loads(sys.argv[3])

    def report(value):
        os.write(status_fd, (json.dumps(value, separators=(',', ':')) + '\n').encode('utf-8'))

    try:
        _restrict(seccomp_path, config)
        with open('/proc/self/status', encoding='ascii') as file:
            status = dict(line.split(':', 1) for line in file if ':' in line)
        if (any(int(status[name].strip(), 16) != 0 for name in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'))
                or status['NoNewPrivs'].strip() != '1' or status['Seccomp'].strip() != '2'
                or not os.statvfs('/').f_flag & os.ST_RDONLY
                or not os.statvfs('/project').f_flag & os.ST_RDONLY):
            raise OSError('Required capability, seccomp or immutable mount state was not established')
        # This first report is emitted before project imports; the parent also
        # verifies namespace identities against its own namespace descriptors.
        report({'phase': 'isolated', 'namespaces': {
            kind: os.readlink('/proc/self/ns/' + kind) for kind in ('user', 'mnt', 'pid', 'net', 'ipc', 'uts')},
            'uid': os.getuid(), 'gid': os.getgid(),
            'seccompInstalled': True, 'namespaceCreationDenied': True,
            'noNewPrivileges': True, 'capabilities': 'none', 'sourceReadOnly': True, 'runtimeReadOnly': True,
            'limits': {'cpuSeconds': config['cpu_seconds'], 'memoryBytes': config['memory_mb'] * 1024 * 1024,
                       'scratchBytes': config['scratch_mb'] * 1024 * 1024, 'openFiles': 64, 'processes': 1,
                       'lockedMemoryBytes': 0, 'messageQueueBytes': 0, 'pendingSignals': 32}})
    except (OSError, ValueError, KeyError) as error:
        report({'phase': 'unsupported', 'reason': str(error)[:1000]})
        return 125
    root = '/project/' + config['root']
    try:
        if config['recipe'] == 'python_pytest':
            count, skipped, passed = _pytest_suite(root, config)
            report({'phase': 'completed', 'testCount': count, 'skippedCount': skipped, 'passed': passed})
            return 0 if passed else 1
        sys.path.insert(0, root)
        suite = unittest.TestLoader().discover(
            start_dir=os.path.join(root, config['test_directory']), pattern=config['pattern'], top_level_dir=root)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        count = result.testsRun
        passed = count > 0 and result.wasSuccessful() and count > len(result.skipped)
        report({'phase': 'completed', 'testCount': count, 'skippedCount': len(result.skipped),
                'passed': passed})
        return 0 if passed else 1
    except BaseException as error:
        # Includes sys.exit() from discovery. os._exit(), crashes and signals are
        # detected by the parent as missing completion, never as a passed suite.
        print(type(error).__name__ + ': ' + str(error)[:2000], file=sys.stderr)
        report({'phase': 'completed', 'testCount': 0, 'skippedCount': 0, 'passed': False})
        return 1


if __name__ == '__main__':
    sys.exit(main())

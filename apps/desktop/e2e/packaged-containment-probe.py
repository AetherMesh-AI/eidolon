"""Synthetic-only Seatbelt probes; never open a real credential or send external data."""
import sys
sys.stderr.write('containment-stage:python-entry\n')
sys.stderr.flush()


def trace(label):
    sys.stderr.write('containment-stage:' + label + '\n')
    sys.stderr.flush()


trace('before-import-ctypes')
import ctypes
trace('after-import-ctypes')
import errno
import json
import hashlib
import os
from pathlib import Path
trace('before-import-socket')
import socket
trace('after-import-socket')
import subprocess
trace('after-imports')


def observed(label, action):
    trace('before-' + label)
    value = action()
    trace('after-' + label)
    return value


def denied(action):
    try:
        action()
    except OSError as error:
        return {"denied": error.errno in (errno.EPERM, errno.EACCES), "errno": error.errno}
    return {"denied": False, "errno": 0}


def tcp(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.settimeout(3)
        stream.connect((host, port))


def udp(family, host):
    # connect checks destination permission; no send, sendto, DNS or payload.
    with socket.socket(family, socket.SOCK_DGRAM) as stream:
        stream.connect((host, 53))


def mach_policy():
    # WebKit's SandboxSPI.h defines GLOBAL_NAME=2 and XPC_SERVICE_NAME=12.
    # Query policy only: never contact a credential service or open a Keychain.
    trace('before-load-sandbox-library')
    library = ctypes.CDLL('/usr/lib/libsandbox.dylib', use_errno=True)
    trace('after-load-sandbox-library')
    check = library.sandbox_check
    check.restype = ctypes.c_int
    check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    names = [
        'com.apple.securityd', 'com.apple.securityd.xpc', 'com.apple.securityd.system',
        'com.apple.SecurityServer', 'com.apple.trustd', 'com.apple.trustd.agent',
        'com.apple.authd', 'com.apple.cfnetwork.AuthBrokerAgent',
        'com.apple.coreservices.launchservicesd', 'com.apple.coreservices.appleevents',
    ]
    rows = []
    for name in names:
        for kind in (2, 12):
            trace('before-policy-filter-' + str(kind))
            result = check(os.getpid(), b'mach-lookup', kind, ctypes.c_char_p(name.encode()))
            trace('after-policy-filter-' + str(kind))
            rows.append({'name': name, 'filter': kind, 'result': result, 'denied': result > 0})
    return rows


def run(config):
    observed('loopback', lambda: tcp('127.0.0.1', config['loopbackPort']))
    assert observed('allowed-canary', lambda: Path(config['allowedCanary']).read_text()) == 'synthetic-allowed\n'
    return {
        'pid': os.getpid(), 'ppid': os.getppid(),
        'loopback': True, 'allowedCanary': True,
        'tcpNonloopback': observed('tcpNonloopback', lambda: denied(lambda: tcp(config['nonloopback'], config['nonloopbackPort']))),
        'udp4NoPayload': observed('udp4NoPayload', lambda: denied(lambda: udp(socket.AF_INET, '192.0.2.1'))),
        'udp6NoPayload': observed('udp6NoPayload', lambda: denied(lambda: udp(socket.AF_INET6, '2001:db8::1'))),
        'credentialCanary': observed('credentialCanary', lambda: denied(lambda: Path(config['deniedCanary']).read_bytes())),
        'symlinkCanary': observed('symlinkCanary', lambda: denied(lambda: Path(config['symlinkCanary']).read_bytes())),
        'keychainCanary': observed('keychainCanary', lambda: denied(lambda: Path(config['keychainCanary']).read_bytes())),
        'dataVolumeCanary': observed('dataVolumeCanary', lambda: denied(lambda: Path(config['dataVolumeCanary']).read_bytes())),
        'machPolicy': observed('mach-policy', mach_policy),
        'homeMatches': os.environ['HOME'] == config['home'],
        'nonceSha256': hashlib.sha256(os.environ['EIDOLON_CONTAINMENT_NONCE'].encode()).hexdigest(),
        'environmentNames': sorted(os.environ),
    }


def main():
    trace('before-config')
    config = json.loads(Path(sys.argv[1]).read_text())
    trace('after-config')
    if len(sys.argv) > 2 and sys.argv[2] == '--inherited':
        print(json.dumps(run(config)))
        return
    direct = run(config)
    # A true fork/exec child, with no sandbox-exec around this second invocation.
    trace('before-inherited-child')
    try:
        child = subprocess.run([sys.executable, '-I', __file__, sys.argv[1], '--inherited'],
                               check=True, text=True, capture_output=True, timeout=15)
    except subprocess.CalledProcessError as error:
        sys.stderr.write(error.stderr or '')
        sys.stderr.flush()
        raise
    trace('after-inherited-child')
    sys.stderr.write(child.stderr)
    sys.stderr.flush()
    print(json.dumps({'direct': direct, 'inherited': json.loads(child.stdout)}))


if __name__ == '__main__':
    main()

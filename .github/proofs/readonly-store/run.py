"""Bounded diagnostic runner. Source and output roots are explicit inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


def parse():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--store-sha256', required=True)
    parser.add_argument('--expected-head')
    parser.add_argument('--expected-tree')
    parser.add_argument('--suite', choices=('all', 'portable', 'admission', 'permissions', 'identity'), default='all')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    return parser.parse_args()


def git(root, expression):
    return subprocess.check_output(['git', '-C', str(root), 'rev-parse', expression],
                                   text=True, timeout=10).strip()


def source_receipt(args, root):
    module = root / 'eidolon_cli' / 'organization_store.py'
    digest = hashlib.sha256(module.read_bytes()).hexdigest()
    if digest != args.store_sha256:
        raise RuntimeError('Candidate OrganizationStore SHA-256 mismatch')
    if os.environ.get('GITHUB_ACTIONS') == 'true' and not (args.expected_head and args.expected_tree):
        raise RuntimeError('Hosted proof requires explicit candidate head and tree')
    if bool(args.expected_head) != bool(args.expected_tree):
        raise RuntimeError('Supply candidate head and tree together')
    result = {'storeSha256': digest, 'root': str(root), 'head': None, 'tree': None}
    if args.expected_head:
        result.update(head=git(root, 'HEAD'), tree=git(root, 'HEAD^{tree}'))
        if result['head'] != args.expected_head or result['tree'] != args.expected_tree:
            raise RuntimeError('Candidate commit/tree mismatch')
        subprocess.run(['git', '-C', str(root), 'diff', '--exit-code'], check=True, timeout=10)
        subprocess.run(['git', '-C', str(root), 'diff', '--cached', '--exit-code'], check=True, timeout=10)
        if git(root, '--show-prefix'):
            raise RuntimeError('Candidate source must be checkout root')
    return result


def child(args):
    source = Path(args.source_root).resolve(strict=True)
    output = Path(args.output).resolve(strict=True)
    harness = Path(__file__).resolve().parent
    sys.path[:0] = [str(harness), str(source)]
    os.environ.update(EIDOLON_PROOF_SOURCE=str(source), EIDOLON_PROOF_OUTPUT=str(output),
                      EIDOLON_PROOF_STORE_SHA256=args.store_sha256,
                      HERMES_DISABLE_LAZY_INSTALLS='1', AWS_EC2_METADATA_DISABLED='true',
                      PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONDONTWRITEBYTECODE='1')
    os.environ.pop('PYTEST_ADDOPTS', None)
    before = source_receipt(args, source)
    (output / 'source-before.json').write_text(json.dumps(before, indent=2) + '\n')
    network = []
    def audit(event, unused):
        if event in {'socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname',
                     'socket.gethostbyaddr', 'socket.sendto'}:
            network.append(event)
            raise RuntimeError('Native proof blocks network')
    sys.addaudithook(audit)
    code = 1
    try:
        from native_sqlite import runtime, candidate_identity
        identity = {**runtime().receipt, 'candidate': candidate_identity()}
        (output / 'runtime.json').write_text(json.dumps(identity, indent=2) + '\n')
        if args.suite == 'identity':
            code = 0
        else:
            import pytest
            from verify_evidence import required
            expected_cases = required(args.suite, sys.platform)
            class Evidence:
                deselected = []
                reports = []
                def pytest_deselected(self, items):
                    self.deselected.extend(item.nodeid for item in items)
                def pytest_collection_finish(self, session):
                    actual = {'selected': sorted(item.nodeid for item in session.items),
                              'deselected': sorted(self.deselected)}
                    (output / 'collection.json').write_text(json.dumps(actual, indent=2) + '\n')
                    if actual != expected_cases:
                        raise RuntimeError('Collected test IDs differ from frozen manifest')
                def pytest_runtest_logreport(self, report):
                    self.reports.append({'nodeid': report.nodeid, 'when': report.when,
                                         'outcome': report.outcome})
            evidence = Evidence()
            files = []
            if args.suite in {'all', 'portable'}:
                files.append(str(harness / 'test_native_vfs.py'))
            if args.suite in {'all', 'permissions'}:
                files.append(str(harness / 'test_native_permissions.py'))
            if args.suite in {'all', 'portable', 'admission'}:
                files.append(str(harness / 'test_admission_probe.py'))
            arguments = ['-c', str(harness / 'pytest.ini'), '-q', '-vv', '-x', '-W', 'ignore::pytest.PytestUnknownMarkWarning',
                         '--tb=short', '-p', 'no:cacheprovider', '-o', 'junit_family=legacy',
                         '--junitxml=' + str(output / 'junit.xml'), *files]
            if args.suite == 'portable' or (os.name == 'nt' and args.suite == 'all'):
                arguments += ['-k', 'not production_admission']
            if os.name == 'nt' and args.suite == 'admission':
                raise RuntimeError('Windows subscription admission is intentionally unsupported')
            code = int(pytest.main(arguments, plugins=[evidence]))
            (output / 'reports.json').write_text(json.dumps(evidence.reports, indent=2) + '\n')
            cases = list(ET.parse(output / 'junit.xml').iter('testcase'))
            expected = {'portable': 25, 'admission': 16, 'permissions': 2,
                        'all': 27 if os.name == 'nt' else 34}[args.suite]
            names = [(case.get('classname'), case.get('name')) for case in cases]
            gate = {'expected': expected, 'observed': len(cases), 'unique': len(set(names)),
                    'skips': sum(case.find('skipped') is not None for case in cases),
                    'failures': sum(case.find('failure') is not None for case in cases),
                    'errors': sum(case.find('error') is not None for case in cases)}
            (output / 'case-gate.json').write_text(json.dumps(gate, indent=2) + '\n')
            if code == 0 and (len(cases) != expected or len(set(names)) != expected or
                              gate['skips'] or gate['failures'] or gate['errors']):
                raise RuntimeError('Required proof cases missing, duplicated, or not passed')
            import ctypes as C
            from vfs_probe import KEEPALIVE, KEEPALIVE_LIMIT
            from native_sqlite import P
            native = runtime()
            cleanup = {'instances': len(KEEPALIVE),
                       'defaultRestored': C.cast(native.lib.sqlite3_vfs_find(None), P).value ==
                                          C.cast(native.original, P).value,
                       'allRestored': all(item.restored for item in KEEPALIVE),
                       'remainingFiles': sum(len(item.files) for item in KEEPALIVE),
                       'pauseFailures': [failure for item in KEEPALIVE for failure in item.failures],
                       'withinRetentionBounds': len(KEEPALIVE) <= KEEPALIVE_LIMIT and
                                                all(len(item.retained_bundles) <= 512 for item in KEEPALIVE)}
            (output / 'vfs-cleanup.json').write_text(json.dumps(cleanup, indent=2) + '\n')
    finally:
        (output / 'network.json').write_text(json.dumps({'attempts': network, 'exit': code}) + '\n')
        after = source_receipt(args, source)
        (output / 'source-after.json').write_text(json.dumps(after, indent=2) + '\n')
        if after != before:
            raise RuntimeError('Candidate source changed during proof')
    if network:
        raise RuntimeError('Network activity occurred during proof')
    if code == 0:
        from verify_evidence import verify
        verify(output, args.suite, sys.platform, require_process=False)
    return code


def main():
    args = parse()
    if args.child:
        return child(args)
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise RuntimeError('Evidence output must be empty; never overwrite a proof')
    command = [sys.executable, '-B', str(Path(__file__).resolve()), *sys.argv[1:], '--child']
    try:
        with (output / 'test.log').open('w', encoding='utf-8') as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=60)
        (output / 'process.json').write_text(json.dumps({'capSeconds': 60,
            'exitCode': completed.returncode, 'timedOut': False}) + '\n')
        if completed.returncode == 0:
            from verify_evidence import verify
            result = verify(output, args.suite, sys.platform)
            (output / 'verified.json').write_text(json.dumps(result, indent=2) + '\n')
        return completed.returncode
    except subprocess.TimeoutExpired:
        (output / 'deadline.json').write_text('{"processCapSeconds":60,"expired":true}\n')
        (output / 'process.json').write_text('{"capSeconds":60,"exitCode":124,"timedOut":true}\n')
        return 124


if __name__ == '__main__':
    raise SystemExit(main())

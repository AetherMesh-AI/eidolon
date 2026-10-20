"""Strict standalone gate for this frozen temporary harness's evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)


def required(suite, platform):
    require(platform in {'linux', 'darwin', 'win32'}, 'Unsupported expected host')
    key = ('all-windows' if platform == 'win32' else 'all-posix') if suite == 'all' else suite
    result = read(Path(__file__).with_name('required-cases.json'))
    require(result['version'] == 1 and key in result, 'Unknown required-case manifest')
    if platform == 'win32':
        require(suite != 'admission', 'Windows admission is intentionally unsupported')
    return result[key]


def verify(output, suite, platform, *, require_process=True):
    output = Path(output)
    expected = required(suite, platform)
    identity = read(output / 'runtime.json')
    require(identity['platform'] == platform, 'Wrong native host')
    require(identity['vfs'] == ('win32' if platform == 'win32' else 'unix'), 'Wrong VFS')
    require(identity['vfsVersion'] == 3 and identity['pointerBytes'] == 8, 'Unsupported native ABI')
    require(identity['sqliteThreadsafe'] != 0 and identity['pythonThreadsafety'] > 0,
            'Unsafe threaded runtime')
    witness = identity['sameInstanceWitness']
    require(witness['pythonOpenObserved'] is True and witness['defaultUnchanged'] is True and
            witness['restored'] is True, 'Missing or unrestored Python/native instance witness')
    require(len(witness['mainOpens']) == 1 and witness['mainOpens'][0]['result'] == 0,
            'Wrong same-instance open witness')
    for section in ('library', 'candidate'):
        value = identity[section]
        require(len(value['sha256']) == 64 and value['bytes'] > 0 and value['path'],
                'Missing image/source byte identity')
    before, after = read(output / 'source-before.json'), read(output / 'source-after.json')
    require(before == after and before['storeSha256'] == identity['candidate']['sha256'],
            'Candidate source receipt changed')
    require(read(output / 'network.json') == {'attempts': [], 'exit': 0}, 'Network or test failure')
    if require_process:
        require(read(output / 'process.json') == {'capSeconds': 60, 'exitCode': 0, 'timedOut': False},
                'Proof subprocess did not exit successfully within its bound')
    if suite == 'identity':
        return {'suite': suite, 'platform': platform, 'cases': 0, 'verified': True}

    collection = read(output / 'collection.json')
    require(collection == expected, 'Collected/deselected test IDs differ from frozen manifest')
    cases = list(ET.parse(output / 'junit.xml').iter('testcase'))
    ids = [case.get('classname', '').replace('.', '/') + '.py::' + case.get('name', '')
           for case in cases]
    require(sorted(ids) == expected['selected'] and len(set(ids)) == len(ids),
            'Executed JUnit IDs differ from frozen collection')
    require(all(not any(case.find(tag) is not None for tag in ('failure', 'error', 'skipped'))
                for case in cases), 'A required case failed, errored, or skipped')
    reports = read(output / 'reports.json')
    phases = sorted((report['nodeid'], report['when'], report['outcome']) for report in reports)
    wanted = sorted((nodeid, phase, 'passed') for nodeid in expected['selected']
                    for phase in ('setup', 'call', 'teardown'))
    require(phases == wanted, 'Case did not run exactly once with successful setup/call/teardown')
    gate = read(output / 'case-gate.json')
    count = len(expected['selected'])
    require(gate == {'expected': count, 'observed': count, 'unique': count,
                     'skips': 0, 'failures': 0, 'errors': 0}, 'Case-count gate mismatch')
    cleanup = read(output / 'vfs-cleanup.json')
    instances = 0 if suite == 'permissions' else (16 if suite == 'admission' else
                 (24 if suite == 'all' and platform != 'win32' else 17))
    require(cleanup['instances'] == instances and cleanup['defaultRestored'] is True and
            cleanup['allRestored'] is True and cleanup['remainingFiles'] == 0 and
            cleanup['pauseFailures'] == [] and cleanup['withinRetentionBounds'] is True,
            'VFS callback/connection cleanup is incomplete')

    if suite in {'all', 'portable'}:
        comparison = read(output / 'store-close-comparison.json')
        require(comparison['scope'] == 'store_level_native_sqlite_not_subscription_eligibility',
                'Wrong Windows/native store proof scope')
        positive, negative = comparison['results']
        require(positive['readonly'] is False and positive['positiveExclusiveObserved'] is True and
                positive['readError'] == 5 and positive['readSucceeded'] is False,
                'Readonly proof lacks observed writable positive contention')
        require(positive['events'] == [{'phase': 'inside_connection_close', 'lock': 'exclusive', 'result': 0}],
                'Writable native lock was not actually acquired')
        require(positive['openFlags'] == {'requestedReadonly': False, 'requestedReadwrite': True,
                                         'returnedReadonly': False, 'returnedFlagsPresent': True,
                                         'methodVersion': 3}, 'Writable positive MAIN_DB flags missing')
        require(negative['readonly'] is True and negative['positiveExclusiveObserved'] is False and
                negative['readError'] is None and negative['readSucceeded'] is True and
                negative['mutationErrors'] == [8], 'Readonly comparison failed')
        require(negative['events'] == [{'phase': 'inside_connection_close', 'lock': 'exclusive', 'result': 3850}],
                'Readonly native refusal was not observed')
        require(negative['openFlags'] == {'requestedReadonly': True, 'requestedReadwrite': False,
                                         'returnedReadonly': True, 'returnedFlagsPresent': True,
                                         'methodVersion': 3}, 'Readonly native MAIN_DB flags missing')
        require(all(row['fileIdentity']['sameFile'] is True and row['fileIdentity']['inode'] > 0
                    for row in (positive, negative)), 'Native ledger file identity unverified')
        require(positive['identity']['candidate'] == identity['candidate'] and
                negative['identity']['candidate'] == identity['candidate'] and
                positive['identity']['library'] == identity['library'] and
                negative['identity']['library'] == identity['library'], 'Comparison runtime/source changed')

    if suite in {'all', 'permissions'}:
        permission_cases = [case for case in cases if case.get('classname') == 'test_native_permissions']
        require(len(permission_cases) == 2, 'Missing native permission controls')
        for case in permission_cases:
            props = list(case.iter('property'))
            names = [prop.get('name') for prop in props]
            require(len(names) == len(set(names)), 'Duplicate native permission property')
            props = {prop.get('name'): prop.get('value') for prop in props}
            require(props['native_permission_platform'] == platform, 'Permission evidence from wrong host')
            restore = json.loads(props['native_permission_restoration'])
            denials = json.loads(props['native_permission_denials'])
            require(restore and denials and all(row['exact'] is True and row['before'] == row['after']
                                               for row in restore), 'Permission restoration unverified')
            kind = 'windows-owner-group-dacl-descriptor' if platform == 'win32' else 'posix-mode'
            require(all(row['kind'] == kind for row in restore), 'Wrong native permission mechanism')

    if suite == 'admission' or (suite == 'all' and platform != 'win32'):
        refused = {'uncoordinated_close', 'coordinated_stale_owner', 'readonly_stale_owner'}
        modes = ['uncoordinated_close', 'coordinated_close', 'coordinated_stale_owner',
                 'ordinary_open_reader', 'released_close', 'readonly_close', 'readonly_stale_owner']
        for mode in modes:
            value = read(output / (mode + '.json'))
            require(value['mode'] == mode and value['policyUnchanged'] is True and
                    value['sameKeyReplayAddsSend'] is False and value['callsCharged'] == 1 and
                    value['dispatched'] == 1 and value['probeFailures'] == [],
                    'Admission debt/policy/replay invariant failed: ' + mode)
            require(value['guardResults'] == [mode not in refused] and
                    value['generationStatus'] == ('uncertain' if mode in refused else 'completed') and
                    value['sendBytesCalls'] == (0 if mode in refused else 2), 'Wrong admission outcome: ' + mode)
            # Gate the native mechanism independently of JUnit and the store-level
            # comparison. A successful admission result is not lock evidence.
            readonly = mode.startswith('readonly_')
            mechanism = {
                'locks': [{'phase': 'inside_connection_close', 'lock': 'exclusive',
                           'result': 3850 if readonly else 0}],
                'readErrors': ([{'line': 290, 'class': 'OperationalError',
                                 'code': 5, 'name': 'SQLITE_BUSY'}]
                               if mode == 'uncoordinated_close' else []),
                # This is the acquired-lock count when the guard returned, not
                # a claim that released/coordinated closes still hold a lock.
                'closeLocksAtGuard': [0 if readonly or mode == 'ordinary_open_reader' else 1],
                'readonlyEvidence': ({'queryOnlyFalse': True, 'dmlError': 'SQLITE_READONLY',
                                      'requestedReadonly': True, 'requestedReadwrite': False,
                                      'returnedReadonly': True, 'returnedFlagsPresent': True,
                                      'methodVersion': 3} if readonly else {}),
                'readonlyRefusalPauses': 1 if readonly else 0,
                'coordinatorWaits': 1 if mode.startswith('coordinated_') else 0,
                'connections': 1,
            }
            for field, expected_value in mechanism.items():
                # JSON equality also keeps booleans distinct from integer counts.
                require(json.dumps(value[field], sort_keys=True, allow_nan=False) ==
                        json.dumps(expected_value, sort_keys=True, allow_nan=False),
                        'Admission mechanism field differs: ' + mode + '.' + field)
            require(value['identity']['candidate'] == identity['candidate'] and
                    value['identity']['library'] == identity['library'], 'Admission runtime/source changed')
    return {'suite': suite, 'platform': platform, 'cases': count, 'verified': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', '--evidence', dest='output', required=True)
    parser.add_argument('--suite', required=True)
    parser.add_argument('--platform', default=sys.platform, choices=('linux', 'darwin', 'win32'))
    parser.add_argument('--source-root')
    parser.add_argument('--store-sha256')
    parser.add_argument('--expected-head')
    parser.add_argument('--expected-tree')
    args = parser.parse_args()
    result = verify(args.output, args.suite, args.platform)
    external = (args.source_root, args.store_sha256, args.expected_head, args.expected_tree)
    if any(external):
        require(all(external), 'Supply all external source expectations together')
        root = Path(args.source_root).resolve(strict=True)
        before = read(Path(args.output) / 'source-before.json')
        require(before == {'root': str(root), 'storeSha256': args.store_sha256,
                           'head': args.expected_head, 'tree': args.expected_tree},
                'Evidence does not match external candidate expectations')
        require(hashlib.sha256((root / 'eidolon_cli/organization_store.py').read_bytes()).hexdigest()
                == args.store_sha256, 'Current candidate module changed')
        for expression, expected in [('HEAD', args.expected_head), ('HEAD^{tree}', args.expected_tree)]:
            actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', expression],
                                             text=True, timeout=10).strip()
            require(actual == expected, 'Current candidate commit/tree changed')
    print(json.dumps(result, sort_keys=True))

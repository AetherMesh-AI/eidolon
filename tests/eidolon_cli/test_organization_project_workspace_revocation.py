"""Bounded test-only live-process observation; no project code is executed."""
import json

import pytest

from eidolon_cli import organization_project_workspace_node as runtime


def exact_revocation_process(data, marker, read_identity):
    if len(data) > 65536:
        raise ValueError('Revocation process listing exceeds its bound')
    matches = []
    for line in data.decode('utf-8', 'strict').splitlines():
        row = line.strip().split(maxsplit=2)
        if (len(row) != 3 or not row[0].isdigit() or row[1] != '65534'
                or row[2] != marker or not 0 < int(row[0]) < 2 ** 31):
            continue
        pid = int(row[0])
        identity = read_identity(pid)
        if (isinstance(identity, tuple) and len(identity) == 2 and identity[0] == 65534
                and type(identity[1]) is int and identity[1] > 0):
            matches.append((pid, identity))
    if len(matches) > 1:
        raise ValueError('Revocation process identity is ambiguous')
    return matches[0] if matches else None


def revocation_diagnostic(result, evidence):
    def text(value, budget):
        if not isinstance(value, str):
            return None
        value = runtime._safe_log(value.encode('utf-8', 'replace'))
        while len(encoded := json.dumps(value)) > budget:
            value = value[:len(value) * budget // len(encoded)]
        return value

    def number(value):
        return value if type(value) is int and -(2 ** 63) < value < 2 ** 63 else None

    report = {
        'status': text(result.get('status'), 64), 'reason': text(result.get('reason'), 512),
        'cleanupConfirmed': result.get('cleanupConfirmed') is True,
        'exitCode': number(result.get('exitCode')),
        'isolationEstablished': result.get('isolation', {}).get('established') is True,
        'phaseLogEvidenceOrigin': 'workload-claim', 'stderr': text(result.get('stderr'), 2000),
        'processEvidenceOrigin': 'host-process-table', 'topAttempts': number(evidence['topAttempts']),
        'lastTopCode': number(evidence['lastTopCode']), 'lastTopForced': text(evidence['lastTopForced'], 64),
        'lastTopOutput': text(evidence['lastTopOutput'].decode('utf-8', 'replace'), 2000),
        'observerErrors': [text(error, 128) for error in evidence['errors'][:4]],
        'observedPid': number(evidence['pid']),
        'observedUid': number(evidence['identity'][0]) if evidence['identity'] else None,
        'observedStartTime': number(evidence['identity'][1]) if evidence['identity'] else None,
        'callbackSawRevocation': evidence['callbackSawRevocation'],
    }
    if evidence['attach'] is not None:
        code, _, totals, forced = evidence['attach']
        report['attach'] = {'exitCode': number(code), 'forced': text(forced, 64),
                            'stdoutBytes': number(totals['stdout']), 'stderrBytes': number(totals['stderr'])}
    return json.dumps(report, sort_keys=True)


def test_revocation_requires_exact_host_pid_uid_title_and_live_start_identity():
    marker = 'eidolon-revoke-exact'
    reads = []

    def identity(pid):
        reads.append(pid)
        return 65534, 12345

    assert exact_revocation_process(b'PID UID COMMAND\n123 65534 eidolon-revoke-exact\n', marker, identity) == (
        123, (65534, 12345))
    assert reads == [123]
    for record in (b'', b'123 0 eidolon-revoke-exact\n', b'123 65534 eidolon-revoke-exact-extra\n',
                   b'123 65534 node eidolon-revoke-exact\n', b'0 65534 eidolon-revoke-exact\n',
                   b'EIDOLON_REVOCATION_BODY_STARTED eidolon-revoke-exact\n'):
        assert exact_revocation_process(record, marker, identity) is None
    for stale in (None, (0, 12345), (65534, 0), (65534, True)):
        assert exact_revocation_process(b'123 65534 eidolon-revoke-exact\n', marker, lambda _pid: stale) is None
    with pytest.raises(ValueError, match='ambiguous'):
        exact_revocation_process(b'123 65534 eidolon-revoke-exact\n124 65534 eidolon-revoke-exact\n', marker, identity)
    with pytest.raises(ValueError, match='bound'):
        exact_revocation_process(b'x' * 65537, marker, identity)


def test_revocation_diagnostics_are_bounded_redacted_and_do_not_upgrade_phase_claims(monkeypatch):
    monkeypatch.setattr(runtime, '_safe_log', lambda data: data.decode().replace('secret-value', '[REDACTED]'))
    long = 'secret-value ' + '\0\U0001f642' * 10000
    result = {'status': 'cancelled', 'reason': long, 'stderr': long, 'source': 'do-not-include',
              'cleanupConfirmed': True, 'isolation': {'established': False}, 'exitCode': -9}
    evidence = {'topAttempts': 4, 'lastTopCode': 0, 'lastTopForced': None, 'lastTopOutput': long.encode(),
                'errors': [], 'pid': None, 'identity': None, 'callbackSawRevocation': False,
                'attach': (-9, {'stdout': b'do-not-include'}, {'stdout': 200, 'stderr': 300}, 'cancelled')}
    rendered = revocation_diagnostic(result, evidence)
    assert len(rendered.encode()) <= 8192 and 'secret-value' not in rendered and 'do-not-include' not in rendered
    value = json.loads(rendered)
    assert value['phaseLogEvidenceOrigin'] == 'workload-claim' and value['processEvidenceOrigin'] == 'host-process-table'
    assert value['stderr'].startswith('[REDACTED]') and value['lastTopOutput'].startswith('[REDACTED]')
    assert value['observedPid'] is None and value['callbackSawRevocation'] is False
    assert value['attach'] == {'exitCode': -9, 'forced': 'cancelled', 'stdoutBytes': 200, 'stderrBytes': 300}

"""Hosted native gates only; workload reports do not prove assertion honesty."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import threading
import time
import uuid

import pytest

from eidolon_cli import organization_project_workspace_node as runtime
from tests.eidolon_cli.test_organization_project_workspace_native import (
    UI_TEST, changed, prepared as prepared,
)

CONTROL_BYTES = 1048576


def attach_frames(data, forced):
    """A killed attach may truncate only its final unterminated frame."""
    lines = data.split(b'\n')
    if lines[-1]:
        if forced not in {'cancelled', 'timed_out'}:
            raise ValueError('Unterminated runtime control frame')
        lines.pop()
    else:
        lines.pop()
    return [json.loads(line) for line in lines]


@pytest.mark.parametrize('forced', ['cancelled', 'timed_out'])
def test_forced_attach_preserves_complete_proof_and_ignores_only_partial_tail(forced):
    assert attach_frames(b'{"kind":"isolation"}\n{"kind":', forced) == [{'kind': 'isolation'}]
    with pytest.raises(ValueError):
        attach_frames(b'{"kind":"isolation"}\nmalformed\n{"kind":', forced)


@pytest.mark.parametrize('forced', [None, 'output_limit'])
def test_other_attach_results_do_not_tolerate_partial_control_frames(forced):
    with pytest.raises(ValueError, match='Unterminated'):
        attach_frames(b'{"kind":"isolation"}\n{"kind":', forced)


def test_complete_attach_frames_still_require_valid_json():
    assert attach_frames(b'{"kind":"isolation"}\n', None) == [{'kind': 'isolation'}]
    with pytest.raises(ValueError):
        attach_frames(b'not-json\n', None)


@pytest.mark.parametrize('receipt', [
    {'status': 'cleanup_failed', 'cleanupConfirmed': False},
    {'status': 'failed', 'cleanupConfirmed': True},
])
def test_paired_native_check_does_not_start_ui_after_unsuccessful_typecheck(monkeypatch, receipt):
    from tests.eidolon_cli import test_organization_project_workspace_native as native
    calls = []

    def unsuccessful(prepared, step):
        calls.append(step)
        return receipt

    monkeypatch.setattr(native, 'execute', unsuccessful)
    with pytest.raises(AssertionError):
        native.test_actual_renderer_typecheck_and_real_ui_share_source_and_bundle(None)
    assert calls == ['renderer-types']


def process_identity(pid):
    """Read host PID start time too, so PID reuse cannot masquerade as survival."""
    try:
        directory = Path('/proc') / str(pid)
        fields = (directory / 'stat').read_text().rsplit(') ', 1)[1].split()
        return directory.stat().st_uid, int(fields[19])
    except (FileNotFoundError, ProcessLookupError):
        return None


@contextmanager
def observed_workload(monkeypatch, markers):
    original = runtime._docker
    stop, observed = threading.Event(), threading.Event()
    evidence = {'observed': observed, 'processes': {}, 'errors': [], 'attach': None,
                'removed': False, 'absentAfterRemoval': False}
    observers = []
    seen = set()

    def observe(container, configuration):
        deadline = time.monotonic() + 120
        try:
            while not stop.is_set() and time.monotonic() < deadline:
                code, output, _, forced = original(['top', container, '-eo', 'pid,uid,args'],
                    configuration, runtime._Budget(None, 2, time.monotonic()), limit=65536)
                if code == 0 and forced is None:
                    for line in output['stdout'].decode('utf-8', 'strict').splitlines():
                        row = line.strip().split(maxsplit=2)
                        if len(row) == 3 and row[0].isdigit() and row[1] == '65534' and row[2] in markers:
                            pid = int(row[0])
                            identity = process_identity(pid)
                            if identity is not None and identity[0] == 65534:
                                evidence['processes'][pid] = identity
                                seen.add(row[2])
                                if seen == set(markers):
                                    observed.set()
                                    return
                stop.wait(0.1)
        except Exception as error:
            evidence['errors'].append(type(error).__name__)

    def monitored(arguments, configuration, budget, limit=1048576):
        if arguments[:2] == ['start', '--attach']:
            observer = threading.Thread(target=observe, args=(arguments[2], configuration), daemon=True)
            observers.append(observer)
            observer.start()
        answer = original(arguments, configuration, budget, limit)
        if arguments[:2] == ['start', '--attach']:
            evidence['attach'] = answer
        if arguments[:2] == ['rm', '--force'] and answer[0] == 0 and answer[3] is None:
            evidence['removed'] = True
            code, output, _, forced = original(
                ['container', 'ls', '--all', '--no-trunc', '--filter', 'id=' + arguments[2], '--format', '{{.ID}}'],
                configuration, runtime._Budget(None, 5, time.monotonic()), limit=4096)
            evidence['absentAfterRemoval'] = code == 0 and forced is None and not output['stdout'].strip()
        return answer

    monkeypatch.setattr(runtime, '_docker', monitored)
    try:
        yield evidence
    finally:
        stop.set()
        for observer in observers:
            observer.join(timeout=5)
            assert not observer.is_alive(), 'Native startup observer failed to stop'


def execute_probe(prepared, monkeypatch, body, *, wall_seconds=120, log_bytes=4096,
                  extra_markers=(), revoke_when_observed=False):
    original, directory, manifest, original_profile = prepared
    marker = 'eid-abuse-' + uuid.uuid4().hex[:12]
    # A finite observation window precedes the attack. The host must actually
    # see this UID/title; merely emitting a workload log cannot satisfy the gate.
    probe = ("\nimport abuseFs from 'node:fs';\n"
             f"process.title = {json.dumps(marker)};\n"
             "await new Promise((resolve) => setTimeout(resolve, 3000));\n" + body + '\n').encode()
    source = changed(original, 'apps/desktop/vitest.config.ts', probe)
    profile = deepcopy(original_profile)
    profile['limits'].update(wallSeconds=wall_seconds, scratchMiB=32, logBytes=log_bytes)
    started = time.monotonic()
    with observed_workload(monkeypatch, (marker, *extra_markers)) as evidence:
        result = runtime.run_prepared_workspace_step(source, directory, manifest, manifest['toolchain'],
            profile, 'ui-tests', allow_local_docker=True,
            check_authorized=lambda: not (revoke_when_observed and evidence['observed'].is_set()))
    assert evidence['observed'].is_set(), (evidence['errors'], result)
    assert evidence['errors'] == []
    assert len(evidence['processes']) == 1 + len(extra_markers)
    assert result['cleanupConfirmed'] is True, result
    assert evidence['removed'] and evidence['absentAfterRemoval'], result
    assert result['snapshotSha256'] == source.snapshot_sha256
    assert result['snapshotSha256'] != original.snapshot_sha256
    assert time.monotonic() - started < wall_seconds + 30
    deadline = time.monotonic() + 5
    while any(process_identity(pid) == identity for pid, identity in evidence['processes'].items()):
        assert time.monotonic() < deadline, 'Observed workload identity survived container cleanup'
        time.sleep(0.05)
    assert evidence['attach'] is not None
    code, output, totals, forced = evidence['attach']
    if forced not in {'cancelled', 'timed_out'}:
        assert result['isolation']['established'] is True, result
    assert len(output['stdout']) <= CONTROL_BYTES
    assert len(output['stderr']) <= log_bytes
    assert totals['stdout'] <= CONTROL_BYTES and totals['stderr'] <= log_bytes
    frames = attach_frames(output['stdout'], forced)
    assert frames and frames[0]['kind'] == 'isolation'
    assert frames[0]['supervisorUid'] == 0 and frames[0]['workloadUid'] == 65534
    assert frames[0]['supervisorCapabilities'] == ['SETUID', 'SETGID']
    assert frames[0]['workloadCapabilities'] == []
    return result, code, frames, forced


@pytest.mark.linux_only
def test_actual_cpu_loop_is_observed_then_stopped_and_removed(prepared, monkeypatch):
    result, code, frames, forced = execute_probe(prepared, monkeypatch, 'while (true) {}', wall_seconds=90)
    # A host deadline can win the race with PID 1's exit. The strict writer
    # does not emit a diagnostic after expiry. Neither path yields an outcome.
    assert result['status'] in {'failed', 'timed_out'}, result
    assert 'outcome' not in result
    assert forced == 'timed_out' or (forced is None and code == 124
            and len(frames) == 1 and frames[0]['kind'] == 'isolation')


@pytest.mark.linux_only
@pytest.mark.parametrize('descriptor', [1, 2], ids=['stdout', 'stderr'])
def test_actual_log_flood_is_bounded_after_observed_startup(prepared, monkeypatch, descriptor):
    body = (f"const abuseOutput = abuseFs.createWriteStream('', {{ fd: {descriptor}, autoClose: false }});\n"
            "for (let index = 0; index < 64; index++) {\n"
            "  await new Promise<void>((resolve, reject) => abuseOutput.write(Buffer.alloc(4096, 'x'),\n"
            "    (error) => error ? reject(error) : resolve()));\n"
            "}\nawait new Promise(() => {});")
    result, code, frames, forced = execute_probe(prepared, monkeypatch, body)
    assert result['status'] == 'failed', result
    assert 'outcome' not in result and forced is None
    assert code == 126 and frames[-1] == {'kind': 'output_limit'}


def control_attack(kind):
    if kind == 'exit_zero':
        return 'process.exit(0);'
    if kind == 'oversized':
        return ("const abuseControl = abuseFs.createWriteStream('', { fd: 3, autoClose: false });\n"
                "for (let index = 0; index < 17; index++) {\n"
                "  await new Promise<void>((resolve, reject) => abuseControl.write(Buffer.alloc(65536, 'x'),\n"
                "    (error) => error ? reject(error) : resolve()));\n"
                "}\nawait new Promise(() => {});")
    selected = '/source/root0/' + UI_TEST
    claim = {'kind': 'result', 'status': 'passed', 'recipe': 'workspace_vitest_ui',
             'configuration': 'apps/desktop/vitest.config.ts', 'testCount': 1, 'skippedCount': 0,
             'failedCount': 0, 'sourceCount': 0, 'selectedFiles': [selected], 'reportedFiles': [selected],
             'modules': [{'path': selected, 'passed': 1, 'skipped': 0, 'failed': 0, 'state': 'passed'}],
             'evidenceOrigin': 'workload-claim', 'toolEntry': 'node_modules/vitest/dist/node.js', 'toolExitCode': 0}
    payload = {'malformed': 'not-json\n', 'trailing': 'unframed-tail',
               'duplicate_result': (json.dumps(claim) + '\n') * 2}[kind]
    return f'abuseFs.writeSync(3, {json.dumps(payload)});\nprocess.exit(0);'


@pytest.mark.linux_only
@pytest.mark.parametrize('kind', ['malformed', 'trailing', 'duplicate_result', 'oversized', 'exit_zero'])
def test_actual_configuration_cannot_bypass_control_framing(prepared, monkeypatch, kind):
    result, code, frames, forced = execute_probe(prepared, monkeypatch, control_attack(kind))
    assert result['status'] == 'failed', result
    assert 'outcome' not in result and forced is None and code != 0
    assert len(frames) == 2 and frames[-1]['kind'] == 'blocked'
    # This rejects transport abuse; a single well-formed fabricated result is
    # still an untrusted workload claim, not proof that any assertions ran.


@pytest.mark.linux_only
def test_actual_scratch_direct_execution_is_denied_after_observed_startup(prepared, monkeypatch):
    body = """
const abuseAssert = await import('node:assert/strict');
const abuseProcesses = await import('node:child_process');
const abuseExecutable = '/tmp/eidolon-noexec-probe';
abuseFs.copyFileSync('/usr/bin/true', abuseExecutable, abuseFs.constants.COPYFILE_EXCL);
abuseFs.chmodSync(abuseExecutable, 0o755);
const abuseAttempt = abuseProcesses.spawnSync(abuseExecutable, [], { timeout: 2000, stdio: 'ignore' });
abuseAssert.equal(abuseAttempt.error?.code, 'EACCES');
abuseAssert.equal(abuseAttempt.status, null);
process.stderr.write('SCRATCH_DIRECT_EXECUTION_EACCES_WORKLOAD_CLAIM\\n');
"""
    result, code, _, forced = execute_probe(prepared, monkeypatch, body, log_bytes=32768)
    assert result['status'] == 'passed' and code == 0 and forced is None, json.dumps({
        **{key: result.get(key) for key in ('status', 'reason', 'cleanupConfirmed', 'exitCode', 'isolation')},
        'stderr': result.get('stderr', '')[:2000],
    }, sort_keys=True)
    assert result['isolation']['established'] is True
    assert result['outcome']['evidenceOrigin'] == 'workload-claim'
    assert 'SCRATCH_DIRECT_EXECUTION_EACCES_WORKLOAD_CLAIM' in result['stderr']
    # The trusted supervisor independently checks the noexec mount. The errno
    # report remains workload-originated; noexec does not stop script reading.


@pytest.mark.linux_only
@pytest.mark.parametrize('termination', ['timeout', 'revocation'])
def test_actual_detached_descendant_disappears_with_observed_workload(prepared, monkeypatch, termination):
    child_marker = 'eid-child-' + uuid.uuid4().hex[:12]
    child_code = f'process.title = {json.dumps(child_marker)}; setInterval(() => {{}}, 1000);'
    body = ("const abuseProcesses = await import('node:child_process');\n"
            "const abuseChild = abuseProcesses.spawn(process.execPath,\n"
            f"  ['--disable-sigusr1', '--input-type=module', '--eval', {json.dumps(child_code)}],\n"
            "  { detached: true, stdio: 'ignore' });\n"
            "abuseChild.unref();\nawait new Promise((resolve) => setTimeout(resolve, 300000));")
    result, code, frames, forced = execute_probe(prepared, monkeypatch, body, wall_seconds=90,
        extra_markers=(child_marker,), revoke_when_observed=termination == 'revocation')
    assert 'outcome' not in result
    if termination == 'revocation':
        assert result['status'] == 'cancelled' and forced == 'cancelled', result
    else:
        assert result['status'] in {'failed', 'timed_out'}, result
        assert forced == 'timed_out' or (forced is None and code == 124
            and len(frames) == 1 and frames[0]['kind'] == 'isolation')

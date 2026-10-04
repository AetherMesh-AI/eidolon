"""Production orphan cleanup paths, with synthetic ledger/process boundaries only."""
import json
import signal
from unittest.mock import Mock

import psutil
import pytest

from eidolon_cli import dashboard_procs as desktop, gateway, process_identity as identity

PID = 987654321
START = 1234.125


@pytest.fixture
def orphan(monkeypatch, tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    ledger = tmp_path / 'ledger.json'
    monkeypatch.setattr(identity, '_ledger_path', lambda: ledger)
    proc = Mock()
    proc.create_time.return_value = START
    proc.environ.return_value = {'HERMES_HOME': str(home)}
    monkeypatch.setattr(psutil, 'Process', Mock(return_value=proc))
    monkeypatch.setattr(psutil, 'pid_exists', lambda pid: True)
    kill = Mock()
    monkeypatch.setattr(desktop.os, 'kill', kill)
    monkeypatch.setattr(desktop, '_scan_dashboard_processes', lambda **kw: [(PID, 'python eidolon_cli/main.py serve --host 127.0.0.1 --port 0')])
    monkeypatch.setattr(desktop, '_process_ppid', lambda pid: 1)
    monkeypatch.setattr(desktop, '_process_age_seconds', lambda pid: 600)
    monkeypatch.setattr(desktop, '_lock_owned_serve_pids', lambda: set())
    monkeypatch.setattr(gateway, 'supports_systemd_services', lambda: False)
    monkeypatch.setattr(gateway, 'is_windows', lambda: False)
    monkeypatch.setattr(gateway, '_reaper_exclusion_pids', lambda extra: set())
    monkeypatch.setattr(gateway, '_reaper_candidate_is_supervisor_owned', lambda pid: False)
    monkeypatch.setattr(gateway, 'find_gateway_pids', lambda **kw: [PID])
    monkeypatch.setattr(gateway, '_await_gateway_exit', lambda pids, **kw: list(pids))
    from gateway import status
    monkeypatch.setattr(status, 'get_process_start_time', lambda pid: START)
    monkeypatch.setattr(status, 'write_planned_stop_marker', Mock())
    def run(kind, **changes):
        entry = dict(pid=PID, create_time=START, install=identity.install_id(),
                     purpose='serve' if kind == 'desktop' else 'gateway', home=str(home))
        entry.update(changes)
        ledger.write_text(json.dumps([entry]))
        if kind == 'desktop':
            desktop._reap_orphaned_desktop_local_serves(sleep_fn=lambda _: None)
        else:
            gateway._reap_unsupervised_gateway_orphans()
    return run, kill, proc, home


@pytest.mark.parametrize('kind', ['desktop', 'gateway'])
def test_foreign_install_identical_argv_is_not_signalled(orphan, kind):
    run, kill, _, _ = orphan
    run(kind, install='foreign-install')
    kill.assert_not_called()


@pytest.mark.parametrize('kind', ['desktop', 'gateway'])
@pytest.mark.parametrize('case', ['foreign-home', 'missing-start', 'inaccessible', 'reuse-term', 'reuse-kill'])
def test_identity_must_remain_owned_before_each_signal(orphan, kind, case):
    run, kill, proc, home = orphan
    changes = {}
    if case == 'foreign-home':
        changes['home'] = str(home.parent / 'foreign')
    elif case == 'missing-start':
        changes['create_time'] = None
    elif case == 'inaccessible':
        proc.create_time.side_effect = psutil.AccessDenied(PID)
    elif case == 'reuse-term':
        proc.create_time.side_effect = [START, START + 0.25, START + 0.25]
    elif case == 'reuse-kill':
        def signal_sent(*args):
            proc.create_time.return_value = START + 0.25
        kill.side_effect = signal_sent
    run(kind, **changes)
    assert [c.args[1] for c in kill.call_args_list] == ([signal.SIGTERM] if case == 'reuse-kill' else [])


@pytest.mark.parametrize('kind', ['desktop', 'gateway'])
def test_same_install_home_old_owned_child_is_cleaned(orphan, kind):
    run, kill, _, _ = orphan
    run(kind)
    assert [c.args for c in kill.call_args_list] == [(PID, signal.SIGTERM), (PID, signal.SIGKILL)]


def test_register_self_records_exact_effective_home(orphan, monkeypatch):
    _, _, _, home = orphan
    captured = []
    monkeypatch.setattr(identity, '_desktop_spawner_identity', lambda: (None, None))
    monkeypatch.setattr(identity, '_process_create_time', lambda: START)
    monkeypatch.setattr(identity, '_append_entry', lambda entry: captured.append(entry) or True)
    assert identity.register_self('serve')
    assert captured[0].home == str(home.resolve())


@pytest.mark.parametrize('kind', ['desktop', 'gateway'])
@pytest.mark.parametrize('case', ['recorded-home', 'legacy-owned', 'legacy-no-home', 'env-denied', 'wrong-purpose', 'no-ledger', 'lock', 'new-lock'])
def test_home_and_lock_guards(orphan, monkeypatch, kind, case):
    run, kill, proc, home = orphan
    changes = {}
    if case == 'recorded-home':
        proc.environ.side_effect = psutil.AccessDenied(PID)
    elif case.startswith('legacy') or case == 'env-denied':
        changes['home'] = ''
        if case == 'legacy-no-home':
            proc.environ.return_value = {}
        elif case == 'env-denied':
            proc.environ.side_effect = psutil.AccessDenied(PID)
    elif case == 'wrong-purpose':
        changes['purpose'] = 'mcp'
    elif case == 'no-ledger':
        monkeypatch.setattr(identity, '_read_ledger', lambda p: None)
    elif case in ('lock', 'new-lock'):
        values = [{PID}] if case == 'lock' else [set(), {PID}]
        last = values[-1]
        def locks(*a):
            return values.pop(0) if values else last
        monkeypatch.setattr(desktop, '_lock_owned_serve_pids', locks)
        monkeypatch.setattr(gateway, '_reaper_exclusion_pids', locks)
    run(kind, **changes)
    expected = [signal.SIGTERM, signal.SIGKILL] if case in ('recorded-home', 'legacy-owned') else []
    assert [c.args[1] for c in kill.call_args_list] == expected


@pytest.mark.parametrize('case', ['parent', 'young'])
def test_desktop_ancestry_age_preserved(orphan, monkeypatch, case):
    run, kill, _, _ = orphan
    if case == 'parent':
        monkeypatch.setattr(desktop, '_process_ppid', lambda p: 88)
    else:
        monkeypatch.setattr(desktop, '_process_age_seconds', lambda p: 1)
    run('desktop')
    kill.assert_not_called()


def test_gateway_supervisor_preserved(orphan, monkeypatch):
    run, kill, _, _ = orphan
    monkeypatch.setattr(gateway, 'supports_systemd_services', lambda: True)
    run('gateway')
    kill.assert_not_called()

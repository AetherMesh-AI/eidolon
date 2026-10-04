"""Real CLI/module startup -> real ledger -> strict ownership, without a backend."""
import asyncio
import atexit
import json
import os
import sys
from unittest.mock import AsyncMock, Mock

import psutil
import pytest

from eidolon_cli import gateway as cli, process_identity as identity
import eidolon_constants as constants


class HardExit(BaseException):
    pass


@pytest.fixture
def startup_boundary(monkeypatch, tmp_path):
    # Import the real module only after the external harness's process guards.
    import gateway.run as runtime
    import gateway.status as status
    import gateway.code_skew as skew
    import eidolon_cli.resource_limits as limits
    import eidolon_startup_watchdog as watchdog
    import eidolon_cli.stdio as stdio

    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setattr(type(tmp_path), 'home', lambda: tmp_path)
    for key in ('HERMES_SPAWN', 'HERMES_PARENT_PID', 'HERMES_HOME'):
        monkeypatch.delenv(key, raising=False)
    proc = Mock()
    proc.create_time.return_value = 1234.125
    proc.environ.side_effect = psutil.AccessDenied(1)
    monkeypatch.setattr(psutil, 'Process', lambda pid: proc)
    monkeypatch.setattr(identity, 'attach_self_to_kill_on_close_job', Mock())
    registered = Mock(wraps=identity.register_self)
    monkeypatch.setattr(identity, 'register_self', registered)
    monkeypatch.setattr(atexit, 'register', lambda f, *a, **k: f)
    monkeypatch.setattr(sys, 'argv', ['gateway'])
    for name in ('_guard_official_docker_root_gateway', '_guard_named_profile_under_multiplexer',
                 '_guard_supervised_gateway_conflict', '_guard_existing_gateway_process_conflict',
                 '_apply_startup_watchdog_config', '_respawn_storm_backoff',
                 '_absorb_windows_console_controls', 'refresh_systemd_unit_if_needed'):
        monkeypatch.setattr(cli, name, lambda *a, **k: None)
    for name in ('supports_systemd_services', '_stdin_is_tty', '_windows_console_window_attached',
                 '_windows_gateway_should_absorb_console_controls', 'is_linux'):
        monkeypatch.setattr(cli, name, lambda: False)
    monkeypatch.setattr(cli, '_windows_gateway_breakaway_state', lambda: None)
    monkeypatch.setattr(cli, '_make_exit_diag', lambda: lambda *a, **k: None)
    monkeypatch.setattr(watchdog, 'arm_startup_watchdog', lambda: None)
    monkeypatch.setattr(stdio, 'configure_windows_stdio', lambda: None)
    monkeypatch.setattr(limits, 'apply_nofile_soft_limit', lambda: None)
    monkeypatch.setattr(skew, 'record_boot_fingerprint', lambda: None)
    monkeypatch.setattr(status, 'get_running_pid', lambda: None)
    monkeypatch.setattr(runtime, '_start_gateway_configure_logging', lambda *a: None)
    monkeypatch.setattr(runtime, '_enable_multiplex_log_routing', lambda *a: None)
    monkeypatch.setattr(runtime, '_start_gateway_claim_pid_file', lambda: True)
    monkeypatch.setattr(runtime, '_start_gateway_start_control_socket', AsyncMock(return_value=None))
    monkeypatch.setattr(runtime, '_discover_gateway_mcp_tools', AsyncMock())
    monkeypatch.setattr(runtime, '_ensure_windows_gateway_venv_imports', lambda: None)
    monkeypatch.setattr(runtime, '_shutdown_gateway_health_export', lambda *a: None)
    monkeypatch.setattr(asyncio.SelectorEventLoop, 'add_signal_handler', lambda *a: None)
    monkeypatch.setattr(runtime.threading.Thread, 'start', lambda *a: None)
    # External lifecycle services (DB recovery/keepalive); identity registration is NOT stubbed.
    original_best_effort = runtime._best_effort
    def bounded_best_effort(fn, *args):
        if fn.__name__ in ('_lifecycle_record_startup', '_start_keepalive', '_recover_pending'):
            return None
        return original_best_effort(fn, *args)
    monkeypatch.setattr(runtime, '_best_effort', bounded_best_effort)
    def hard_exit(code):
        raise HardExit(code)
    monkeypatch.setattr(os, '_exit', hard_exit)
    import gateway.lifecycle_ledger as lifecycle
    import eidolon_logging
    monkeypatch.setattr(lifecycle, 'mark_exited', Mock())
    monkeypatch.setattr(eidolon_logging, 'drain_log_queue', Mock())
    remove_pid, release_lock = Mock(), Mock()
    monkeypatch.setattr(status, 'remove_pid_file', remove_pid)
    monkeypatch.setattr(status, 'release_gateway_runtime_lock', release_lock)
    return runtime, registered, proc, remove_pid, release_lock


@pytest.mark.parametrize('entry', ['cli', 'module'])
@pytest.mark.parametrize('home_mode', ['default', 'named', 'override'])
@pytest.mark.parametrize('outcome', ['success', 'false', 'error', 'cancel', 'system_exit'])
def test_entry_registration_to_ownership(startup_boundary, monkeypatch, tmp_path, entry, home_mode, outcome):
    runtime, registered, proc, remove_pid, release_lock = startup_boundary
    if home_mode == 'named':
        monkeypatch.setenv('HERMES_HOME', str(tmp_path / '.eidolon/profiles/demo'))
    token = constants.set_eidolon_home_override(tmp_path / 'task-home' if home_mode == 'override' else None)
    expected_home = str(constants.get_eidolon_home().resolve())
    visited = []
    async def start():
        # This is the first synthetic adapter boundary, after REAL shared startup.
        ledger = identity._ledger_path()
        assert ledger.is_relative_to(tmp_path)
        entries = json.loads(ledger.read_text()) if ledger.exists() else []
        assert len(entries) == 1, 'gateway startup must register before adapters'
        assert entries[0]['purpose'] == 'gateway'
        assert entries[0]['home'] == expected_home
        assert identity.owned_process_start_time(os.getpid(), 'gateway') == 1234.125
        assert identity.owned_process_start_time(os.getpid(), 'serve') is None
        proc.create_time.return_value = 1235.125
        assert identity.owned_process_start_time(os.getpid(), 'gateway') is None
        proc.create_time.return_value = 1234.125
        registered.assert_called_once_with('gateway')
        visited.append(True)
        exceptions = {'error': RuntimeError('synthetic startup failure'),
                      'cancel': asyncio.CancelledError(), 'system_exit': SystemExit(78)}
        if outcome in exceptions:
            raise exceptions[outcome]
        return outcome == 'success'
    runner = Mock(config=Mock(), should_exit_cleanly=True, exit_reason=None, exit_code=None)
    runner.start = start
    monkeypatch.setattr(runtime, 'GatewayRunner', lambda config: runner)
    try:
        expected_exception = {'error': RuntimeError, 'cancel': asyncio.CancelledError}.get(outcome, HardExit)
        with pytest.raises(expected_exception) as caught:
            if entry == 'cli':
                cli.run_gateway(quiet=True)
            else:
                runtime.main()
        if expected_exception is HardExit:
            assert caught.value.args == ({'success': 0, 'false': 1, 'system_exit': 78}[outcome],)
            remove_pid.assert_called_once_with()
            release_lock.assert_called_once_with()
        else:
            remove_pid.assert_not_called()
            release_lock.assert_not_called()
        assert visited == [True]
        # The ledger intentionally persists until the process dies and the next registration prunes it.
        assert identity.owned_process_start_time(os.getpid(), 'gateway') == 1234.125
    finally:
        constants.reset_eidolon_home_override(token)


@pytest.mark.parametrize('entry', ['cli', 'module'])
@pytest.mark.parametrize('failure', ['false', 'raise'])
def test_registration_failure_remains_best_effort(startup_boundary, monkeypatch, entry, failure):
    runtime, registered, proc, remove_pid, release_lock = startup_boundary
    registered.return_value = False
    registered.side_effect = OSError('synthetic ledger failure') if failure == 'raise' else None
    runner = Mock(config=Mock(), should_exit_cleanly=True, exit_reason=None, exit_code=None)
    runner.start = AsyncMock(return_value=True)
    monkeypatch.setattr(runtime, 'GatewayRunner', lambda config: runner)
    with pytest.raises(HardExit) as caught:
        cli.run_gateway(quiet=True) if entry == 'cli' else runtime.main()
    assert caught.value.args == (0,)
    runner.start.assert_awaited_once()
    registered.assert_called_once_with('gateway')
    remove_pid.assert_called_once_with()
    release_lock.assert_called_once_with()

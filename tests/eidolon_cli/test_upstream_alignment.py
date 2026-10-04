"""Offline behavioral upstream contracts; no live updater or source extraction."""
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from eidolon_cli import update_cmd, update_cmd_git

ROOT = Path(__file__).resolve().parents[2]


def test_network_git_has_bounded_timeout(monkeypatch, tmp_path):
    run = Mock(side_effect=subprocess.TimeoutExpired(['git', 'fetch'], 300))
    monkeypatch.setattr(update_cmd.subprocess, 'run', run)
    result = update_cmd._git_run(['git'], ['fetch', 'origin', 'main'], cwd=tmp_path, network=True)
    assert result.returncode == 124
    assert run.call_args.kwargs['timeout'] == 300
    with pytest.raises(subprocess.CalledProcessError):
        update_cmd._git_run(['git'], ['fetch'], cwd=tmp_path, network=True, check=True)


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


@pytest.mark.parametrize('shallow', [False, True])
@pytest.mark.parametrize('fail', [False, True])
def test_apply_caller_completes_history_before_checkout(monkeypatch, tmp_path, shallow, fail):
    from eidolon_cli import main
    remote = tmp_path / 'remote'
    remote.mkdir()
    git(remote, 'init', '-b', 'main')
    git(remote, 'config', 'user.name', 'Fixture')
    git(remote, 'config', 'user.email', 'fixture@example.invalid')
    for message in ['anchor', 'next']:
        git(remote, 'commit', '--allow-empty', '-m', message)
    checkout = tmp_path / 'checkout'
    subprocess.run(['git', 'clone', *(['--depth', '1'] if shallow else []), remote.as_uri(), str(checkout)], check=True)
    monkeypatch.setattr(main, 'PROJECT_ROOT', checkout)
    monkeypatch.setattr(main, '_run_pre_update_backup', lambda _: None)
    monkeypatch.setattr(main, '_pause_windows_gateways_for_update', lambda: None)
    monkeypatch.setattr(main, '_resolve_update_branch', lambda _: 'main')
    monkeypatch.setattr(main, '_warn_orphaned_update_autostashes', lambda *a: None)
    monkeypatch.setattr(update_cmd, '_begin_update_receipt_and_plan', lambda _: None)
    monkeypatch.setattr(update_cmd, '_record_update_step', lambda *a: None)
    monkeypatch.setattr(update_cmd, '_resolve_update_options', lambda *a: SimpleNamespace(gw_input_fn=None, assume_yes=True, switch_branch=False))
    monkeypatch.setattr(update_cmd, '_prepare_git_command', lambda: (False, ['git'], False))
    if fail:
        git(checkout, 'remote', 'set-url', 'origin', str(tmp_path / 'absent'))
    class ReachedCheckout(Exception):
        pass
    def checkout_boundary(*args, **kwargs):
        assert git(checkout, 'rev-parse', '--is-shallow-repository') == 'false'
        assert git(checkout, 'rev-list', '--count', 'origin/main') == '2'
        assert args[:3] == (['git'], 'main', 'main')
        raise ReachedCheckout()
    monkeypatch.setattr(update_cmd, '_prepare_checkout_for_update', checkout_boundary)
    with pytest.raises(SystemExit if fail else ReachedCheckout) as error:
        # force_venv avoids a host process sweep, not a simulated host OS.
        update_cmd._cmd_update_impl(SimpleNamespace(force_venv=True), gateway_mode=False)
    if fail:
        assert error.value.code == 1
        assert git(checkout, 'rev-parse', '--is-shallow-repository') == str(shallow).lower()


def test_official_upstream_add_is_not_disabled(tmp_path):
    git(tmp_path, 'init')
    assert update_cmd_git._add_upstream_remote(['git'], tmp_path)
    assert git(tmp_path, 'remote', 'get-url', 'upstream') == 'https://github.com/AetherMesh-AI/Eidolon.git'


def test_linux_symlink_is_not_package_skew(tmp_path):
    actual = tmp_path / 'actual'
    unpacked = actual / 'apps/desktop/release/linux-unpacked'
    unpacked.mkdir(parents=True)
    link = tmp_path / 'link'
    link.symlink_to(actual, target_is_directory=True)
    # macOS lacks GNU readlink -m; substitute that OS utility only. The whole
    # production script runs its supported, early-exiting gate entrypoint.
    tools = tmp_path / 'bin'
    tools.mkdir()
    import sys
    readlink = tools / 'readlink'
    readlink.write_text(f'#!{sys.executable}\nimport os,sys\nprint(os.path.realpath(sys.argv[-1]))\n')
    readlink.chmod(0o755)
    for target, expected in [(unpacked / 'eidolon', 'relaunch'), (tmp_path / 'other/eidolon', 'skew')]:
        result = subprocess.run(['bash', str(ROOT / 'scripts/desktop-update/posix.sh'), '--self-test-gate',
            '--install-root', str(link), '--relaunch-target', str(target), '--', '--no-sandbox'],
            env={**os.environ, 'PATH': str(tools) + os.pathsep + os.environ['PATH']}, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().split(':', 1)[0] == expected, result.stdout


@pytest.mark.skipif(os.name != 'nt' or not shutil.which('powershell'), reason='requires native Windows PowerShell')
def test_windows_real_cwd_setup_precedes_update(tmp_path):
    result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'scripts/desktop-update/windows.ps1'), '-InstallRoot', str(tmp_path),
        '-SelfTestWorkingDirectory'], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'WORKING-DIRECTORY SELF-TEST: PASS' in result.stdout

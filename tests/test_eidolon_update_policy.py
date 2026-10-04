"""Offline Eidolon source-policy acceptance; no installed agent imports."""

import os
from pathlib import Path
import subprocess
from eidolon_cli import __version__


def test_build_identity_reads_stamp_without_spawning_or_changing_target(tmp_path, monkeypatch):
    import json
    from eidolon_cli import eidolon_update_policy as policy
    from eidolon_cli.eidolon_version import ANCHOR_COMMIT, RELEASE_VERSION, fallback
    value = fallback('a' * 40, False)
    value.update(version=RELEASE_VERSION, versionSource='stamp', baseTag='alpha-v0.1.0',
                 baseCommit=ANCHOR_COMMIT, distance=12)
    package = tmp_path / 'eidolon_cli'
    package.mkdir()
    (package / '_build_identity.json').write_text(json.dumps(value))
    def forbidden(*args, **kwargs): raise AssertionError('runtime spawned Git')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    identity = policy.build_identity(tmp_path)
    assert identity['version'] == RELEASE_VERSION
    assert identity['versionSource'] == 'stamp'
    assert identity['channel'] == 'alpha'
    assert identity['repository'] == 'AetherMesh-AI/Eidolon'
    assert identity['updateBranch'] == 'main'

    # A previous Git-distance release is not a valid stamp for this release.
    value['version'] = '0.1.12'
    (package / '_build_identity.json').write_text(json.dumps(value))
    rejected = policy.build_identity(tmp_path)
    assert rejected['version'] == RELEASE_VERSION
    assert rejected['versionSource'] == 'fallback'


ROOT = Path(__file__).resolve().parents[1]

def policy():
    from eidolon_cli import eidolon_update_policy as module
    return module


def test_official_source_only():
    p = policy()
    for url in ['https://github.com/AetherMesh-AI/Eidolon.git', 'git@github.com:AetherMesh-AI/Eidolon.git']:
        assert p.is_official_source(url)
    for url in ['', '/tmp/repo', 'https://github.com/NousResearch/hermes-agent.git', 'https://github.com.evil/AetherMesh-AI/Eidolon.git']:
        assert not p.is_official_source(url)
    assert p.UPDATE_BRANCH == 'main'


def test_git_ancestry_not_hash_order(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('HERMES_HOME', str(home / '.eidolon'))
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.setenv('GIT_CONFIG_SYSTEM', os.devnull)
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(tmp_path))
    def git(*args, cwd=tmp_path):
        return subprocess.check_output(['git', *args], cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()
    remote = tmp_path / 'remote.git'
    work = tmp_path / 'work'
    git('init', '--bare', str(remote))
    git('init', '-b', 'main', str(work))
    git('config', 'user.name', 'Offline test', cwd=work)
    git('config', 'user.email', 'offline@example.invalid', cwd=work)
    git('commit', '--allow-empty', '-m', 'first', cwd=work)
    first = git('rev-parse', 'HEAD', cwd=work)
    git('remote', 'add', 'origin', str(remote), cwd=work)
    git('push', 'origin', 'main', cwd=work)
    clone = tmp_path / 'clone'
    git('clone', '-b', 'main', str(remote), str(clone))
    git('commit', '--allow-empty', '-m', 'second', cwd=work)
    second = git('rev-parse', 'HEAD', cwd=work)
    git('push', 'origin', 'main', cwd=work)
    git('fetch', 'origin', 'main', cwd=clone)
    p = policy()
    assert p.relation(clone, first, second) == 'behind'
    assert p.relation(clone, second, first) == 'ahead'
    assert p.relation(clone, first, first) == 'current'
    git('merge', '--ff-only', 'origin/main', cwd=clone)
    assert git('rev-parse', 'HEAD', cwd=clone) == second
    git('checkout', '-b', 'side', first, cwd=work)
    git('commit', '--allow-empty', '-m', 'divergence', cwd=work)
    side = git('rev-parse', 'HEAD', cwd=work)
    assert p.relation(work, side, second) == 'diverged'
    assert p.build_identity(work)['commit'] == side
    assert p.build_identity(work)['version'] == __version__


def test_fork_sync_reuses_upstream_force_with_lease(tmp_path, monkeypatch):
    from eidolon_cli import update_cmd_git
    calls = []
    def git_ok(*args, **kwargs):
        calls.append((args, kwargs))
        return True
    monkeypatch.setattr(update_cmd_git, '_git_ok', git_ok)
    assert update_cmd_git._sync_fork_with_upstream(['git'], tmp_path) is True
    assert calls == [((['git'], ['push', 'origin', 'main', '--force-with-lease'], tmp_path), {'network': True})]


def test_windows_git_error_dispatcher_refuses_archive(tmp_path, monkeypatch):
    import pytest
    from types import SimpleNamespace
    from eidolon_cli import update_cmd, main
    archive_calls = []
    monkeypatch.setattr(main, "_is_windows", lambda: True)
    monkeypatch.setattr(main, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(update_cmd, "_update_via_zip", lambda *a, **k: archive_calls.append((a, k)))
    error = subprocess.CalledProcessError(128, ["git", "pull", "--ff-only"], stderr="injected Windows Git I/O failure")
    with pytest.raises(SystemExit) as result:
        update_cmd._handle_update_called_process_error(error, SimpleNamespace(), False, False)
    assert result.value.code == 1
    assert archive_calls == []


def test_update_body_routes_windows_git_exception_without_network(tmp_path, monkeypatch):
    import pytest
    import urllib.request
    from types import SimpleNamespace
    from eidolon_cli import update_cmd, main
    calls = []
    monkeypatch.setattr(main, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(main, '_is_windows', lambda: True)
    monkeypatch.setattr(main, '_run_pre_update_backup', lambda args: None)
    monkeypatch.setattr(main, '_pause_windows_gateways_for_update', lambda: None)
    monkeypatch.setattr(main, '_resume_windows_gateways_after_update', lambda state: None)
    monkeypatch.setattr(main, '_resolve_update_branch', lambda args: 'main')
    monkeypatch.setattr(main, '_warn_orphaned_update_autostashes', lambda *args: None)
    monkeypatch.setattr(update_cmd, '_resolve_update_options', lambda *args: SimpleNamespace(gw_input_fn=None, assume_yes=True))
    monkeypatch.setattr(update_cmd, '_begin_update_receipt_and_plan', lambda args: None)
    monkeypatch.setattr(update_cmd, '_record_update_step', lambda *args: None)
    monkeypatch.setattr(update_cmd, '_prepare_git_command', lambda: (False, ['git'], False))
    monkeypatch.setattr(update_cmd, '_update_via_zip', lambda *a, **k: calls.append('archive'))
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: calls.append('network'))
    def git_failure(*args, **kwargs):
        raise subprocess.CalledProcessError(128, ['git', 'rev-parse'], stderr='injected Windows Git failure')
    monkeypatch.setattr(update_cmd, '_git_run', git_failure)
    with pytest.raises(SystemExit) as result:
        update_cmd._cmd_update_impl(SimpleNamespace(force_venv=True), gateway_mode=False)
    assert result.value.code == 1
    assert calls == []

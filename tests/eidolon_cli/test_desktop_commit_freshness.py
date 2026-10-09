"""A matching renderer content hash must not reuse a previous commit's app."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from eidolon_cli import eidolon_version as version
from eidolon_cli import main_desktop as desktop


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def stamp(commit):
    return {**version.fallback(commit, False), 'versionSource': 'git-derived',
            'baseTag': 'alpha-v0.1.0', 'baseCommit': version.ANCHOR_COMMIT, 'distance': 1}


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / 'source'
    root.mkdir()
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    ui = root / 'apps/desktop'
    (ui / 'dist').mkdir(parents=True)
    (ui / 'dist/index.html').write_text('<html>fixture</html>')
    (ui / 'package.json').write_text('{"name":"fixture"}')
    (root / '.gitignore').write_text('apps/desktop/dist/\napps/desktop/build/\napps/desktop/release/\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'desktop source')
    if sys.platform == 'darwin':
        executable = ui / 'release/mac-universal/Eidolon.app/Contents/MacOS/Eidolon'
        resources = executable.parent.parent / 'Resources'
    elif sys.platform == 'win32':
        executable = ui / 'release/win-unpacked/Eidolon.exe'
        resources = executable.parent / 'resources'
    else:
        import platform
        directory = 'linux-arm64-unpacked' if platform.machine().lower() in ('arm64', 'aarch64') else 'linux-unpacked'
        executable = ui / 'release' / directory / 'Eidolon'
        resources = executable.parent / 'resources'
    executable.parent.mkdir(parents=True)
    executable.write_text('fixture executable')
    resources.mkdir(parents=True)
    (ui / 'build').mkdir()
    head = git(root, 'rev-parse', 'HEAD')
    for target in [resources / 'install-stamp.json', ui / 'build/install-stamp.json']:
        target.write_text(json.dumps(stamp(head)))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'profile'))
    monkeypatch.delenv('EIDOLON_HOME', raising=False)
    return root, ui, resources, head


@pytest.mark.parametrize('source_mode', [False, True])
def test_backend_only_commit_invalidates_current_content_cache(source, source_mode):
    root, ui, resources, old = source
    desktop._write_desktop_build_stamp(root, source_mode=source_mode)
    before = desktop._compute_desktop_content_hash(root)
    assert not desktop._desktop_build_needed(ui, root, source_mode=source_mode)
    (root / 'backend-only.txt').write_text('new source commit, identical desktop bytes')
    git(root, 'add', 'backend-only.txt')
    git(root, 'commit', '-m', 'backend-only source update')
    new = git(root, 'rev-parse', 'HEAD')
    assert old != new
    assert desktop._compute_desktop_content_hash(root) == before
    assert desktop._desktop_build_needed(ui, root, source_mode=source_mode)
    artifact_stamp = ui / 'build/install-stamp.json' if source_mode else resources / 'install-stamp.json'
    assert json.loads(artifact_stamp.read_text())['commit'] == old, 'Freshness probing must not relabel an old app'
    artifact_stamp.write_text(json.dumps(stamp(new)))
    assert not desktop._desktop_build_needed(ui, root, source_mode=source_mode)


@pytest.mark.parametrize('damage', ['missing', 'malformed', 'fallback', 'wrong-commit', 'wrong-version', 'dirty', 'unknown-dirty'])
def test_known_source_rejects_missing_or_unverified_packaged_identity(source, damage):
    root, ui, resources, head = source
    desktop._write_desktop_build_stamp(root, source_mode=False)
    target = resources / 'install-stamp.json'
    value = stamp(head)
    if damage == 'missing': target.unlink()
    elif damage == 'malformed': target.write_text('{')
    else:
        if damage == 'fallback': value['versionSource'] = 'fallback'
        if damage == 'wrong-commit': value.update(commit='a' * 40, shortCommit='a' * 12)
        if damage == 'wrong-version': value['version'] = '0.0.0'
        if damage == 'dirty': value['dirty'] = True
        if damage == 'unknown-dirty': value['dirty'] = None
        target.write_text(json.dumps(value))
    assert desktop._desktop_build_needed(ui, root, source_mode=False)

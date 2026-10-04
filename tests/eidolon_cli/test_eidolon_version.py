"""Offline contracts for one build-time Git owner and spawn-free runtime reads."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def owner():
    spec = importlib.util.spec_from_file_location('version_owner', ROOT / 'eidolon_cli/eidolon_version.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ANCHOR_COMMIT = globals().get("_FIXTURE_ANCHOR", module.ANCHOR_COMMIT)
    return module


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for key in ('GITHUB_SHA', 'GITHUB_REF_NAME', 'GITHUB_HEAD_REF'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.setenv('GIT_CONFIG_SYSTEM', os.devnull)
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(tmp_path))
    root = tmp_path / 'repo'
    root.mkdir()
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
    git('init', '-b', 'main')
    git('config', 'user.name', 'Offline fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    (root / '.gitignore').write_text('eidolon_cli/_build_identity.json\n')
    git('add', '.gitignore')
    git('commit', '-m', 'anchor')
    git('tag', 'alpha-v0.1.0')
    monkeypatch.setitem(globals(), '_FIXTURE_ANCHOR', git('rev-parse', 'HEAD'))
    return root, git


def test_declared_release_keeps_git_lineage_without_version_counters(repo):
    root, git = repo
    v = owner()
    assert v.resolve_identity(root)['version'] == v.RELEASE_VERSION
    git('commit', '--allow-empty', '-m', 'one')
    result = v.resolve_identity(root)
    assert (result['version'], result['distance'], result['channel']) == (v.RELEASE_VERSION, 1, 'alpha')
    assert result['commit'] == git('rev-parse', 'HEAD')
    assert result['shortCommit'] == result['commit'][:12]
    git('tag', '-a', 'alpha-v2.3.99', '-m', 'annotated')
    git('commit', '--allow-empty', '-m', 'two')
    result = v.resolve_identity(root)
    assert (result['version'], result['baseTag'], result['distance']) == (v.RELEASE_VERSION, 'alpha-v0.1.0', 2)


def test_merge_counts_once_and_ignores_side_anchor(repo):
    root, git = repo
    git('checkout', '-b', 'side')
    for n in range(3):
        git('commit', '--allow-empty', '-m', f'side {n}')
    git('tag', 'alpha-v9.9.9')
    assert owner().resolve_identity(root)['versionSource'] == 'fallback'
    git('checkout', 'main')
    git('merge', '--no-ff', 'side', '-m', 'merge')
    result = owner().resolve_identity(root)
    assert (result['version'], result['distance']) == (owner().RELEASE_VERSION, 1)
    git('checkout', '--detach')
    assert owner().resolve_identity(root)['version'] == owner().RELEASE_VERSION


@pytest.mark.parametrize('failure', ['shallow', 'ci-mismatch', 'side'])
def test_invalid_provenance_is_explicit_fallback(repo, failure):
    root, git = repo
    env = {}
    if failure == 'missing': git('tag', '-d', 'alpha-v0.1.0')
    if failure == 'conflicting': git('tag', 'alpha-v0.2.0')
    if failure == 'malformed': git('tag', 'alpha-v01.2.3')
    if failure == 'shallow': (root / '.git/shallow').write_text(git('rev-parse', 'HEAD') + '\n')
    if failure == 'ci-mismatch': env['GITHUB_SHA'] = 'a' * 40
    if failure == 'side': git('checkout', '-b', 'side')
    result = owner().resolve_identity(root, env=env)
    assert result['versionSource'] == 'fallback'
    assert result['distance'] is None
    assert 'build provenance unverified' in owner().format_identity(result)
    with pytest.raises(ValueError): owner().write_identity(root, require_verified=True, env=env)


@pytest.mark.parametrize('failure, reason', [
    ('detached', 'Detached build lacks main provenance'),
    ('ci-mismatch', 'Invalid or inconsistent CI SHA'),
    ('ci-malformed', 'Invalid or inconsistent CI SHA'),
    ('side', 'Unknown or unsupported build branch'),
])
@pytest.mark.parametrize('shallow', [False, True])
def test_existing_stamp_cannot_rescue_invalid_provenance(repo, failure, reason, shallow):
    root, git = repo
    v = owner()
    git('commit', '--allow-empty', '-m', 'advance')
    stamp = v.write_identity(root, require_verified=True)
    target = root / v.STAMP_PATH
    original = target.read_bytes()
    env = {}
    if failure == 'conflicting': git('tag', 'alpha-v0.2.0', stamp['baseCommit'])
    if failure == 'malformed': git('tag', 'alpha-v01.2.3')
    if failure == 'unresolvable':
        git('tag', 'alpha-v0.2.0', git('rev-parse', 'HEAD^{tree}'))
    if failure == 'detached':
        git('checkout', '--detach')
        git('branch', '-f', 'main', stamp['baseCommit'])
    if failure == 'ci-mismatch': env['GITHUB_SHA'] = 'a' * 40
    if failure == 'ci-malformed': env['GITHUB_SHA'] = 'invalid'
    if failure == 'side': git('checkout', '-b', 'side')
    if shallow:
        # Keep the anchor visible while marking its ancestry unavailable.
        (root / '.git/shallow').write_text(stamp['baseCommit'] + '\n')
    assert git('rev-parse', 'HEAD') == stamp['commit']
    # Strict refusal must preserve an existing output, not overwrite it.
    with pytest.raises(ValueError, match=reason):
        v.write_identity(root, require_verified=True, env=env)
    assert target.read_bytes() == original
    result = v.resolve_identity(root, env=env)
    assert result['versionSource'] == 'fallback'
    assert reason in result['versionReason']
    assert result['distance'] is None
    assert 'build provenance unverified' in v.format_identity(result)


@pytest.mark.parametrize('missing', ['gitless'])
def test_existing_stamp_preserves_identity_when_history_unavailable(repo, missing):
    root, git = repo
    v = owner()
    git('commit', '--allow-empty', '-m', 'advance')
    stamp = v.write_identity(root, require_verified=True)
    if missing == 'tag': git('tag', '-d', stamp['baseTag'])
    if missing == 'shallow': (root / '.git/shallow').write_text(stamp['commit'] + '\n')
    if missing == 'gitless': (root / '.git').rename(root / 'hidden-git')
    result = v.write_identity(root, require_verified=True, env={})
    assert result['versionSource'] == 'stamp'
    for key in ('version', 'commit', 'baseTag', 'baseCommit', 'distance'):
        assert result[key] == stamp[key]
    assert v.validate_identity(json.loads((root / v.STAMP_PATH).read_text()))


def test_generation_runtime_roundtrip_stale_and_gitless(repo, monkeypatch):
    root, git = repo
    v = owner()
    result = v.write_identity(root)
    assert result['dirty'] is False
    assert not git('status', '--porcelain')
    # Runtime reads exactly the generated artifact without launching Git.
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('runtime spawned'))
    assert v.runtime_identity(root)['version'] == result['version']
    monkeypatch.undo()
    git('commit', '--allow-empty', '-m', 'changed')
    assert v.runtime_identity(root)['versionSource'] == 'fallback'
    git('tag', '-d', 'alpha-v0.1.0')
    assert v.resolve_identity(root)['versionSource'] == 'git-derived'
    (root / '.git').rename(root / 'hidden-git')
    assert v.runtime_identity(root)['version'] == result['version']
    assert v.resolve_identity(root)['versionSource'] == 'stamp'


def test_stamp_reuse_and_dirty_unknown(repo, monkeypatch):
    root, git = repo
    v = owner()
    v.write_identity(root)
    git('tag', '-d', 'alpha-v0.1.0')
    assert v.resolve_identity(root)['versionSource'] == 'git-derived'
    (root / 'new-source').write_text('dirty')
    assert v.resolve_identity(root)['dirty'] is True
    original = v._git
    monkeypatch.setattr(v, '_git', lambda root, *args: None if args[0] == 'status' else original(root, *args))
    assert v.resolve_identity(root)['dirty'] is None


@pytest.mark.parametrize('change', [
    {'schemaVersion': 99}, {'version': '0.1.1'}, {'distance': True},
    {'commit': 'z' * 40}, {'shortCommit': 'bad'}, {'channel': 'stable'},
    {'baseTag': 'alpha-v00.1.0'}, {'distance': -1}, {'dirty': 'false'},
    {'repository': 'other/repo'}, {'updateBranch': 'side'},
])
def test_malformed_stamp_rejected(repo, change):
    root, git = repo
    v = owner()
    stamp = v.resolve_identity(root)
    stamp.update(change)
    assert v.validate_identity(stamp) is None


def test_no_git_ci_sha_is_not_ancestry(tmp_path):
    v = owner()
    result = v.resolve_identity(tmp_path, env={'GITHUB_SHA': 'b' * 40})
    assert result['versionSource'] == 'fallback'
    assert result['commit'] == 'b' * 40
    assert v.runtime_identity(tmp_path)['commit'] is None


def test_setup_build_and_editable_hooks_invoke_real_generator(repo, monkeypatch):
    """Run real build_py copying and editable build_ext without an install."""
    import runpy
    import setuptools
    from setuptools.command.build_py import build_py
    from setuptools.command.build_ext import build_ext
    root, git = repo
    source = ROOT / 'eidolon_cli/eidolon_version.py'
    (root / 'eidolon_cli').mkdir()
    (root / 'eidolon_cli/eidolon_version.py').write_text(source.read_text().replace(
        '437db7394d78a178966fb2ae42792f2978133a9d', git('rev-parse', 'HEAD')))
    (root / 'setup.py').write_bytes((ROOT / 'setup.py').read_bytes())
    git('add', 'eidolon_cli/eidolon_version.py', 'setup.py')
    git('commit', '-m', 'build inputs')
    captured = {}
    monkeypatch.setattr(setuptools, 'setup', lambda **kw: captured.update(kw))
    (root / 'eidolon_cli/__init__.py').write_text('')
    monkeypatch.chdir(root)
    runpy.run_path(str(root / 'setup.py'))
    for name in ('build_py', 'build_ext'):
        dist = setuptools.Distribution({'packages': ['eidolon_cli'],
                                       'package_data': {'eidolon_cli': ['_build_identity.json']}})
        dist.script_name = str(root / 'setup.py')
        command = captured['cmdclass'][name](dist)
        command.ensure_finalized()
        if name == 'build_py': command.build_lib = str(root / 'artifact')
        if name == 'build_ext': command.editable_mode = True
        command.run()
        identity = json.loads((root / 'eidolon_cli/_build_identity.json').read_text())
        assert identity['version'] == owner().RELEASE_VERSION
        if name == 'build_py':
            assert owner().runtime_identity(root / 'artifact')['version'] == owner().RELEASE_VERSION
        (root / 'eidolon_cli/_build_identity.json').unlink()


def test_declared_release_matches_python_package_version():
    import tomllib
    from packaging.version import Version

    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    assert Version(project['version']) == Version(owner().RELEASE_VERSION)
    assert Version(project['version']).is_prerelease

"""Optional pytest remains inside the exact same native isolation boundary."""
from dataclasses import replace
import threading
import time

import pytest

from eidolon_cli.organization_project_runner import (
    ProjectExecutionGrant, _Budget, _prepare_runtime, run_project_tests,
)
from eidolon_cli.organization_project_pytest import (
    PACKAGES, PytestBundleUnavailable, pytest_bundle, valid_pytest_identity,
)
from tests.eidolon_cli.test_organization_project_runner import isolated_linux, live_probe, snapshot  # noqa: F401


GRANT = ProjectExecutionGrant(recipe='python_pytest', timeout_seconds=30)


def test_bundle_uses_only_pinned_packages_and_rejects_linked_or_missing_payloads(tmp_path, monkeypatch):
    from eidolon_cli import organization_project_pytest as bundle
    root = tmp_path / 'packages'
    root.mkdir()
    for name, (version, modules) in PACKAGES.items():
        info = root / f'{name}-{version}.dist-info'
        info.mkdir()
        (info / 'METADATA').write_text(f'Name: {name}\nVersion: {version}\n')
        for module in modules:
            (root / module).mkdir()
            (root / module / '__init__.py').write_text('raise AssertionError("never import on the host")\n')
    (root / 'owner-secret').write_text('not a package')
    (root / 'dangerous.pth').write_text('import untrusted_host_code\n')
    monkeypatch.setattr(bundle.sysconfig, 'get_path', lambda key: str(root))
    files, provenance = pytest_bundle([3, 13, 0], _Budget(None, 5, time.monotonic()))
    assert {relative.parts[0] for _, relative in files} == {m for _, modules in PACKAGES.values() for m in modules}
    assert provenance == [{'name': n, 'version': v} for n, (v, _) in PACKAGES.items()]
    cancel = threading.Event()
    cancel.set()
    from eidolon_cli.organization_project_runner import _Cancelled
    with pytest.raises(_Cancelled):
        pytest_bundle([3, 13, 0], _Budget(cancel, 5, time.monotonic()))
    native = root / 'pytest' / 'native.so'
    native.write_bytes(b'ELF')
    with pytest.raises(PytestBundleUnavailable, match='pure-Python'):
        pytest_bundle([3, 13, 0], _Budget(None, 5, time.monotonic()))
    native.unlink()
    oversized = root / 'pytest' / 'large.py'
    oversized.write_bytes(b'x' * (2 * 1024 * 1024 + 1))
    with pytest.raises(PytestBundleUnavailable, match='bounds'):
        pytest_bundle([3, 13, 0], _Budget(None, 5, time.monotonic()))
    oversized.unlink()
    linked = root / 'pytest' / 'leak.py'
    linked.symlink_to(root / 'owner-secret')
    with pytest.raises(PytestBundleUnavailable, match='Linked'):
        pytest_bundle([3, 13, 0], _Budget(None, 5, time.monotonic()))
    linked.unlink()
    with pytest.raises(PytestBundleUnavailable, match='exceptiongroup'):
        pytest_bundle([3, 10, 0], _Budget(None, 5, time.monotonic()))
    (root / 'pytest-9.1.1.dist-info' / 'METADATA').write_text('Name: pytest\nVersion: 1.0\n')
    with pytest.raises(PytestBundleUnavailable, match='pytest=='):
        pytest_bundle([3, 13, 0], _Budget(None, 5, time.monotonic()))


@pytest.mark.linux_only
def test_real_installed_bundle_is_copied_and_fingerprinted_without_project_imports(tmp_path):
    from eidolon_cli.organization_project_runner import _Unsupported
    try:
        runtime, _, identity = _prepare_runtime(tmp_path, '/usr/bin/python3', '/usr/bin/ldd',
                                               _Budget(None, 30, time.monotonic()), 'python_pytest')
    except _Unsupported as error:
        import os
        if os.environ.get('EIDOLON_REQUIRE_PROJECT_SANDBOX') == '1':
            pytest.fail(str(error))
        pytest.skip('Optional installed bundle unavailable: ' + str(error))
    assert valid_pytest_identity(identity)
    assert not (runtime / 'runner-packages' / 'pytest_asyncio').exists()
    assert not (runtime / 'runner-packages' / 'sitecustomize.py').exists()
    assert not (runtime / 'runner-packages' / 'pytest' / '__init__.py').stat().st_mode & 0o222
    assert not valid_pytest_identity({**identity, 'dependencies': []})
    assert not valid_pytest_identity({**identity, 'stdlibOnly': True})


@pytest.mark.linux_only
@pytest.mark.parametrize('test, status, count', [
    ('import pytest\n@pytest.mark.parametrize("number", [1, 2])\ndef test_answer(number): assert number > 0\n', 'passed', 2),
    ('def test_wrong(): assert 2 + 2 == 5\n', 'failed', 1),
    ('def helper_only(): return 42\n', 'failed', 0),
    ('import pytest\n@pytest.mark.skip(reason="not run")\ndef test_skipped(): assert False\n', 'failed', 1),
    ('import os\ndef test_exit(): os._exit(0)\n', 'failed', 0),
])
def test_real_pytest_pass_failure_skip_and_missing_completion(isolated_linux, test, status, count):
    result = run_project_tests(snapshot(test), GRANT)
    assert result['status'] == status, result
    assert result['runner'] == 'eidolon.isolated-python-pytest'
    assert result['testCount'] == count
    assert result['isolation']['established']
    assert valid_pytest_identity(result['runtime'])


@pytest.mark.linux_only
def test_real_pytest_keeps_host_plugins_network_processes_and_writes_outside_boundary(isolated_linux, tmp_path, monkeypatch):
    secret = tmp_path / 'host-secret'
    secret.write_text('owner data')
    marker = tmp_path / 'host-plugin-ran'
    plugin = tmp_path / 'host_plugin.py'
    plugin.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("ran")\n')
    monkeypatch.setenv('PYTHONPATH', str(tmp_path))
    monkeypatch.setenv('PYTEST_PLUGINS', 'host_plugin')
    monkeypatch.setenv('PYTEST_ADDOPTS', '-p host_plugin')
    monkeypatch.setenv('OWNER_SECRET', 'must not reach project')
    content = f'''import os, pathlib, socket, subprocess, threading, pytest
@pytest.mark.parametrize('path', [{str(secret)!r}, '/etc/passwd', '/root', '/home'])
def test_no_host_paths(path): assert not pathlib.Path(path).exists()
def test_no_host_environment():
    assert 'OWNER_SECRET' not in os.environ
    assert 'PYTEST_PLUGINS' not in os.environ
    assert 'PYTHONPATH' not in os.environ
def test_no_socket():
    with pytest.raises(OSError): socket.socket()
def test_no_process():
    with pytest.raises(OSError): subprocess.run(['/usr/bin/python3', '-c', 'pass'])
def test_no_thread():
    with pytest.raises(RuntimeError): threading.Thread(target=lambda: None).start()
@pytest.mark.parametrize('path', ['/project/root0/test_project.py', '/runner-packages/pytest/__init__.py', '/scratch/new-file'])
def test_no_writes(path):
    with pytest.raises(OSError): pathlib.Path(path).write_text('tamper')
def test_selected_conftest(selected_fixture): assert selected_fixture == 42
'''
    files = snapshot(content) + snapshot('import pytest\n@pytest.fixture\ndef selected_fixture(): return 42\n', 'root0/conftest.py')
    # Selected config remains untrusted data: no arbitrary host/plugin options.
    files += snapshot('[pytest]\naddopts = --unknown-host-option\n', 'root0/pytest.ini')
    result = run_project_tests(files, GRANT)
    assert result['status'] == 'passed', result
    assert result['isolation']['processLimit'] == 1
    assert not marker.exists()
    assert secret.read_text() == 'owner data'


@pytest.mark.linux_only
def test_real_pytest_limits_and_cancellation_are_terminal(isolated_linux):
    noisy = run_project_tests(snapshot('def test_output():\n    print("x" * 100000)\n    assert False\n'),
                              replace(GRANT, output_bytes=1024))
    assert noisy['status'] == 'failed', noisy
    assert noisy['stdoutTruncated'] or noisy['stderrTruncated']
    cancel = threading.Event()
    timer = threading.Timer(2, cancel.set)
    timer.start()
    try:
        stopped = run_project_tests(snapshot('def test_wait():\n    while True: pass\n'), GRANT, cancel)
    finally:
        timer.cancel()
        timer.join()
    assert stopped['status'] == 'cancelled', stopped
    assert stopped['exitCode'] != 0
    assert stopped['durationSeconds'] < 10


@pytest.mark.linux_only
def test_real_pytest_directory_and_pattern_are_owner_selected(isolated_linux):
    files = snapshot('def test_wrong(): assert False\n')
    files += snapshot('def test_right(): assert True\n', 'root0/checks/check_answer.py')
    result = run_project_tests(files, replace(GRANT, test_directory='checks', pattern='check_*.py'))
    assert result['status'] == 'passed', result
    assert result['testCount'] == 1


@pytest.mark.linux_only
@pytest.mark.parametrize('corruption', ['runner', 'dependencies', 'hash', 'stdlib'])
def test_pytest_admission_rejects_other_recipe_or_unbound_bundle(tmp_path, monkeypatch, corruption):
    from eidolon_cli import organization_project_runner as runner
    from tests.eidolon_cli.test_organization_project_security import _reviewed_project, _simulated_terminal_result
    store, _, _, _ = _reviewed_project(tmp_path, recipe='python_pytest')

    def injected(files, grant, cancel):
        result = _simulated_terminal_result(files)
        result['runner'] = 'eidolon.isolated-python-pytest'
        result['runtime'].update(stdlibOnly=False, versionInfo=[3, 13, 0],
            dependencies=[{'name': name, 'version': value[0]} for name, value in PACKAGES.items()],
            dependencyFilesSha256='c' * 64)
        if corruption == 'runner':
            result['runner'] = 'eidolon.isolated-python-unittest'
        elif corruption == 'dependencies':
            result['runtime']['dependencies'][0]['version'] = '0.0.0'
        elif corruption == 'hash':
            result['runtime']['dependencyFilesSha256'] = 'unverified'
        else:
            result['runtime']['stdlibOnly'] = True
        return result

    monkeypatch.setattr(runner, 'run_project_tests', injected)
    claim = store.claim_next()
    store.run_project_stage(claim, threading.Event())
    with pytest.raises(ValueError):
        store.finish(claim, {})
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM project_run_reviews').fetchone()[0] == 0

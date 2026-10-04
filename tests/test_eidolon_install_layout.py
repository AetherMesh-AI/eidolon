"""The public home alias and legacy checkout selection preserve fork isolation."""

import os
from pathlib import Path
import shlex
import subprocess

import pytest


INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"


@pytest.mark.parametrize("home_flag", [None, "--eidolon-home", "--hermes-home"])
def test_shell_home_alias_is_consumed_before_explicit_child_overrides(tmp_path, home_flag):
    canonical = tmp_path / "canonical profile"
    explicit = tmp_path / "explicit profile"
    env = os.environ | {
        "HOME": str(tmp_path),
        "EIDOLON_HOME": str(canonical),
        "HERMES_HOME": str(tmp_path / "compatibility profile"),
    }
    args = " --manifest"
    if home_flag:
        args += f" {home_flag} {shlex.quote(str(explicit))}"
    body = (
        f"source {shlex.quote(str(INSTALLER))}{args} >/dev/null\n"
        "printf '%s\\n' \"$HERMES_HOME\" \"${EIDOLON_HOME-unset}\"\n"
        "HERMES_HOME=child-profile bash -c 'printf \"%s\\n\" \"$HERMES_HOME\"'\n"
    )
    result = subprocess.run(["bash", "-c", body], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [str(explicit if home_flag else canonical), "unset", "child-profile"]


@pytest.mark.parametrize("legacy_is_fork,current_exists", [(False, False), (True, False), (True, True)])
def test_shell_reuses_only_verified_fork_checkout_inside_selected_home(tmp_path, legacy_is_fork, current_exists):
    profile = tmp_path / ".eidolon"
    previous = profile / "hermes-agent"
    current = profile / "eidolon-agent"
    previous.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(previous)], check=True)
    repository = "AetherMesh-AI/Eidolon" if legacy_is_fork else "NousResearch/hermes-agent"
    subprocess.run(["git", "-C", str(previous), "remote", "add", "origin", f"https://github.com/{repository}.git"], check=True)
    if current_exists:
        current.mkdir()
    unrelated = tmp_path / ".hermes" / "hermes-agent"
    unrelated.mkdir(parents=True)
    (unrelated / "untouched").write_text("unrelated Hermes installation")
    env = os.environ | {"HOME": str(tmp_path), "EIDOLON_HOME": str(profile)}
    env.pop("HERMES_INSTALL_DIR", None)
    body = (
        f"source {shlex.quote(str(INSTALLER))} --manifest >/dev/null\n"
        "detect_os >/dev/null\nresolve_install_layout >/dev/null\n"
        "printf '%s\\n' \"$INSTALL_DIR\"\n"
    )
    result = subprocess.run(["bash", "-c", body], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    selected = Path(result.stdout.strip())
    if legacy_is_fork and not current_exists:
        assert selected == previous
    else:
        assert selected.name == "eidolon-agent"
        assert selected != previous
    assert (unrelated / "untouched").read_text() == "unrelated Hermes installation"


def test_current_installer_stages_migrate_old_checkout_and_keep_profile_data(tmp_path):
    """Real Git, uv, wheel metadata, CLI imports, and shell stages cross the rename."""
    import json
    import shutil
    import sys

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required for the isolated installer migration")
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    profile = tmp_path / ".eidolon"
    selected = profile / "hermes-agent"
    env = os.environ | {
        "HOME": str(tmp_path),
        "EIDOLON_HOME": str(profile),
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "UV_OFFLINE": "1",
        "UV_PYTHON_DOWNLOADS": "never",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }

    def run(argv, cwd=upstream):
        result = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    # A dependency-free PEP 517 fixture builds real editable wheels offline.
    (upstream / "fixture_backend.py").write_text('''import base64, csv, hashlib, io, pathlib, tomllib, zipfile

def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    root = pathlib.Path.cwd()
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    name = project["name"].replace("-", "_")
    info = name + "-1.0.0.dist-info"
    command = "hermes" if name == "hermes_agent" else "eidolon"
    module = command + "_cli.main"
    files = {
        name + ".pth": str(root) + "\\n",
        info + "/METADATA": "Metadata-Version: 2.3\\nName: " + project["name"] + "\\nVersion: 1.0.0\\nProvides-Extra: all\\n",
        info + "/WHEEL": "Wheel-Version: 1.0\\nGenerator: installer-fixture\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n",
        info + "/entry_points.txt": "[console_scripts]\\n" + command + " = " + module + ":main\\n",
    }
    records = io.StringIO()
    writer = csv.writer(records)
    wheel = name + "-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(pathlib.Path(wheel_directory) / wheel, "w") as archive:
        for path, text in files.items():
            payload = text.encode()
            archive.writestr(path, payload)
            digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()
            writer.writerow([path, "sha256=" + digest, len(payload)])
        writer.writerow([info + "/RECORD", "", ""])
        archive.writestr(info + "/RECORD", records.getvalue())
    return wheel

build_wheel = build_editable
''')
    def write_project(name, module):
        (upstream / "pyproject.toml").write_text(f'''[build-system]
requires = []
build-backend = "fixture_backend"
backend-path = ["."]
[project]
name = "{name}"
version = "1.0.0"
requires-python = ">=3.11"
[project.optional-dependencies]
all = []
''')
        package = upstream / module
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "main.py").write_text(f'def main():\n    print("{module} runtime")\n')

    (upstream / "package.json").write_text(json.dumps({"repository": {"url": "https://github.com/AetherMesh-AI/Eidolon.git"}}))
    (upstream / ".gitignore").write_text("venv/\n__pycache__/\n.eidolon-bootstrap-complete\n")
    write_project("hermes-agent", "hermes_cli")
    run(["git", "init", "-q", "-b", "main"])
    run(["git", "config", "user.name", "Installer fixture"])
    run(["git", "config", "user.email", "installer@example.invalid"])
    run(["git", "add", "."])
    run(["git", "commit", "-qm", "previous fork namespace"])
    run(["git", "clone", "-q", str(upstream), str(selected)])
    run([uv, "venv", str(selected / "venv"), "--python", sys.executable])
    python = selected / "venv/bin/python"
    run([uv, "pip", "install", "--python", str(python), "-e", str(selected)])
    assert "hermes_cli runtime" in run([str(selected / "venv/bin/hermes")], selected).stdout
    assert run([str(python), "-c", "from importlib.metadata import version; print(version('hermes-agent'))"], selected).stdout.strip() == "1.0.0"

    protected = {
        "config.yaml": 'model:\n  provider: custom\n  base_url: https://provider.example.invalid/v1\n',
        ".env": "CUSTOM_PROVIDER_TOKEN=offline-fixture\n",
        "auth.json": '{"providers":{"custom":{"token":"offline-fixture"}}}\n',
        "SOUL.md": "Keep this custom persona with Hermes references.\n",
        "sessions/preserved.json": '{"messages":["keep me"]}\n',
    }
    for relative, value in protected.items():
        path = profile / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    unrelated = tmp_path / ".hermes" / "hermes-agent/venv/keep"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("unrelated installation")

    shutil.rmtree(upstream / "hermes_cli")
    write_project("eidolon-agent", "eidolon_cli")
    (upstream / "eidolon").write_text("from eidolon_cli.main import main\nmain()\n")
    run([uv, "lock", "--python", sys.executable])
    run(["git", "add", "-A"])
    run(["git", "commit", "-qm", "canonical fork namespace"])
    command_dir = tmp_path / ".local/bin"
    env["PATH"] = str(command_dir) + os.pathsep + env["PATH"]
    env["HERMES_INSTALL_DIR"] = str(selected)
    body = (
        f"source {shlex.quote(str(INSTALLER))} --manifest --no-skills >/dev/null\n"
        "detect_os\nresolve_install_layout\n"
        f"UV_CMD={shlex.quote(uv)}\nPYTHON_VERSION={shlex.quote(sys.executable)}\n"
        "clone_repo\nsetup_venv\n"
        'run_locked_uv_sync "$INSTALL_DIR/venv"\n'
        "setup_path\ncopy_config_templates\nwrite_bootstrap_marker\n"
    )
    run(["bash", "-c", body], tmp_path)
    assert "eidolon_cli runtime" in run([str(command_dir / "eidolon")], tmp_path).stdout
    check = "from importlib.metadata import distributions; names={d.metadata['Name'] for d in distributions()}; assert 'eidolon-agent' in names and 'hermes-agent' not in names; import eidolon_cli.main"
    run([str(python), "-c", check], tmp_path)
    assert not (selected / "venv/bin/hermes").exists()
    assert not list((selected / "venv").rglob("hermes_agent.pth"))
    assert (selected / ".eidolon-bootstrap-complete").is_file()
    for relative, value in protected.items():
        assert (profile / relative).read_text() == value
    assert unrelated.read_text() == "unrelated installation"

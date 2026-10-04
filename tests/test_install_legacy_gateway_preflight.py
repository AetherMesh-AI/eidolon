"""The downloaded installer refuses to replace a legacy gateway's runtime."""

import json
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys

import pytest

INSTALLER = Path(__file__).resolve().parents[1] / "scripts/install.sh"


def _fixture(tmp_path):
    home = tmp_path / ".eidolon"
    root = home / "hermes-agent"
    (root / "hermes_cli").mkdir(parents=True)
    (root / "hermes_cli/main.py").write_text("old source must survive a refusal\n")
    (root / "venv/bin").mkdir(parents=True)
    (root / "venv/bin/python").symlink_to(sys.executable)
    (root / "venv/keep").write_text("old virtual environment\n")
    (home / "config.yaml").write_text("model:\n  provider: custom\n")
    (home / ".env").write_text("CUSTOM_PROVIDER_TOKEN=offline-test\n")
    (root / "package.json").write_text(json.dumps({"repository": {"url": "https://github.com/AetherMesh-AI/Eidolon.git"}}))
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return home, root


def _run(tmp_path, home, root, action="preflight_legacy_gateway_services", extra_env=None):
    env = os.environ | {"HOME": str(tmp_path), "EIDOLON_HOME": str(home), "HERMES_INSTALL_DIR": str(root)}
    if extra_env:
        env.update(extra_env)
    body = f"source {shlex.quote(str(INSTALLER))} --manifest >/dev/null\n{action}\n"
    return subprocess.run(["bash", "-c", body], env=env, text=True, capture_output=True, timeout=30)


def _unit(home, root):
    return f'''[Unit]
Description=Hermes Gateway
[Service]
ExecStart={root}/venv/bin/python -m hermes_cli.main gateway run
Environment="HERMES_HOME={home}"
Restart=always
'''


def _plist(home, root):
    return plistlib.dumps({
        "Label": "ai.hermes.gateway-work",
        "ProgramArguments": [str(root / "venv/bin/python"), "-m", "hermes_cli.stderr_timestamp", "--error-log", str(home / "logs/gateway.error.log"), "--", str(root / "venv/bin/python"), "-m", "hermes_cli.main", "gateway", "run"],
        "EnvironmentVariables": {"HERMES_HOME": str(home)},
        "KeepAlive": True,
    })


@pytest.mark.parametrize("kind", ["systemd", "launchd"])
@pytest.mark.parametrize("profile", [False, True])
@pytest.mark.parametrize("action", ["clone_repo", "setup_venv"])
def test_legacy_gateway_blocks_before_checkout_or_venv_swap(tmp_path, kind, profile, action):
    home, root = _fixture(tmp_path)
    service_home = home / "profiles/work" if profile else home
    service_home.mkdir(parents=True, exist_ok=True)
    if kind == "systemd":
        definition = tmp_path / ".config/systemd/user/hermes-gateway-work.service"
        content = _unit(service_home, root).encode()
    else:
        definition = tmp_path / "Library/LaunchAgents/ai.hermes.gateway-work.plist"
        content = _plist(service_home, root)
    definition.parent.mkdir(parents=True)
    definition.write_bytes(content)
    result = _run(tmp_path, home, root, action)
    assert result.returncode != 0
    assert "paused before changing" in result.stderr
    assert str(service_home) in result.stderr
    expected_stop = "disable --now hermes-gateway-work.service" if kind == "systemd" else "launchctl bootout"
    assert expected_stop in result.stderr
    assert str(definition) + ".pre-eidolon" in result.stderr
    assert "gateway install" in result.stderr and "gateway start" in result.stderr
    assert (root / "hermes_cli/main.py").read_text() == "old source must survive a refusal\n"
    assert (root / "venv/keep").read_text() == "old virtual environment\n"
    assert (home / "config.yaml").read_text() == "model:\n  provider: custom\n"
    assert (home / ".env").read_text() == "CUSTOM_PROVIDER_TOKEN=offline-test\n"
    assert definition.read_bytes() == content
    # Retiring the exact supervisor definition permits the operator's rerun.
    definition.rename(str(definition) + ".pre-eidolon")
    assert _run(tmp_path, home, root).returncode == 0


@pytest.mark.parametrize("kind", ["systemd", "launchd"])
@pytest.mark.parametrize("foreign_part", ["home", "interpreter", "both"])
def test_unrelated_hermes_supervisors_are_untouched(tmp_path, kind, foreign_part):
    home, root = _fixture(tmp_path)
    service_home = tmp_path / ".hermes" if foreign_part in ("home", "both") else home
    runtime = tmp_path / ".hermes/hermes-agent" if foreign_part in ("interpreter", "both") else root
    if kind == "systemd":
        definition = tmp_path / ".config/systemd/user/hermes-gateway.service"
        content = _unit(service_home, runtime).encode()
    else:
        definition = tmp_path / "Library/LaunchAgents/ai.hermes.gateway-work.plist"
        content = _plist(service_home, runtime)
    definition.parent.mkdir(parents=True)
    definition.write_bytes(content)
    result = _run(tmp_path, home, root)
    assert result.returncode == 0, result.stderr
    assert definition.read_bytes() == content
    assert (root / "venv/keep").read_text() == "old virtual environment\n"


def test_running_systemd_gateway_without_unit_file_still_blocks(tmp_path):
    home, root = _fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    systemctl = bin_dir / "systemctl"
    systemctl.write_text("#!/bin/sh\ncase \"$*\" in\n*list-units*) echo 'hermes-gateway.service loaded active running gateway' ;;\n*show*) cat <<'DETAILS'\n" +
        f"ExecStart={{ path={root}/venv/bin/python ; argv[]={root}/venv/bin/python -m hermes_cli.main gateway run ; }}\nEnvironment=HERMES_HOME={home}\nFragmentPath=\nActiveState=active\n" +
        "DETAILS\n;;\nesac\n")
    systemctl.chmod(0o755)
    result = _run(tmp_path, home, root, extra_env={"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]})
    assert result.returncode != 0
    assert "disable --now hermes-gateway.service" in result.stderr
    assert (root / "venv/keep").exists()


def test_loaded_launchd_gateway_without_plist_still_blocks(tmp_path):
    home, root = _fixture(tmp_path)
    # Exercise the embedded inspector's macOS branch with actual cached-job
    # output; no daemon or service registration is touched by this fixture.
    inspector = root / "venv/bin/python"
    inspector.unlink()
    code = "import sys; sys.platform='darwin'; sys.argv=sys.argv[2:]; exec(sys.stdin.read())"
    inspector.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -I -c {shlex.quote(code)} \"$@\"\n")
    inspector.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "launchctl-calls"
    launchctl = bin_dir / "launchctl"
    launchctl.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n" +
        "case \"$2\" in\n*/ai.hermes.gateway-work) cat <<'DETAILS'\n" +
        f"gui/{os.getuid()}/ai.hermes.gateway-work = {{\n    state = running\n    arguments = {{\n        {root}/venv/bin/python\n        -m\n        hermes_cli.main\n        gateway\n        run\n    }}\n    environment = {{\n        HERMES_HOME => {home}\n    }}\n}}\n" +
        "DETAILS\n;;\n*) echo '123 0 ai.hermes.gateway-work' ;;\nesac\n")
    launchctl.chmod(0o755)
    result = _run(tmp_path, home, root, extra_env={"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]})
    assert result.returncode != 0
    assert f"launchctl bootout gui/{os.getuid()}/ai.hermes.gateway-work" in result.stderr
    assert all(call.startswith("print ") for call in calls.read_text().splitlines())
    assert (root / "venv/keep").exists()


def test_loaded_gateway_inspection_failure_does_not_allow_mutation(tmp_path):
    home, root = _fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    systemctl = bin_dir / "systemctl"
    systemctl.write_text("#!/bin/sh\ncase \"$*\" in\n*list-units*) echo 'hermes-gateway.service loaded active running gateway' ;;\n*show*) exit 1 ;;\nesac\n")
    systemctl.chmod(0o755)
    result = _run(tmp_path, home, root, "clone_repo", extra_env={"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]})
    assert result.returncode != 0
    assert "Cannot inspect loaded gateway hermes-gateway.service" in result.stderr
    assert (root / "venv/keep").exists()
    assert (root / "hermes_cli/main.py").exists()


def test_loaded_launchd_inspection_failure_does_not_allow_mutation(tmp_path):
    home, root = _fixture(tmp_path)
    inspector = root / "venv/bin/python"
    inspector.unlink()
    code = "import sys; sys.platform='darwin'; sys.argv=sys.argv[2:]; exec(sys.stdin.read())"
    inspector.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -I -c {shlex.quote(code)} \"$@\"\n")
    inspector.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launchctl = bin_dir / "launchctl"
    launchctl.write_text("#!/bin/sh\ncase \"$2\" in\n*/ai.hermes.gateway-work) exit 1 ;;\n*) echo '123 0 ai.hermes.gateway-work' ;;\nesac\n")
    launchctl.chmod(0o755)
    result = _run(tmp_path, home, root, "clone_repo", extra_env={"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]})
    assert result.returncode != 0
    assert f"Cannot inspect loaded gateway gui/{os.getuid()}/ai.hermes.gateway-work" in result.stderr
    assert (root / "venv/keep").exists()
    assert (root / "hermes_cli/main.py").exists()


@pytest.mark.parametrize("alias_in_selection", [False, True])
@pytest.mark.parametrize("kind", ["systemd", "launchd"])
def test_checkout_directory_alias_cannot_bypass_gateway_refusal(tmp_path, alias_in_selection, kind):
    home, root = _fixture(tmp_path)
    alias = tmp_path / "checkout-alias"
    alias.symlink_to(root, target_is_directory=True)
    selected_root, service_root = (alias, root) if alias_in_selection else (root, alias)
    if kind == "systemd":
        definition = tmp_path / ".config/systemd/user/hermes-gateway.service"
        content = _unit(home, service_root).encode()
    else:
        definition = tmp_path / "Library/LaunchAgents/ai.hermes.gateway-work.plist"
        content = _plist(home, service_root)
    definition.parent.mkdir(parents=True)
    definition.write_bytes(content)
    result = _run(tmp_path, home, selected_root, "clone_repo")
    assert result.returncode != 0
    assert "paused before changing" in result.stderr
    assert (root / "venv/keep").exists()
    assert (root / "hermes_cli/main.py").exists()
    assert definition.read_bytes() == content

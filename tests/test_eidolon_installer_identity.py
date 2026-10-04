"""Installer identity must agree with the runtime without replacing user content."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest

from eidolon_cli.default_soul import DEFAULT_SOUL_MD


INSTALL_SH = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"


def _run_installer_functions(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = os.environ | {
        "HOME": str(home),
        "HERMES_HOME": str(home / "profile"),
        "HERMES_INSTALL_DIR": str(tmp_path / "selected install"),
    }
    # --manifest only defines functions and prints local stage data. Never run main.
    harness = f"source {shlex.quote(str(INSTALL_SH))} --manifest >/dev/null\n{body}"
    return subprocess.run(
        ["bash", "-c", harness], env=env, text=True, capture_output=True, timeout=20
    )


@pytest.mark.parametrize("custom_soul", [None, "Keep my custom persona, including Hermes references.\n"])
def test_shell_installer_seeds_runtime_persona_and_preserves_custom_soul(
    tmp_path: Path, custom_soul: str | None
) -> None:
    profile = tmp_path / "home" / "profile"
    profile.mkdir(parents=True)
    soul = profile / "SOUL.md"
    if custom_soul is not None:
        soul.write_text(custom_soul, encoding="utf-8")

    completed = _run_installer_functions(
        tmp_path,
        "NO_SKILLS=true\n"
        "configure_browser_env_from_system_browser() { :; }\n"
        "copy_config_templates\n",
    )

    assert completed.returncode == 0, completed.stderr
    expected = custom_soul if custom_soul is not None else DEFAULT_SOUL_MD + "\n"
    assert soul.read_text(encoding="utf-8") == expected
    assert f"Configuration directory ready: {profile}/" in completed.stdout


@pytest.mark.linux_only
@pytest.mark.parametrize(
    ("selected_files", "selected_dirs", "outside_files", "expected_path"),
    [
        (("native/Eidolon",), (), (), "native/Eidolon"),
        (
            ("native/Eidolon", "native/Hermes", "native/hermes"),
            (), (), "native/Eidolon",
        ),
        (("native/Hermes",), (), (), "native/Hermes"),
        (("native/hermes",), (), (), "native/hermes"),
        (
            (), (),
            ("native/Eidolon", "native/Hermes", "native/hermes"),
            None,
        ),
        (
            ("native/Hermes",), ("native/Eidolon",), (),
            "native/Hermes",
        ),
        ((), ("native/Eidolon",), (), None),
        (("foreign/Eidolon",), (), (), None),
        (
            ("foreign/Eidolon", "native/Hermes"), (), (),
            "native/Hermes",
        ),
    ],
)
def test_desktop_stage_prefers_native_architecture_then_current_identity(
    tmp_path: Path,
    selected_files: tuple[str, ...],
    selected_dirs: tuple[str, ...],
    outside_files: tuple[str, ...],
    expected_path: str | None,
) -> None:
    # These are host-native stage tests. "foreign" is just an on-disk layout;
    # no OS or architecture probe is replaced, including on native ARM runners.
    layouts = {
        "x86_64": ("linux-unpacked", "linux-arm64-unpacked"),
        "aarch64": ("linux-arm64-unpacked", "linux-unpacked"),
        "arm64": ("linux-arm64-unpacked", "linux-unpacked"),
    }
    native, foreign = layouts[os.uname().machine.lower()]

    def artifact_path(relative_path: str) -> str:
        return relative_path.replace("native/", f"{native}/").replace("foreign/", f"{foreign}/")

    selected = tmp_path / "selected install"
    for root, files in ((selected, selected_files), (tmp_path / "other install", outside_files)):
        desktop = root / "apps" / "desktop"
        release = desktop / "release"
        release.mkdir(parents=True)
        (desktop / "package.json").write_text("{}\n", encoding="utf-8")
        for relative_path in files:
            executable = release / artifact_path(relative_path)
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
    for relative_path in selected_dirs:
        directory = selected / "apps" / "desktop" / "release" / artifact_path(relative_path)
        directory.mkdir(parents=True)
        directory.chmod(0o755)

    completed = _run_installer_functions(
        tmp_path,
        "OS=linux\n"
        "check_node() { :; }\n"
        "npm() { :; }\n"
        # Dependency installation and packaging are external boundaries. The real
        # desktop stage still checks its selected checkout's generated artifacts.
        "run_with_timeout() { :; }\n"
        "restore_dirty_lockfiles() { :; }\n"
        "install_desktop\n",
    )

    if expected_path is None:
        assert completed.returncode != 0
        assert "no app was found" in completed.stdout
    else:
        assert completed.returncode == 0, completed.stderr
        expected = selected / "apps" / "desktop" / "release" / artifact_path(expected_path)
        assert f"Desktop app built: {expected}" in completed.stdout

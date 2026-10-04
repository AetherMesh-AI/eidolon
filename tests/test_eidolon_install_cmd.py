"""Run the Windows wrapper against an inert PowerShell recorder, never an installer."""

import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.windows_only
def test_cmd_bootstrap_invokes_only_the_eidolon_installer(tmp_path, monkeypatch):
    command_shell = os.environ["COMSPEC"]
    trace = tmp_path / "powershell-arguments.txt"
    recorder = tmp_path / "powershell.cmd"
    recorder.write_text(
        '@echo off\n> "%EIDOLON_TEST_ARGUMENTS%" echo %*\nexit /b 0\n',
        encoding="ascii",
    )
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("EIDOLON_TEST_ARGUMENTS", str(trace))
    wrapper = Path(__file__).resolve().parents[1] / "scripts" / "install.cmd"

    result = subprocess.run(
        [command_shell, "/d", "/c", str(wrapper)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    arguments = trace.read_text(encoding="ascii")
    assert arguments.count("https://") == 1
    assert "https://raw.githubusercontent.com/AetherMesh-AI/Eidolon/main/scripts/install.ps1" in arguments
    assert "NousResearch" not in arguments
    assert "nousresearch.com" not in arguments

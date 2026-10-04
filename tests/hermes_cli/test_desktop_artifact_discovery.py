"""Packaged-app discovery keeps brand, architecture, and release-root boundaries."""

import os
import platform as host_platform
import struct
import sys
from pathlib import Path

import pytest

from hermes_cli import main_desktop


def _artifact(path: Path, *, mtime: int, machine: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if machine is None:
        path.write_bytes(b"packaged executable")
    else:
        data = bytearray(0x400)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 0x3C, 0x80)
        data[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<HHIIIHH", data, 0x84, machine, 1, 0, 0, 0, 0, 0x0002)
        struct.pack_into("<II", data, 0x98 + 16, 0x200, 0x200)
        path.write_bytes(data)
    os.utime(path, (mtime, mtime))
    return path


@pytest.mark.parametrize(
    "platform, architecture, preferred, legacy",
    [
        ("darwin", "x86_64", "mac/Eidolon.app/Contents/MacOS/Eidolon", "mac/Hermes.app/Contents/MacOS/Hermes"),
        ("darwin", "arm64", "mac-arm64/Eidolon.app/Contents/MacOS/Eidolon", "mac-arm64/Hermes.app/Contents/MacOS/Hermes"),
        ("win32", "AMD64", "win-unpacked/Eidolon.exe", "win-unpacked/Hermes.exe"),
        ("win32", "x86", "win-ia32-unpacked/Eidolon.exe", "win-ia32-unpacked/Hermes.exe"),
        ("win32", "ARM64", "win-arm64-unpacked/Eidolon.exe", "win-arm64-unpacked/Hermes.exe"),
        ("linux", "x86_64", "linux-unpacked/Eidolon", "linux-unpacked/hermes"),
        ("linux", "aarch64", "linux-arm64-unpacked/Eidolon", "linux-arm64-unpacked/Hermes"),
        ("linux", "x86_64", "linux-unpacked/eidolon", "linux-unpacked/Hermes"),
    ],
)
def test_discovery_prefers_brand_then_legacy_only_within_requested_release(
    tmp_path, monkeypatch, platform, architecture, preferred, legacy
):
    release = tmp_path / "checkout/apps/desktop/.staging-test"
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HERMES_HOME", str(home / ".hermes"))
    # An unrelated install and the live release must never fill an empty staging directory.
    _artifact(home / ".hermes/hermes-agent/apps/desktop/release" / legacy, mtime=300)
    _artifact(release.parent / "release" / preferred, mtime=300)
    kwargs = {"platform": platform, "architecture": architecture}
    if platform == sys.platform and architecture.casefold() == host_platform.machine().casefold():
        kwargs = {}
    assert main_desktop._desktop_packaged_executable_in(release, **kwargs) is None

    fallback = _artifact(release / legacy, mtime=200)
    assert main_desktop._desktop_packaged_executable_in(release, **kwargs) == fallback
    branded = _artifact(release / preferred, mtime=100)
    assert main_desktop._desktop_packaged_executable_in(release, **kwargs) == branded
    branded.unlink()
    branded.mkdir()
    assert main_desktop._desktop_packaged_executable_in(release, **kwargs) == fallback
    fallback.unlink()
    assert main_desktop._desktop_packaged_executable_in(release, **kwargs) is None


@pytest.mark.parametrize(
    "artifacts, selected",
    [
        # Loadability outranks branding and mtime across coexisting unpacked trees.
        ([("win-unpacked/Hermes.exe", 0x8664, 100), ("win-arm64-unpacked/Eidolon.exe", 0xAA64, 200)], 0),
        # Among loadable artifacts, a newer legacy build cannot displace Eidolon.
        ([("win-unpacked/Eidolon.exe", 0x8664, 100), ("win-ia32-unpacked/Hermes.exe", 0x014C, 200)], 0),
        # Both x64 and x86 remain eligible, preserving the existing newest-build rule.
        ([("win-unpacked/Eidolon.exe", 0x8664, 100), ("win-ia32-unpacked/Eidolon.exe", 0x014C, 200)], 1),
        # With no matching PE, keep discovery's mtime fallback for later integrity checks.
        ([("win-unpacked/Eidolon.exe", None, 100), ("win-arm64-unpacked/Eidolon.exe", 0xAA64, 200)], 1),
        ([("win-unpacked/Hermes.exe", None, 100), ("win-arm64-unpacked/Hermes.exe", None, 200)], 1),
    ],
)
def test_windows_discovery_preserves_loadable_architecture_and_mtime_selection(
    tmp_path, monkeypatch, artifacts, selected
):
    # Platform is path-layout data; the interpreter and host APIs stay on the real OS.
    monkeypatch.setattr(main_desktop, "_expected_windows_pe_machines", lambda: {0x8664, 0x014C})
    paths = [_artifact(tmp_path / path, machine=machine, mtime=mtime) for path, machine, mtime in artifacts]
    assert main_desktop._desktop_packaged_executable_in(tmp_path, platform="win32") == paths[selected]


@pytest.mark.parametrize(
    "platform, architecture, native, foreign, compatible",
    [
        ("linux", "x86_64", "linux-unpacked", "linux-arm64-unpacked", "linux-unpacked"),
        ("linux", "aarch64", "linux-arm64-unpacked", "linux-unpacked", "linux-arm64-unpacked"),
        ("linux", "i686", "linux-ia32-unpacked", "linux-unpacked", "linux-ia32-unpacked"),
        ("darwin", "x86_64", "mac", "mac-arm64", "mac"),
        ("darwin", "arm64", "mac-arm64", "mac", "mac-arm64"),
        ("darwin", "x86_64", "mac", "mac-arm64", "mac-universal"),
        ("darwin", "arm64", "mac-arm64", "mac", "mac-universal"),
    ],
)
def test_unix_discovery_checks_architecture_before_brand_and_mtime(
    tmp_path, platform, architecture, native, foreign, compatible
):
    current = "Eidolon.app/Contents/MacOS/Eidolon" if platform == "darwin" else "Eidolon"
    legacy = "Hermes.app/Contents/MacOS/Hermes" if platform == "darwin" else "hermes"
    kwargs = {"platform": platform, "architecture": architecture}
    if platform == sys.platform and architecture.casefold() == host_platform.machine().casefold():
        kwargs = {}
    _artifact(tmp_path / foreign / current, mtime=300)
    assert main_desktop._desktop_packaged_executable_in(tmp_path, **kwargs) is None
    previous = _artifact(tmp_path / native / legacy, mtime=200)
    assert main_desktop._desktop_packaged_executable_in(tmp_path, **kwargs) == previous
    preferred = _artifact(tmp_path / compatible / current, mtime=100)
    assert main_desktop._desktop_packaged_executable_in(tmp_path, **kwargs) == preferred

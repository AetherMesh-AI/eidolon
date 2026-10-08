"""Native signing must never remove a downloaded app's security provenance."""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

import pytest

from eidolon_cli import main_desktop


@pytest.mark.macos_only
@pytest.mark.skipif(sys.platform != "darwin", reason="Requires real macOS xattrs and codesign")
def test_native_relaunch_fixup_preserves_quarantine_and_nested_metadata(tmp_path, monkeypatch):
    """Real native tools and real attributes; this fixture is never launched."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    desktop = tmp_path / "desktop"
    app = desktop / "release" / "mac-universal" / "Eidolon.app"
    executable = app / "Contents" / "MacOS" / "Eidolon"
    executable.parent.mkdir(parents=True)
    # Compile a tiny Mach-O solely to give codesign a real native bundle.
    program = tmp_path / "fixture.c"
    program.write_text("int main(void) { return 0; }\n")
    subprocess.run(["/usr/bin/clang", str(program), "-o", str(executable)], check=True)
    with (app / "Contents" / "Info.plist").open("wb") as stream:
        plistlib.dump({"CFBundleExecutable": "Eidolon", "CFBundleIdentifier": "invalid.example.eidolon-quarantine-fixture",
                      "CFBundleName": "Eidolon", "CFBundleVersion": "1", "CFBundlePackageType": "APPL"}, stream)
    entitlements = desktop / "electron"
    entitlements.mkdir()
    real_desktop = Path(__file__).resolve().parents[2] / "apps" / "desktop"
    for name in ("entitlements.mac.plist", "entitlements.mac.inherit.plist"):
        shutil.copyfile(real_desktop / "electron" / name, entitlements / name)
    resource = app / "Contents" / "Resources" / "proof.txt"
    resource.parent.mkdir()
    resource.write_text("This application must never run during the test.\n")
    quarantine = b"0081;00000000;EidolonCI;00000000-0000-0000-0000-000000000000"
    attributes = [(app, "com.apple.quarantine", quarantine),
                  (resource, "com.apple.quarantine", quarantine),
                  (resource, "org.eidolon.test.provenance", b"preserve nested metadata")]
    for target, attribute, value in attributes:
        os.setxattr(target, attribute, value)
    assert main_desktop._desktop_macos_relaunchable_fixup(desktop, publisher_signing_configured=False)
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    for target, attribute, value in attributes:
        assert os.getxattr(target, attribute) == value

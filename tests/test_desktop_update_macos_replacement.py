"""Real macOS rename authorization must govern the desktop install outcome."""

import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time
import uuid

import pytest

from eidolon_cli import eidolon_version as version


def build_stamp(commit, dirty=False):
    return {**version.fallback(commit, dirty), 'versionSource': 'git-derived',
            'baseTag': 'alpha-v0.1.0', 'baseCommit': version.ANCHOR_COMMIT, 'distance': 1}


@pytest.mark.macos_only
@pytest.mark.parametrize("deny_replacement,lifetime", [(False, 5), (True, 5), (False, 0)])
def test_macos_replacement_keeps_denied_app_and_reports_manual_install(tmp_path, deny_replacement, lifetime):
    if os.geteuid() == 0:
        pytest.skip("The replacement must exercise an ordinary user's authorization")
    install = tmp_path / "checkout"
    home = tmp_path / "profile"
    bin_dir = install / "venv/bin"
    bin_dir.mkdir(parents=True)
    home.mkdir()
    subprocess.run(['git', 'init', str(install)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(install), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '--allow-empty', '-m', 'Update target'], check=True, capture_output=True)
    expected_commit = subprocess.check_output(['git', '-C', str(install), 'rev-parse', 'HEAD'], text=True).strip()
    state = home / "profile-data"
    state.write_bytes(b"existing user state")
    (bin_dir / "python3").symlink_to(sys.executable)
    cli = bin_dir / "eidolon"
    cli.write_text('#!/bin/bash\ncase "$*" in *--help*) echo --keep-stash ;; esac\nexit 0\n')
    cli.chmod(0o755)
    launch_receipt = tmp_path / "launched-generation"
    application_id = f"com.aethermesh.eidolon.replacement-test-{uuid.uuid4().hex}"

    def bundle(path, generation):
        executable = path / "Contents/MacOS/Eidolon"
        executable.parent.mkdir(parents=True)
        source = tmp_path / f"{generation}.m"
        source.write_text(
            '#import <AppKit/AppKit.h>\n#include <stdio.h>\n'
            '@interface ProbeDelegate : NSObject <NSApplicationDelegate> @end\n'
            '@implementation ProbeDelegate\n'
            '- (void)applicationDidFinishLaunching:(NSNotification *)note { FILE *f = fopen('
            + json.dumps(str(launch_receipt)) + ', "w"); if (f) { fputs('
            + json.dumps(generation) + ', f); fclose(f); } '
            + f'[self performSelector:@selector(quit) withObject:nil afterDelay:{lifetime}]; }}\n'
            '- (void)quit { [NSApp terminate:nil]; }\n@end\n'
            'int main(void) { @autoreleasepool { [NSApplication sharedApplication]; '
            'ProbeDelegate *delegate = [ProbeDelegate new]; [NSApp setDelegate:delegate]; '
            '[NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory]; [NSApp run]; } return 0; }\n'
        )
        subprocess.run(["/usr/bin/clang", "-fobjc-arc", "-framework", "AppKit", str(source),
                        "-o", str(executable)], check=True, capture_output=True)
        with (path / "Contents/Info.plist").open("wb") as stream:
            plistlib.dump({"CFBundleExecutable": "Eidolon", "CFBundleIdentifier": application_id,
                          "CFBundleName": "Eidolon", "CFBundlePackageType": "APPL",
                          "CFBundleVersion": generation}, stream)
        resources = path / 'Contents/Resources'
        resources.mkdir()
        (resources / 'install-stamp.json').write_text(json.dumps(build_stamp(expected_commit if generation == 'new' else 'a' * 40)))
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(path)], check=True, capture_output=True)
        return executable

    target = tmp_path / "Applications/Eidolon.app"
    old_executable = bundle(target, "old")
    arch = "mac-arm64" if os.uname().machine == "arm64" else "mac"
    rebuilt = install / f"apps/desktop/release/{arch}/Eidolon.app"
    new_executable = bundle(rebuilt, "new")
    old_bytes = old_executable.read_bytes()
    old_info = (target / "Contents/Info.plist").read_bytes()
    metadata = target.stat()
    username = subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()
    acl = f"user:{username} deny delete"
    assert target.resolve().is_relative_to(tmp_path.resolve()) and not target.is_symlink()
    assert target.parent.resolve().is_relative_to(tmp_path.resolve())
    if deny_replacement:
        subprocess.run(["/bin/chmod", "+a", acl, str(target)], check=True)
    try:
        before_acl = subprocess.check_output(["/bin/ls", "-ldeO@", str(target)], text=True)
        xattrs = subprocess.check_output(["/usr/bin/xattr", "-lx", str(target)])
        # The exact reported shape: a new sibling can be created while the old
        # bundle cannot be renamed. No mocked mv and no faked host platform.
        sibling = target.parent / "writability-proof"
        sibling.write_text("parent accepts new files")
        sibling.unlink()
        env = {**os.environ, "HERMES_HOME": str(home), "HOME": str(tmp_path),
               "TMPDIR": str(tmp_path), "HERMES_UPDATE_SHIM_GRACE_SECONDS": "0"}
        env.pop("EIDOLON_HOME", None)
        result = subprocess.run(
            ["/bin/bash", str(Path(__file__).resolve().parents[1] / "scripts/desktop-update/posix.sh"),
             "--daemonized", "--no-ui", "--install-root", str(install), "--relaunch-target", str(target)],
            env=env, capture_output=True, text=True, timeout=60,
        )
        receipt = json.loads((home / ".eidolon-update-result.json").read_text())
        log = (home / "logs/desktop-update-handoff.log").read_text()
        assert receipt["expected_source_commit"] == expected_commit
        assert receipt["ok"] is (not deny_replacement), log + result.stderr
        assert result.returncode == receipt["exit_code"] == (7 if deny_replacement else 0)
        assert receipt["manual"] is (deny_replacement or lifetime == 0)
        assert state.read_bytes() == b"existing user state"
        assert not (home / ".eidolon-update-in-progress").exists()
        assert not Path(f"{target}.new").exists()
        assert not Path(f"{target}.old").exists()
        assert new_executable.exists(), "The recoverable new app must remain available"
        transactions = list(target.parent.glob('Eidolon.app.eidolon-update-*'))
        assert len(transactions) == 1
        transaction = transactions[0]
        journal = json.loads((transaction / 'transaction.json').read_text())
        assert journal['expected_source_commit'] == expected_commit
        assert journal['phase'] == ('unchanged' if deny_replacement else 'installed')
        assert not Path(f'{target}.eidolon-update').exists()
        if deny_replacement:
            assert "Permission denied" in log
            assert "Finder" in receipt["message"]
            assert str(rebuilt) in receipt["message"] and str(target) in receipt["message"]
            assert "Update complete" not in receipt["message"]
            assert old_executable.read_bytes() == old_bytes
            assert (target / "Contents/Info.plist").read_bytes() == old_info
            after = target.stat()
            assert (after.st_uid, after.st_gid, after.st_mode, after.st_flags) == (
                metadata.st_uid, metadata.st_gid, metadata.st_mode, metadata.st_flags)
            assert subprocess.check_output(["/bin/ls", "-ldeO@", str(target)], text=True) == before_acl
            assert subprocess.check_output(["/usr/bin/xattr", "-lx", str(target)]) == xattrs
        else:
            assert old_executable.read_bytes() == new_executable.read_bytes()
            assert (transaction / 'previous.app/Contents/MacOS/Eidolon').read_bytes() == old_bytes
            if lifetime:
                assert 'Relaunched app verified: pid=' in log
                assert 'version=new' in log
            else:
                assert 'Relaunched app verified: pid=' not in log
                assert 'could not restart itself' in receipt['message']
        deadline = time.monotonic() + 10
        while not launch_receipt.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert launch_receipt.exists(), "LaunchServices did not execute the selected app"
        assert launch_receipt.read_text() == ("old" if deny_replacement else "new")
    finally:
        if deny_replacement:
            subprocess.run(["/bin/chmod", "-a", acl, str(target)], check=True)


@pytest.mark.macos_only
@pytest.mark.parametrize('fault', ['install', 'interrupted', 'rollback', 'identity', 'provenance', 'external', 'external-empty'])
def test_macos_transaction_preserves_recovery_and_fences_retries(tmp_path, monkeypatch, fault):
    import importlib.util
    module_path = Path(__file__).resolve().parents[1] / 'scripts/desktop-update/mac_transaction.py'
    spec = importlib.util.spec_from_file_location('mac_transaction', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def bundle(path, version):
        executable = path / 'Contents/MacOS/Eidolon'
        executable.parent.mkdir(parents=True)
        executable.write_text('#!/bin/sh\necho ' + version + '\n')
        executable.chmod(0o755)
        (path / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleExecutable': 'Eidolon', 'CFBundleVersion': version,
            'CFBundleIdentifier': 'com.aethermesh.test.transaction'}))
        resources = path / 'Contents/Resources'
        resources.mkdir()
        (resources / 'install-stamp.json').write_text(json.dumps(build_stamp('b' * 40)))
        return module.bundle_identity(path)

    target = tmp_path / 'Eidolon.app'
    source = tmp_path / 'source/Eidolon.app'
    previous = bundle(target, 'old')
    expected = bundle(source, 'new')
    workspace = Path(str(target) + '.eidolon-update')
    rename = module.rename_noreplace

    identity = module.bundle_identity
    identity_failed = False

    def injected_identity(path):
        nonlocal identity_failed
        value = identity(path)
        if fault == 'identity' and path == target and value == expected and not identity_failed:
            identity_failed = True
            return {**value, 'sha256': 'unexpected installed identity'}
        return value

    def injected_rename(path, destination):
        if path == workspace / 'new.app' and fault not in ('identity', 'provenance'):
            if fault == 'external-empty':
                target.mkdir()
                return rename(path, destination)
            if fault == 'external':
                bundle(target, 'external')
            if fault == 'interrupted':
                raise KeyboardInterrupt('process interrupted after old bundle moved')
            raise OSError('injected final rename failure')
        if fault == 'rollback' and path == workspace / 'previous.app':
            raise OSError('injected rollback failure')
        result = rename(path, destination)
        if fault == 'provenance' and path == workspace / 'new.app':
            (target / 'Contents/Resources/install-stamp.json').write_text(json.dumps(build_stamp('a' * 40)))
        return result

    with monkeypatch.context() as patch:
        patch.setattr(module, 'rename_noreplace', injected_rename)
        patch.setattr(module, 'bundle_identity', injected_identity)
        with pytest.raises(KeyboardInterrupt if fault == 'interrupted' else module.ReplacementError):
            module.replace_bundle(source, target, 'b' * 40)
    assert module.bundle_identity(source) == expected
    if fault in ('install', 'identity', 'provenance'):
        assert module.bundle_identity(target) == previous
        archived = next(tmp_path.glob('Eidolon.app.eidolon-update-*'))
        assert json.loads((archived / 'transaction.json').read_text())['phase'] == 'restored'
        if fault != 'provenance':
            assert module.bundle_identity(archived / ('failed.app' if fault == 'identity' else 'new.app')) == expected
        receipt = module.replace_bundle(source, target, 'b' * 40)
        assert module.bundle_identity(target) == expected
        assert module.bundle_identity(receipt / 'previous.app') == previous
    else:
        if fault == 'external':
            assert module.bundle_identity(target)['version'] == 'external'
        elif fault == 'external-empty':
            assert target.is_dir() and not list(target.iterdir())
        else:
            assert not target.exists()
        assert module.bundle_identity(workspace / 'previous.app') == previous
        assert module.bundle_identity(workspace / 'new.app') == expected
        before = (workspace / 'transaction.json').read_bytes()
        # A second attempt must not delete/overwrite recoverable bundles even
        # when the destination is missing after an interrupted rename.
        with pytest.raises(module.ReplacementError, match="active or interrupted"):
            module.replace_bundle(source, target, 'b' * 40)
        assert (workspace / 'transaction.json').read_bytes() == before
        assert module.bundle_identity(workspace / 'previous.app') == previous


@pytest.mark.parametrize('stamp', [None, {}, build_stamp('a' * 40),
    build_stamp('b' * 12), build_stamp('b' * 40, True), build_stamp('b' * 40, None),
    {'commit': 'b' * 40, 'dirty': False},
    {**build_stamp('b' * 40), 'versionSource': 'fallback'}])
def test_transaction_rejects_unproven_source_before_touching_previous_app(tmp_path, stamp):
    """Equal app code is insufficient: full, clean source identity is required."""
    import importlib.util
    module_path = Path(__file__).resolve().parents[1] / 'scripts/desktop-update/mac_transaction.py'
    spec = importlib.util.spec_from_file_location('transaction_provenance', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / 'source.app'
    target = tmp_path / 'installed.app'
    for bundle in (source, target):
        resources = bundle / 'Contents/Resources'
        resources.mkdir(parents=True)
        (resources / 'app.asar').write_bytes(b'identical application code across source commits')
    if stamp is not None:
        (source / 'Contents/Resources/install-stamp.json').write_text(json.dumps(stamp))
    previous = (target / 'Contents/Resources/app.asar').read_bytes()
    with pytest.raises(module.ReplacementError, match='previous app was kept'):
        module.replace_bundle(source, target, 'b' * 40)
    assert (target / 'Contents/Resources/app.asar').read_bytes() == previous
    assert not Path(str(target) + '.eidolon-update').exists()
    # This function is host-independent; actual copy/rename coverage runs on macOS.
    (source / 'Contents/Resources/install-stamp.json').write_text(json.dumps(build_stamp('b' * 40)))
    assert module.verify_source_commit(source, 'b' * 40) == 'b' * 40

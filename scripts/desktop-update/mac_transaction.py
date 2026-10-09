#!/usr/bin/env python3
"""Unprivileged, recoverable app replacement. Never discards a prior transaction."""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import uuid

# These helpers also run directly from the detached shell handoff.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from eidolon_cli.eidolon_version import read_head, valid_sha, validate_identity


class ReplacementError(Exception):
    pass



def rename_noreplace(source: Path, destination: Path) -> Path:
    # Darwin RENAME_EXCL atomically rejects *any* existing destination, even
    # an empty directory. A check followed by ordinary rename can clobber it.
    rename = getattr(ctypes.CDLL(None, use_errno=True), 'renamex_np', None)
    if rename is None:
        raise OSError(errno.ENOSYS, 'Exclusive app replacement is not supported')
    rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(os.fsencode(source), os.fsencode(destination), 0x00000004) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(source), None, str(destination))
    return destination


def bundle_identity(bundle: Path) -> dict:
    with (bundle / 'Contents/Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    name = info.get('CFBundleExecutable')
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise ReplacementError(f'Invalid app executable in {bundle}')
    executable = bundle / 'Contents/MacOS' / name
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ReplacementError(f'App executable is not launchable: {executable}')
    # Includes app.asar, build stamps and frameworks, not just the Electron binary.
    digest = hashlib.sha256()
    for entry in sorted(bundle.rglob('*')):
        digest.update(str(entry.relative_to(bundle)).encode())
        if entry.is_symlink():
            digest.update(b'L' + os.readlink(entry).encode())
        elif entry.is_file():
            digest.update(b'F')
            with entry.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
        elif entry.is_dir():
            digest.update(b'D')
        else:
            raise ReplacementError(f'Unsupported app entry: {entry}')
    return {'sha256': digest.hexdigest(), 'executable': name,
            'version': str(info.get('CFBundleVersion', '')),
            'identifier': str(info.get('CFBundleIdentifier', ''))}


def verify_source_commit(bundle: Path, expected_commit: str) -> str:
    """Automatic updates require clean, pinned provenance, not just equal bytes."""
    if not valid_sha(expected_commit):
        raise ReplacementError('The updated source commit could not be verified. Retry the update from a Git checkout.')
    stamp_path = bundle / 'Contents/Resources/install-stamp.json'
    try:
        stamp = json.loads(stamp_path.read_text())
    except (OSError, ValueError) as error:
        raise ReplacementError(f'The new app is missing readable build provenance at {stamp_path}. Use a clean updated checkout, run eidolon desktop --force-build, then retry the update.') from error
    if not validate_identity(stamp) or stamp.get('commit') != expected_commit or stamp.get('dirty') is not False:
        actual = stamp.get('commit', 'unknown') if isinstance(stamp, dict) else 'unknown'
        raise ReplacementError(f'The new app does not prove a clean build of updated source {expected_commit} (app commit: {actual}). Use a clean updated checkout, run eidolon desktop --force-build, then retry the update.')
    return expected_commit


def save_journal(workspace: Path, journal: dict) -> None:
    temporary = workspace / 'transaction.tmp'
    with temporary.open('w') as stream:
        json.dump(journal, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(workspace / 'transaction.json')
    descriptor = os.open(workspace, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def replace_bundle(source: Path, target: Path, expected_commit: str) -> Path:
    if not target.is_absolute() or target.is_symlink() or target.parent.is_symlink():
        raise ReplacementError('The installed app must be an absolute, non-symlink bundle path.')
    workspace = Path(str(target) + '.eidolon-update')
    if os.path.lexists(workspace):
        raise ReplacementError(f'An active or interrupted update exists at {workspace}. Its app copies and journal were preserved. Review recovery before retrying.')
    source = source.resolve(strict=True)
    try:
        verify_source_commit(source, expected_commit)
    except ReplacementError as error:
        raise ReplacementError(f'{error} The previous app was kept.') from error
    if source == target.resolve(strict=True):
        return target
    expected = bundle_identity(source)
    previous = bundle_identity(target)
    for suffix in ('.old', '.new'):
        if os.path.lexists(str(target) + suffix):
            raise ReplacementError(f'Previous update files remain at {target}{suffix}. They were preserved; review them before retrying.')
    try:
        workspace.mkdir(mode=0o700)
    except FileExistsError as error:
        raise ReplacementError(f'An active or interrupted update exists at {workspace}. Its app copies and journal were preserved. Review recovery before retrying.') from error
    archived = Path(str(workspace) + '-' + uuid.uuid4().hex)
    staged = workspace / 'new.app'
    backup = workspace / 'previous.app'
    journal = {'schema': 1, 'source': str(source), 'target': str(target),
               'expected': expected, 'expected_source_commit': expected_commit,
               'previous': previous, 'phase': 'preparing'}
    moved = False
    staged_identity = None
    try:
        save_journal(workspace, journal)
        subprocess.run(['/usr/bin/ditto', str(source), str(staged)], check=True, capture_output=True, text=True)
        verify_source_commit(staged, expected_commit)
        if bundle_identity(staged) != expected:
            raise ReplacementError('The staged app does not match the new build.')
        journal['phase'] = 'staged'
        save_journal(workspace, journal)
        rename_noreplace(target, backup)
        moved = True
        journal['phase'] = 'previous-moved'
        save_journal(workspace, journal)
        staged_stat = staged.stat()
        staged_identity = (staged_stat.st_dev, staged_stat.st_ino)
        rename_noreplace(staged, target)
        verify_source_commit(target, expected_commit)
        if bundle_identity(target) != expected:
            raise ReplacementError('The installed app does not match the verified new build.')
        journal['phase'] = 'installed'
        save_journal(workspace, journal)
        rename_noreplace(workspace, archived)
        return archived
    except (OSError, ValueError, ReplacementError, subprocess.SubprocessError) as error:
        detail = str(error)
        if isinstance(error, subprocess.CalledProcessError):
            detail += ': ' + (error.stderr or '').strip()
        try:
            if moved:
                # Never overwrite an unexpected target while restoring the old app.
                if os.path.lexists(target):
                    target_stat = target.lstat()
                    if (target_stat.st_dev, target_stat.st_ino) != staged_identity:
                        raise ReplacementError('The target was changed outside this transaction; it was not moved.')
                    rename_noreplace(target, workspace / 'failed.app')
                rename_noreplace(backup, target)
                if bundle_identity(target) != previous:
                    raise ReplacementError('Restored app identity does not match the previous app.')
            journal.update(phase='restored' if moved else 'unchanged', error=detail)
            save_journal(workspace, journal)
            rename_noreplace(workspace, archived)
        except (OSError, ValueError, ReplacementError) as recovery_error:
            raise ReplacementError(f'Installation and recovery need attention: {detail}; {recovery_error}. Preserved files and journal: {workspace}. New build: {source}.') from error
        raise ReplacementError(f'The new app could not be installed: {detail}. The previous app was {"restored" if moved else "kept"}. Preserved files and journal: {archived}. New build: {source}. Use Finder to replace {target}, approving any macOS prompt, or ask your administrator.') from error


def legacy_source_commit() -> str:
    # A shell started before a source update keeps running the old two-operand
    # invocation against this newly updated helper. Trust only this checkout's
    # Git HEAD, never the candidate app or a fallback build-identity stamp.
    commit = read_head(Path(__file__).resolve().parents[2])
    if not valid_sha(commit):
        raise ReplacementError('The updated source commit could not be verified. The previous app was kept. Retry the update from a Git checkout.')
    return commit


def main() -> int:
    if sys.platform != 'darwin' or len(sys.argv) not in (3, 4):
        return 64
    try:
        expected_commit = sys.argv[3] if len(sys.argv) == 4 else legacy_source_commit()
        receipt = replace_bundle(Path(sys.argv[1]), Path(sys.argv[2]), expected_commit)
        print(f'Installed app verified: source commit={expected_commit}. Previous app and transaction receipt: {receipt}')
        return 0
    except (OSError, ValueError, ReplacementError) as error:
        print(str(error))
        return 7


if __name__ == '__main__':
    raise SystemExit(main())

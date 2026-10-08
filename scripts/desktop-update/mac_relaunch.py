#!/usr/bin/env python3
"""Verify LaunchServices started a fresh process from the selected app bundle."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time

from mac_transaction import ReplacementError, bundle_identity


def running_app(target: Path) -> dict | None:
    script = '''ObjC.import('AppKit'); function run(argv) {
      const apps=$.NSWorkspace.sharedWorkspace.runningApplications;
      for(let i=0;i<apps.count;i++) { const app=apps.objectAtIndex(i);
        if(app.bundleURL && ObjC.unwrap(app.bundleURL.path)===argv[0]
           && !app.isTerminated) {
          return JSON.stringify({pid:Number(app.processIdentifier),
            ready:Boolean(app.isFinishedLaunching),
            executable:ObjC.unwrap(app.executableURL.path),
            started:Number(app.launchDate.timeIntervalSince1970)});
        }
      } return ''; }'''
    result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-e', script, str(target)],
                            capture_output=True, text=True, timeout=5, check=False)
    if result.returncode != 0:
        raise ReplacementError('LaunchServices process inspection failed: ' + result.stderr.strip())
    return json.loads(result.stdout) if result.stdout.strip() else None


def relaunch(target: Path) -> bool:
    target = target.resolve(strict=True)
    expected = bundle_identity(target)
    if running_app(target) is not None:
        raise ReplacementError('The selected app is already running; a fresh relaunch could not be verified.')
    started = time.time()
    result = subprocess.run(['/usr/bin/open', '-n', str(target)], check=False, timeout=10)
    if result.returncode:
        return False
    deadline = time.monotonic() + 20
    stable = None
    stable_since = time.monotonic()
    executable = str(target / 'Contents/MacOS' / expected['executable'])
    while time.monotonic() < deadline:
        observed = running_app(target)
        if observed and (not observed.get('ready') or observed.get('executable') != executable
                         or observed.get('started', 0) < started - 1
                         or not isinstance(observed.get('pid'), int) or observed['pid'] <= 0):
            observed = None
        if observed != stable:
            stable, stable_since = observed, time.monotonic()
        if observed is not None and time.monotonic() - stable_since >= 2:
            if bundle_identity(target) != expected:
                raise ReplacementError('The app changed while its relaunch was being verified.')
            if running_app(target) != observed:
                return False
            print(f'Relaunched app verified: pid={observed["pid"]}, version={expected["version"]}, sha256={expected["sha256"]}')
            return True
        time.sleep(0.25)
    return False


def main() -> int:
    if sys.platform != 'darwin' or len(sys.argv) != 2:
        return 64
    try:
        return 0 if relaunch(Path(sys.argv[1])) else 1
    except (OSError, ValueError, ReplacementError, subprocess.SubprocessError) as error:
        print(f'Could not verify app relaunch: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

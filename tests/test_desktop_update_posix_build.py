"""Desktop handoffs must build even when a prepared source checkout is new."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.linux_only
@pytest.mark.parametrize("build_exit", [0, 1])
def test_successful_code_update_requires_desktop_build(tmp_path, build_exit):
    install = tmp_path / "desktop-source" / "checkout-new"
    bin_dir = install / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    # A prepared archive migration has Python dependencies but no Desktop
    # release/dist yet. Its regular CLI update therefore skips rebuilding.
    (bin_dir / "python3").symlink_to(sys.executable)
    cli = bin_dir / "eidolon"
    cli.write_text(
        '#!/bin/bash\n'
        'case "$1" in\n'
        '  update)\n'
        '    if [ "${2:-}" = "--help" ]; then echo --keep-stash; exit 0; fi\n'
        '    echo "Already up to date!"; exit 0 ;;\n'
        '  desktop)\n'
        '    printf "%s\\n" "$@" > "$TEST_BUILD_ARGS"\n'
        '    exit "$TEST_BUILD_EXIT" ;;\n'
        '  *) exit 64 ;;\n'
        'esac\n',
        encoding="utf-8",
    )
    cli.chmod(0o755)
    build_args = tmp_path / "build-args"
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "HERMES_HOME": str(install.parent),
        "TMPDIR": str(tmp_path),
        "TEST_BUILD_ARGS": str(build_args),
        "TEST_BUILD_EXIT": str(build_exit),
        "HERMES_UPDATE_SHIM_GRACE_SECONDS": "0",
    }
    env.pop("EIDOLON_HOME", None)
    result = subprocess.run(
        ["/bin/bash", str(Path(__file__).resolve().parents[1] / "scripts/desktop-update/posix.sh"),
         "--install-root", str(install), "--daemonized", "--no-ui"],
        env=env, capture_output=True, text=True, timeout=20,
    )
    assert build_args.exists(), result.stdout + result.stderr
    assert build_args.read_text(encoding="utf-8").splitlines() == ["desktop", "--build-only"]
    receipt = json.loads((install.parent / ".eidolon-update-result.json").read_text(encoding="utf-8"))
    assert receipt["ok"] is (build_exit == 0)
    assert receipt["exit_code"] == result.returncode == (0 if build_exit == 0 else 6)
    if build_exit:
        assert "Desktop app rebuild failed" in receipt["message"]

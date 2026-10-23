"""Exercise the CI stage reporter without installing packages or impersonating Linux."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX Bash CI helper")
SCRIPT = Path(__file__).resolve().parents[2] / "scripts/ci/organization-sandbox.sh"


def run_reporter(body, *, env=None):
    # Load the actual reporting function only; never bypass/run the Ubuntu guard.
    source = SCRIPT.read_text()
    reporter = source[source.index("run_stage() {"):source.index("\nsource /etc/os-release")]
    return subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + reporter + "\n" + body],
        text=True, capture_output=True, timeout=10,
        env=None if env is None else {**os.environ, **env},
    )


def test_preserves_arguments_and_stdout_without_logging_command_data():
    result = run_reporter(
        '''run_stage build_configure bash -c 'printf "%s\\n" "$@"' -- "two words" "private-argument"'''
    )
    assert result.returncode == 0
    assert result.stdout == "two words\nprivate-argument\n"
    assert result.stderr == (
        "organization-sandbox stage=build_configure event=start\n"
        "organization-sandbox stage=build_configure event=end\n"
    )


def test_failure_preserves_status_and_stops_before_later_commands():
    result = run_reporter(
        '''run_stage package_index bash -c 'printf attempted; exit 23'
run_stage build_dependencies bash -c 'printf must-not-run'
'''
    )
    assert result.returncode == 23
    assert result.stdout == "attempted"
    assert result.stderr == (
        "organization-sandbox stage=package_index event=start\n"
        "organization-sandbox stage=package_index event=failed\n"
    )


def test_pipeline_input_is_unchanged_and_failure_prevents_extraction():
    result = run_reporter(
        '''printf payload | run_stage release_checksum bash -c 'cat; exit 7'
run_stage release_extract bash -c 'printf must-not-run'
'''
    )
    assert result.returncode == 7
    assert result.stdout == "payload"
    assert result.stderr == (
        "organization-sandbox stage=release_checksum event=start\n"
        "organization-sandbox stage=release_checksum event=failed\n"
    )


@pytest.mark.parametrize("exit_code", [0, 23])
def test_command_substitution_and_exit_cleanup_preserve_status(tmp_path, exit_code):
    stage_root = tmp_path / "stages"
    stage_root.mkdir()
    result = run_reporter(
        '''build_root="$(run_stage temporary_directory mktemp -d "$TEST_ROOT/run.XXXXXX")"
trap 'run_stage cleanup rm -rf "$build_root"' EXIT
[[ -d "$build_root" ]]
exit "$TEST_EXIT"
''', env={"TEST_ROOT": str(stage_root), "TEST_EXIT": str(exit_code)},
    )
    assert result.returncode == exit_code
    assert result.stdout == ""
    assert list(stage_root.iterdir()) == []
    assert result.stderr == (
        "organization-sandbox stage=temporary_directory event=start\n"
        "organization-sandbox stage=temporary_directory event=end\n"
        "organization-sandbox stage=cleanup event=start\n"
        "organization-sandbox stage=cleanup event=end\n"
    )

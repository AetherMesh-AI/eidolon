"""Exact file budgets leave every other canonical runner cap unchanged."""
import argparse
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.run_tests_parallel import _file_timeout_for, _parse_file_timeout_override


@pytest.mark.parametrize('value', ['../test.py=600', '/test.py=600', 'tests/*.py=600',
    'tests\\test.py=600', 'C:/test.py=600', 'tests/test.py=0', 'tests/test.py=-1',
    'tests/test.py=nan', 'tests/test.py=inf', 'tests/test.py=bad', 'tests/=600'])
def test_invalid_override_fails_closed(value):
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_file_timeout_override(value)


def test_only_exact_file_gets_declared_budget(tmp_path):
    name, seconds = _parse_file_timeout_override('tests/test_retention.py=600')
    overrides = {name: seconds}
    assert _file_timeout_for(tmp_path/name, tmp_path, 300, overrides) == 600
    assert _file_timeout_for(tmp_path/'tests/test_other.py', tmp_path, 300, overrides) == 300
    assert _file_timeout_for(tmp_path/'other/test_retention.py', tmp_path, 300, overrides) == 300
    assert _file_timeout_for(tmp_path.parent/'test_retention.py', tmp_path, 300, overrides) == 300
    assert _file_timeout_for(tmp_path/'tests/test_other.py', tmp_path, 17, overrides) == 17


def test_cli_rejects_duplicate_budget_before_execution():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root/'scripts/run_tests_parallel.py'),
        '--file-timeout-override', 'tests/test_one.py=600',
        '--file-timeout-override', 'tests/test_one.py=700'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert 'Duplicate file timeout override' in result.stderr

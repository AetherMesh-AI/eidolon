"""Canonical home input preserves the established profile subprocess contract."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run(code: str, env: dict[str, str]) -> dict:
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env,
                            text=True, capture_output=True, timeout=30, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('canonical', [True, False])
def test_canonical_home_precedes_legacy_and_is_consumed(tmp_path, canonical):
    selected = tmp_path / 'eidolon-home'
    legacy = tmp_path / 'legacy-input'
    env = {**os.environ, 'HERMES_HOME': str(legacy),
           'EIDOLON_HOME': str(selected) if canonical else '  '}
    result = _run('''
import json, os
import eidolon_cli
from eidolon_constants import get_eidolon_home
print(json.dumps(dict(home=str(get_eidolon_home()),
                      transport=os.environ['HERMES_HOME'],
                      canonical_present='EIDOLON_HOME' in os.environ)))
''', env)
    expected = str(selected if canonical else legacy)
    assert result == dict(home=expected, transport=expected, canonical_present=False)
    assert not selected.exists() and not legacy.exists()


def test_canonical_home_does_not_override_spawned_profile(tmp_path):
    selected = tmp_path / 'eidolon-home'
    profile = selected / 'profiles' / 'work'
    profile.mkdir(parents=True)
    (profile / 'config.yaml').write_text('{}\n', encoding='utf-8')
    upstream = tmp_path / '.hermes'
    upstream.mkdir()
    env = {**os.environ, 'EIDOLON_HOME': str(selected), 'HERMES_HOME': str(upstream)}
    result = _run('''
import json, os, subprocess, sys
from eidolon_constants import get_eidolon_home
from eidolon_cli.profiles import resolve_profile_env
root = str(get_eidolon_home())
child_env = dict(os.environ, HERMES_HOME=resolve_profile_env('work'))
child = subprocess.run([sys.executable, '-c',
    'from eidolon_constants import get_eidolon_home; print(get_eidolon_home())'],
    env=child_env, text=True, capture_output=True, timeout=20, check=True)
print(json.dumps(dict(root=root, child=child.stdout.strip(),
                      parent=str(get_eidolon_home()))))
''', env)
    assert result == dict(root=str(selected), child=str(profile), parent=str(selected))
    assert list(upstream.iterdir()) == []

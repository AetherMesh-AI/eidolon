"""Real module CLI uses the same reviewed, root-only allowance boundary as RPC."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_metadata import save_metadata
from eidolon_cli.organization_store import OrganizationStore


MODULE = 'eidolon_cli.organization_shared_allowance_cli'
ROOT = Path(__file__).resolve().parents[2]


def create(home):
    home.mkdir(parents=True, exist_ok=True)
    (home / 'config.yaml').write_text('{}\n', encoding='utf-8')
    path = home / 'organization' / 'state.db'
    metadata = save_metadata(path, display_name='Allowance test', owner_display_name='Owner',
        expected_organization_id=None, expected_revision=None, idempotency_key='init')['metadata']
    OrganizationStore(path, OrganizationSettings())
    return SimpleNamespace(home=home, metadata=metadata)


def run(home, *args):
    return subprocess.run([sys.executable, '-m', MODULE, *map(str, args)], cwd=ROOT,
                          env={**os.environ, 'HERMES_HOME': str(home)},
                          text=True, capture_output=True, timeout=20)


def result(home, *args):
    completed = run(home, *args)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def file_at(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')
    return path


@pytest.fixture
def installation(tmp_path):
    root = create(tmp_path / 'custom-installation')
    root.member = create(root.home / 'profiles' / 'member')
    return root


def candidate(root, current):
    dims = lambda calls, tokens: {'modelCalls': calls, 'tokens': tokens, 'configuredCostUsd': None}
    iso = lambda seconds: datetime.fromtimestamp(seconds, timezone.utc).isoformat()
    now = time.time()
    return {'expectedOrganizationId': current['organizationId'], 'expectedOwnerId': current['ownerId'],
            'expectedGeneration': current['generation'], 'sourceRevision': current['sourceRevision'],
            'configuration': {'periodId': 'explicit-cli-period', 'startsAt': iso(now - 60), 'endsAt': iso(now + 3600),
                'limits': dims(10, 10000), 'protected': dims(1, 1000), 'allocations': [{
                    'profile': 'member', 'organizationId': root.member.metadata['organizationId'],
                    'ownerId': root.member.metadata['owner']['ownerId'], 'limits': dims(10, 10000),
                    'protected': dims(1, 1000)}]}}


def test_real_cli_show_preview_apply_replay_and_scoped_member_read(installation, tmp_path):
    root = installation
    current = result(root.home, 'show')
    assert current['state'] == 'unconfigured' and current['authority'] == 'installation-owner'
    params = candidate(root, current)
    path = file_at(tmp_path / 'review.json', params)
    preview = result(root.home, 'preview', '--action', 'configuration', '--file', path)
    assert not (root.home / 'organization' / 'shared-allowance.db').exists()
    params.update(configuration=preview['configuration'], previewVersion=preview['previewVersion'],
                  idempotencyKey='cli-save', confirmed=True)
    file_at(path, params)
    receipt = result(root.home, 'apply', '--action', 'configuration', '--file', path, '--confirm')
    assert receipt['receipt']['generation'] == 1
    assert result(root.home, 'apply', '--action', 'configuration', '--file', path, '--confirm') == receipt
    member = result(root.member.home, 'show')
    assert member['authority'] == 'member' and member['configuration'] is None and member['pool'] is None
    assert member['candidates'] == [] and member['member'] is not None
    denied = run(root.member.home, 'apply', '--action', 'configuration', '--file', path, '--confirm')
    assert denied.returncode == 2
    assert result(root.home, 'show')['generation'] == 1


def test_cli_requires_confirmation_and_rejects_arbitrary_routing_fields(installation, tmp_path):
    root = installation
    params = candidate(root, result(root.home, 'show'))
    path = file_at(tmp_path / 'invalid.json', {**params, 'root': str(root.home)})
    assert run(root.home, 'preview', '--action', 'configuration', '--file', path).returncode == 2
    file_at(path, params)
    assert run(root.home, 'apply', '--action', 'configuration', '--file', path).returncode == 2
    assert not (root.home / 'organization' / 'shared-allowance.db').exists()


def test_cli_missing_installation_never_initializes(tmp_path):
    missing = tmp_path / 'absent'
    assert run(missing, 'show').returncode == 2
    assert not missing.exists()


def test_cli_ignores_inherited_context_override_for_read_preview_and_apply(installation, tmp_path, monkeypatch, capsys):
    from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override
    from eidolon_cli.organization_shared_allowance_cli import main
    monkeypatch.setenv('HERMES_HOME', str(installation.home))
    token = set_eidolon_home_override(installation.member.home)
    try:
        assert main(['show']) == 0
        current = json.loads(capsys.readouterr().out)
        assert current['organizationId'] == installation.metadata['organizationId']
        assert current['authority'] == 'installation-owner'
        params = candidate(installation, current)
        path = file_at(tmp_path / 'context-review.json', params)
        assert main(['preview', '--action', 'configuration', '--file', str(path)]) == 0
        reviewed = json.loads(capsys.readouterr().out)
        params.update(configuration=reviewed['configuration'], previewVersion=reviewed['previewVersion'],
                      idempotencyKey='context-save', confirmed=True)
        file_at(path, params)
        assert main(['apply', '--action', 'configuration', '--file', str(path), '--confirm']) == 0
        receipt = json.loads(capsys.readouterr().out)['receipt']
        assert receipt['organizationId'] == installation.metadata['organizationId']
        assert receipt['generation'] == 1
    finally:
        reset_eidolon_home_override(token)

"""Owner observation controls use the selected profile without starting work."""
import json
import time

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_pacing_cli import main
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_pacing import iso


def test_owner_can_record_inspect_and_replay_observation_in_current_profile(tmp_path, monkeypatch, capsys):
    home = tmp_path / 'profile'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    config = {'organization': {'adaptive_pacing_enabled': True}}
    (home / 'config.yaml').write_text(json.dumps(config))
    store = OrganizationStore(home / 'organization' / 'state.db', OrganizationSettings.from_config(config))
    objective = store.create_objective('Bound the local queue', idempotency_key='objective')
    now = time.time()
    args = ['observe', objective['id'], '--remaining-percent', '8.5', '--as-of', iso(now),
            '--expires-at', iso(now + 60), '--expected-revision', '0', '--idempotency-key', 'owner-input']
    assert main(args) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt['revision'] == 1
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out) == receipt
    assert main(['show', objective['id']]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view['observation']['remainingPercent'] == 8.5
    assert view['observation']['source'] == 'owner_manual'
    assert view['mode'] == 'critical'
    with store._connect() as conn:
        assert conn.execute("SELECT count(*) FROM requests WHERE status='running'").fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM organization_model_calls').fetchone()[0] == 0
        assert conn.execute('SELECT actor_id FROM organization_budget_observations').fetchone()[0] == 'owner'
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'other-profile'))
    with pytest.raises(SystemExit) as error:
        main(['show', objective['id']])
    assert error.value.code == 2
    assert not (tmp_path / 'other-profile').exists()


def test_owner_write_cannot_initialize_missing_ledger_or_accept_stale_high_observation(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    now = time.time()
    args = ['observe', 'missing', '--remaining-percent', '100', '--as-of', iso(now - 60),
            '--expires-at', iso(now - 1), '--expected-revision', '0', '--idempotency-key', 'stale']
    with pytest.raises(SystemExit):
        main(args)
    assert not (tmp_path / 'organization').exists()
    store = OrganizationStore(tmp_path / 'organization' / 'state.db')
    objective = store.create_objective('Fresh observation only', idempotency_key='objective')
    args[1] = objective['id']
    with pytest.raises(SystemExit):
        main(args)
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM organization_budget_observations').fetchone()[0] == 0

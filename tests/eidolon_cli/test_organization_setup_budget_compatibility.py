"""Execute pinned pre-policy code to prove mixed-version setup fails closed.

These are executable historical fixtures, not assertions about implementation
text. Their exact bytes come from tree bc0ef1b2648bda8e0beb1db948c365319811e97c.
No checkout, network access, installed old package, or physical provider is used.
"""
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_owner_policy_config as overrides
from eidolon_cli import organization_team_admission as admission
from eidolon_cli import organization_team_budget as budget
from eidolon_cli import organization_team_generation as generation
from tests.eidolon_cli.test_organization_team_budget import ledger, reviewed


_FIXTURES = Path(__file__).parents[1] / 'fixtures' / 'setup_budget_legacy'
_HASHES = {
    'organization_team_setup.py.txt': '46e2312087593cf37285a92565b4f63645c7e0db90e4468a164342925ebb2a3c',
    'organization_owner_policy_config.py.txt': '57357edfa5d8fb947f5ea49881e947274b937d2375c58d57e65f1a605714d545',
}


def historical_module(filename):
    path = _FIXTURES / filename
    source = path.read_bytes()
    assert hashlib.sha256(source).hexdigest() == _HASHES[filename], 'Historical compatibility fixture changed'
    namespace = {'__name__': 'setup_budget_legacy_' + filename.replace('.', '_'), '__file__': str(path)}
    exec(compile(source, str(path), 'exec'), namespace)
    return SimpleNamespace(**namespace)


def test_old_setup_and_inference_accept_legacy_then_reject_explicit_new_policy_before_send(ledger, monkeypatch):
    old_setup = historical_module('organization_team_setup.py.txt')
    old_overrides = historical_module('organization_owner_policy_config.py.txt')
    organization_id = ledger.identity['organizationId']
    before = list(ledger.conn.iterdump())
    with monkeypatch.context() as historical:
        historical.setattr(overrides, 'resolve_owner_overrides', old_overrides.resolve_owner_overrides)
        identity, policy, settings, revision = old_setup._current(ledger.conn, ledger.home, organization_id)
        assert identity['organizationId'] == organization_id
        assert policy['generation'] == 1
        assert settings == ledger.settings
        assert old_setup._fresh(ledger.conn, settings)
    assert list(ledger.conn.iterdump()) == before

    # Only a fresh owner review enables v2. The settings representation itself
    # stays legacy-compatible; the explicit nested override is the version fence.
    old_settings = ledger.conn.execute('SELECT settings FROM organization_policy').fetchone()[0]
    receipt = budget.apply_budget(ledger.home, reviewed(ledger))['receipt']
    assert receipt['policyGeneration'] == policy['generation'] + 1
    assert ledger.conn.execute('SELECT settings FROM organization_policy').fetchone()[0] == old_settings
    after = list(ledger.conn.iterdump())
    sends = []

    def forbidden_provider(*args, **kwargs):
        sends.append(True)
        raise AssertionError('An unsupported old runtime reached provider invocation')

    params = {'expectedOrganizationId': organization_id, 'sourceRevision': revision,
              'jobs': 'Analyze supplied text', 'goals': 'Write a short report', 'namingStyle': '',
              'idempotencyKey': 'old-runtime-send'}
    with monkeypatch.context() as historical:
        historical.setattr(overrides, 'resolve_owner_overrides', old_overrides.resolve_owner_overrides)
        historical.setattr(admission, '_current', old_setup._current)
        historical.setattr(admission, '_connection', budget._connection)
        historical.setattr(generation, 'generate', forbidden_provider)
        with pytest.raises(ValueError, match='Persisted budget policy contains unsupported settings'):
            old_setup._current(ledger.conn, ledger.home, organization_id)
        with pytest.raises(ValueError, match='Persisted budget policy contains unsupported settings'):
            admission.generate_team(ledger.home, params)
    assert not sends
    assert ledger.conn.execute('SELECT count(*) FROM organization_team_generations').fetchone()[0] == 0
    assert list(ledger.conn.iterdump()) == after

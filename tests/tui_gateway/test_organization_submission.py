"""Exact admission inspection through the authenticated profile RPC; no service startup."""
import json
import sqlite3
from dataclasses import replace

import pytest

from eidolon_cli import organization_service as services
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_projects import configured
import tui_gateway.server as server


def rpc(**params):
    return server.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'organization.checkSubmission', 'params': params})


def dump(path):
    with sqlite3.connect(path) as conn:
        return '\n'.join(conn.iterdump())


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    from eidolon_cli import profiles as profile_api
    root = tmp_path / 'root'
    monkeypatch.setattr(profile_api, '_get_profiles_root', lambda: root / 'profiles')
    monkeypatch.setattr(profile_api, '_get_default_eidolon_home', lambda: root)
    homes = {name: root / 'profiles' / name for name in ['alpha', 'beta', 'empty']}
    for home in homes.values():
        home.mkdir(parents=True)
        (home / 'config.yaml').write_text('organization:\n  projects: invalid-current-configuration\n')
    def forbidden(*args, **kwargs):
        pytest.fail('Receipt inspection must not construct a service, write, or execute work')
    monkeypatch.setattr(services, 'get_service', forbidden)
    monkeypatch.setattr(services, '_execute', forbidden)
    monkeypatch.setattr(services.OrganizationService, 'start', forbidden)
    return homes


@pytest.mark.parametrize('change', ['retired', 'team_changed'])
def test_exact_receipt_survives_configuration_changes_and_archive_without_any_ledger_write(profiles, tmp_path, change):
    settings = configured(tmp_path)
    home = profiles['alpha']
    path = home / 'organization' / 'state.db'
    store = OrganizationStore(path, settings)
    original = store.create_objective('Exact admitted title', 'Private context must not be returned', project_ids=['alpha'], idempotency_key='original-key')
    store.cancel(original['id'])
    store.set_objective_archived(original['id'], True, expected_revision=0, idempotency_key='archive')
    projects = tuple(project for project in settings.projects if project.id != 'alpha') if change == 'retired' else (
        replace(settings.projects[0], team='changed-team'), settings.projects[1])
    changed = OrganizationStore(path, replace(settings, projects=projects))
    with pytest.raises(ValueError):
        changed.create_objective('Exact admitted title', 'Private context must not be returned', project_ids=['alpha'], idempotency_key='original-key')
    before = dump(path)
    config = (home / 'config.yaml').read_bytes()
    response = rpc(profile='alpha', idempotencyKey='original-key')
    assert 'error' not in response, response
    receipt = response['result']
    assert receipt['version'] == 1 and receipt['profile'] == 'alpha'
    assert receipt['idempotencyKey'] == 'original-key'
    assert receipt['objective'] == {'id': original['id'], 'title': original['title'],
                                    'createdAt': original['createdAt'], 'archived': True}
    assert 'Private context' not in json.dumps(receipt)
    assert rpc(profile='alpha', idempotencyKey='original-key') == response
    assert dump(path) == before
    assert (home / 'config.yaml').read_bytes() == config


def test_key_guess_and_other_profile_do_not_disclose_receipts_or_provision_storage(profiles):
    a = OrganizationStore(profiles['alpha'] / 'organization' / 'state.db')
    original = a.create_objective('Only alpha', idempotency_key='alpha-key')
    before = dump(a.path)
    for profile, key in [('alpha', original['id']), ('alpha', 'Only alpha'), ('alpha', 'wrong-key'), ('alpha', "' OR 1=1 --"), ('beta', 'alpha-key'), ('empty', 'alpha-key')]:
        response = rpc(profile=profile, idempotencyKey=key)
        assert response['result'] == {'version': 1, 'profile': profile, 'idempotencyKey': key, 'objective': None}
    assert not (profiles['beta'] / 'organization').exists()
    assert not (profiles['empty'] / 'organization').exists()
    assert dump(a.path) == before


def test_uncommitted_admission_is_missing_until_commit_and_lookup_never_changes_it(profiles):
    home = profiles['alpha']
    store = OrganizationStore(home / 'organization' / 'state.db')
    with store._write() as conn:
        ident = store._create_objective(conn, 'Pending commit', idempotency_key='pending')
        assert rpc(profile='alpha', idempotencyKey='pending')['result']['objective'] is None
        assert conn.execute('SELECT count(*) FROM objectives').fetchone()[0] == 1
    assert rpc(profile='alpha', idempotencyKey='pending')['result']['objective']['id'] == ident
    assert len(store.snapshot()['objectives']) == 1


@pytest.mark.parametrize('params', [{}, {'idempotencyKey': ''}, {'idempotencyKey': 'x'*129}, {'idempotencyKey': []},
    {'idempotencyKey': 'key', 'profile': '../alpha'}, {'idempotencyKey': 'key', 'path': '/other.db'},
    {'idempotencyKey': 'key', 'title': 'fuzzy'}, {'idempotencyKey': 'key', 'actorId': 'other'}])
def test_rejects_invalid_or_cross_boundary_inputs(profiles, params):
    assert rpc(**params)['error']['code'] == -32602


def test_unbound_transport_and_unreadable_database_are_not_missing_receipts(profiles):
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'organization.checkSubmission',
               'params': {'profile': 'alpha', 'idempotencyKey': 'key'}}
    assert server.handle_request(request)['error']['code'] == 4001
    path = profiles['alpha'] / 'organization' / 'state.db'
    path.parent.mkdir()
    path.write_bytes(b'not a sqlite database')
    assert rpc(profile='alpha', idempotencyKey='key')['error']['code'] == 5070
    assert path.read_bytes() == b'not a sqlite database'

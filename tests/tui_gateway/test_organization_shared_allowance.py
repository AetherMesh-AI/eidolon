"""Real served-owner RPC boundary for the opt-in installation allowance."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from queue import Queue
import sqlite3
import time
from types import SimpleNamespace
import uuid

import pytest

from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_metadata import save_metadata
from eidolon_cli.organization_store import OrganizationStore
import tui_gateway.server as server


READ = 'organization.sharedAllowance'
OPERATIONS = {
    'configuration': ('organization.previewSharedAllowance', 'organization.configureSharedAllowance'),
    'observation': ('organization.previewSharedAllowanceObservation', 'organization.recordSharedAllowanceObservation'),
}
METHODS = [READ, *(name for pair in OPERATIONS.values() for name in pair)]


def call(method=READ, params=None, transport=None):
    responses = Queue()
    transport = transport or SimpleNamespace(closed=False)
    transport.write = responses.put
    request = {'jsonrpc': '2.0', 'id': 17, 'method': method, 'params': {} if params is None else params}
    result = server.dispatch(request, transport=transport)
    return result if result is not None else responses.get(timeout=10)


def make_profile(home, name='Organization', *, execution=True):
    home.mkdir(parents=True, exist_ok=True)
    (home / 'config.yaml').write_text('{}\n', encoding='utf-8')
    path = home / 'organization' / 'state.db'
    identity = save_metadata(path, display_name=name, owner_display_name='Owner ' + name,
        expected_organization_id=None, expected_revision=None, idempotency_key='initialize')['metadata']
    if execution:
        OrganizationStore(path, OrganizationSettings())
    return SimpleNamespace(home=home, path=path, identity=identity)


def disk_state(root):
    result = {}
    for path in root.rglob('*'):
        # SQLite WAL readers may attach/remove coordination sidecars. Compare
        # durable logical rows/schema and every non-SQLite file instead.
        if path.name in {'state.db-wal', 'state.db-shm', 'shared-allowance.db-wal', 'shared-allowance.db-shm'}:
            continue
        if path.is_file():
            if path.suffix == '.db':
                with closing(sqlite3.connect(path)) as conn:
                    result[str(path.relative_to(root))] = tuple(conn.iterdump())
            else:
                result[str(path.relative_to(root))] = path.read_bytes()
        elif path.is_dir():
            result[str(path.relative_to(root)) + '/'] = None
    return result


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    home = tmp_path / '.eidolon'
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(server, '_eidolon_home', home)
    root = make_profile(home, 'Root')
    root.alpha = make_profile(home / 'profiles' / 'alpha', 'Alpha')
    root.beta = make_profile(home / 'profiles' / 'beta', 'Beta')
    root.empty = make_profile(home / 'profiles' / 'empty', 'Not initialized', execution=False)
    root.vacant = home / 'profiles' / 'vacant'
    root.vacant.mkdir()
    import eidolon_cli.organization_service as services
    def forbidden(*args, **kwargs):
        raise AssertionError('Shared allowance entered execution or mutable profile routing')
    monkeypatch.setattr(services, 'get_service', forbidden)
    monkeypatch.setattr(services.OrganizationService, '__init__', forbidden)
    monkeypatch.setattr(services.OrganizationService, 'start', forbidden)
    monkeypatch.setattr(OrganizationStore, '__init__', forbidden)
    monkeypatch.setattr(server, '_profile_home', forbidden)
    return root


def read(profile):
    response = call(params={'expectedOrganizationId': profile.identity['organizationId']})
    assert 'error' not in response, response
    return response['result']


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def envelope(current, candidate, *, action='configuration'):
    return {'expectedOrganizationId': current['organizationId'], 'expectedOwnerId': current['ownerId'],
            'expectedGeneration': current['generation'], 'sourceRevision': current['sourceRevision'], action: candidate}


def configuration(root):
    now = time.time()
    dimensions = lambda calls, tokens: {'modelCalls': calls, 'tokens': tokens, 'configuredCostUsd': None}
    return {'periodId': 'owner-period-one', 'startsAt': iso(now - 60), 'endsAt': iso(now + 3600),
            'limits': dimensions(20, 20000), 'protected': dimensions(2, 2000),
            'allocations': [{'profile': name, 'organizationId': profile.identity['organizationId'],
                'ownerId': profile.identity['owner']['ownerId'], 'limits': dimensions(10, 10000),
                'protected': dimensions(1, 1000)} for name, profile in [('default', root), ('alpha', root.alpha)]]}


def preview(root, candidate=None, *, action='configuration'):
    params = envelope(read(root), configuration(root) if candidate is None else candidate, action=action)
    response = call(OPERATIONS[action][0], params)
    assert 'error' not in response, response
    params[action] = response['result'][action]
    return params, response['result']


def apply(root, candidate=None, *, action='configuration', key='save'):
    params, reviewed = preview(root, candidate, action=action)
    request = {**params, 'previewVersion': reviewed['previewVersion'], 'idempotencyKey': key, 'confirmed': True}
    response = call(OPERATIONS[action][1], request)
    assert 'error' not in response, response
    return request, response


def test_descriptor_and_preview_are_observational_and_list_only_initialized_candidates(served):
    before = disk_state(served.home)
    current = read(served)
    assert current['state'] == 'unconfigured'
    assert current['authority'] == 'installation-owner'
    assert current['configuration'] is None and current['generation'] == 0
    assert current['capabilities'] == {'configuration': True, 'observation': False}
    assert {row['profile'] for row in current['candidates']} == {'default', 'alpha', 'beta'}
    assert current['scope'] == {'kind': 'served-profile', 'profile': 'default'}
    assert current['pool'] is None
    params, reviewed = preview(served)
    assert reviewed['configuration'] == params['configuration']
    assert reviewed['impact']['automaticReset'] is False
    assert disk_state(served.home) == before
    assert not (served.home / 'organization' / 'shared-allowance.db').exists()
    assert call('gateway.capabilities')['result']['organization_shared_allowance'] == 1


def test_real_apply_observation_and_original_intent_replay_return_immutable_receipts(served):
    request, applied = apply(served)
    assert applied['result']['receipt']['generation'] == 1
    assert call(OPERATIONS['configuration'][1], request) == applied
    current = read(served)
    assert current['state'] == 'ready' and current['configuration'] == request['configuration']
    later = deepcopy(current['configuration'])
    later['limits']['modelCalls'] = 22
    apply(served, later, key='later')
    now = time.time()
    observed = {'remainingPercent': 25, 'asOf': iso(now - 1), 'expiresAt': iso(now + 300), 'resetAt': None}
    obs_request, obs_result = apply(served, observed, action='observation', key='observation')
    assert obs_result['result']['receipt']['generation'] == 3
    assert call(OPERATIONS['observation'][1], obs_request) == obs_result
    assert call(OPERATIONS['configuration'][1], request) == applied
    assert read(served)['generation'] == 3
    changed = {**request, 'sourceRevision': '0' * 64}
    assert call(OPERATIONS['configuration'][1], changed)['error']['data']['reason'] == 'idempotency_conflict'


@pytest.mark.parametrize('method', METHODS)
def test_no_transport_and_parameter_routing_are_rejected_before_storage(served, method):
    before = disk_state(served.home)
    params = {'expectedOrganizationId': served.identity['organizationId']}
    raw = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}
    assert server.handle_request(raw)['error']['code'] == 4001
    assert call(method, params, SimpleNamespace(closed=True))['error']['code'] == 4001
    for extra in ({'profile': 'alpha'}, {'home': str(served.home)}, {'root': str(served.home)},
                  {'organizationId': served.identity['organizationId']}, {'actorId': 'owner'},
                  {'connectionId': 'different'}, {'apiKey': 'not-stored'}):
        assert call(method, {**params, **extra})['error']['code'] == -32602
    assert disk_state(served.home) == before


def test_context_override_cannot_redirect_root_authority_or_candidates(served):
    before = disk_state(served.home)
    initial = read(served)
    token = set_eidolon_home_override(served.alpha.home)
    try:
        assert read(served) == initial
        params, reviewed = preview(served)
        assert reviewed['organizationId'] == served.identity['organizationId']
        assert params['expectedOwnerId'] == served.identity['owner']['ownerId']
    finally:
        reset_eidolon_home_override(token)
    assert disk_state(served.home) == before


def test_member_endpoint_cannot_become_installation_owner_or_reveal_siblings(served, monkeypatch):
    apply(served)
    monkeypatch.setattr(server, '_eidolon_home', served.alpha.home)
    before = disk_state(served.home)
    current = read(served.alpha)
    assert current['authority'] == 'member'
    assert current['configuration'] is None and current['candidates'] == [] and current['pool'] is None
    assert current['member'] is not None
    assert current['capabilities'] == {'configuration': False, 'observation': False}
    assert current['scope'] == {'kind': 'served-profile', 'profile': 'alpha'}
    assert served.beta.identity['organizationId'] not in json.dumps(current)
    params = envelope(current, configuration(served))
    for method in OPERATIONS['configuration']:
        request = params if method == OPERATIONS['configuration'][0] else {
            **params, 'confirmed': True, 'previewVersion': '0' * 64, 'idempotencyKey': 'forged'}
        assert call(method, request)['error']['data']['reason'] == 'installation_owner_required'
    assert call(params={'expectedOrganizationId': served.identity['organizationId']})['error']['data']['reason'] == 'identity_conflict'
    assert disk_state(served.home) == before


def test_confirmation_preview_source_and_owner_changes_never_write(served):
    params, reviewed = preview(served)
    request = {**params, 'confirmed': True, 'previewVersion': reviewed['previewVersion'], 'idempotencyKey': 'valid'}
    before = disk_state(served.home)
    for change in ({'confirmed': False}, {'confirmed': 1}, {'previewVersion': '0' * 64},
                   {'sourceRevision': '0' * 64}, {'expectedGeneration': 1},
                   {'expectedOwnerId': str(uuid.uuid4())}):
        assert 'error' in call(OPERATIONS['configuration'][1], {**request, **change})
    assert disk_state(served.home) == before


def test_metadata_only_profile_cannot_be_enrolled_by_rpc_preview(served):
    candidate = configuration(served)
    candidate['allocations'][1].update(profile='empty', organizationId=served.empty.identity['organizationId'],
                                      ownerId=served.empty.identity['owner']['ownerId'])
    before = disk_state(served.home)
    result = call(OPERATIONS['configuration'][0], envelope(read(served), candidate))
    assert result['error']['data']['reason'] == 'enrollment_identity_conflict'
    assert disk_state(served.home) == before


@pytest.mark.linux_only
def test_launch_symlink_never_promotes_alias_to_installation_owner(served, monkeypatch):
    alias = served.home.parent / 'launch-alias'
    alias.symlink_to(served.home, target_is_directory=True)
    monkeypatch.setattr(server, '_eidolon_home', alias)
    before = disk_state(served.home)
    result = call(params={'expectedOrganizationId': served.identity['organizationId']})
    assert result['error']['code'] == 4001
    assert str(alias) not in str(result)
    assert disk_state(served.home) == before


def test_missing_root_and_corrupt_pool_fail_closed_without_provisioning(tmp_path, monkeypatch):
    home = tmp_path / 'missing'
    monkeypatch.setattr(server, '_eidolon_home', home)
    result = call(params={'expectedOrganizationId': str(uuid.uuid4())})
    assert 'error' in result and str(home) not in str(result)
    assert not home.exists()
    root = make_profile(home)
    (home / 'organization' / 'shared-allowance.db').write_bytes(b'corrupt')
    result = call(params={'expectedOrganizationId': root.identity['organizationId']})
    assert 'error' in result and str(home) not in str(result)
    assert (home / 'organization' / 'shared-allowance.db').read_bytes() == b'corrupt'


def test_named_profile_with_no_root_identity_never_self_promotes(tmp_path, monkeypatch):
    root = tmp_path / 'installation'
    member = make_profile(root / 'profiles' / 'member')
    monkeypatch.setattr(server, '_eidolon_home', member.home)
    monkeypatch.setenv('HERMES_HOME', str(member.home))
    before = disk_state(root)
    response = call(params={'expectedOrganizationId': member.identity['organizationId']})
    assert 'error' in response and str(root) not in str(response)
    assert disk_state(root) == before
    assert not (root / 'organization').exists()


def test_managed_installation_blocks_new_edits_but_preserves_immutable_reconciliation(served, monkeypatch):
    request, applied = apply(served)
    monkeypatch.setenv('HERMES_MANAGED', 'nix')
    current = read(served)
    assert current['managed'] is True
    assert current['capabilities'] == {'configuration': False, 'observation': False}
    before = disk_state(served.home)
    params = envelope(current, current['configuration'])
    response = call(OPERATIONS['configuration'][0], params)
    assert response['error']['data']['reason'] == 'managed_configuration'
    assert call(OPERATIONS['configuration'][1], request) == applied
    assert disk_state(served.home) == before


def test_passive_review_never_loads_profile_credentials_or_replaces_ambient_secrets(served, monkeypatch):
    from agent import secret_scope
    from eidolon_cli import env_loader
    def forbidden(*args, **kwargs):
        raise AssertionError('Shared allowance read attempted credential discovery')
    monkeypatch.setattr(secret_scope, 'build_profile_secret_scope', forbidden)
    monkeypatch.setattr(env_loader, 'hydrate_profile_secret_sources', forbidden)
    token = secret_scope.set_secret_scope({'PRIVATE_TEST_VALUE': 'not-shared'})
    try:
        current = read(served)
        params = envelope(current, configuration(served))
        response = call(OPERATIONS['configuration'][0], params)
        assert 'error' not in response, response
        assert secret_scope.current_secret_scope() == {'PRIVATE_TEST_VALUE': 'not-shared'}
        assert 'not-shared' not in json.dumps(response)
    finally:
        secret_scope.reset_secret_scope(token)

"""Owner policy authority is the live transport's immutable served launch home."""
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from queue import Queue
import sqlite3
from types import SimpleNamespace
import uuid

import pytest

from eidolon_constants import reset_eidolon_home_override, set_eidolon_home_override
from eidolon_cli import organization_owner_policy as owner_policy
import tui_gateway.server as server
from tests.eidolon_cli.test_organization_owner_policy import (
    create_profile, durable_dump, forbid_execution, observation_params, policy_params, routing,
)


METHODS = {
    'organization.ownerPolicy': None,
    'organization.previewModelRouting': 'modelRouting',
    'organization.configureModelRouting': 'modelRouting',
    'organization.previewBudgetPolicy': 'budgetPolicy',
    'organization.configureBudgetPolicy': 'budgetPolicy',
    'organization.previewBudgetObservation': 'budgetObservation',
    'organization.recordBudgetObservation': 'budgetObservation',
}
APPLY_METHODS = {'organization.configureModelRouting', 'organization.configureBudgetPolicy',
                 'organization.recordBudgetObservation'}


def call(method='organization.ownerPolicy', params=None, transport=None):
    responses = Queue()
    transport = transport or SimpleNamespace(closed=False)
    transport.write = responses.put
    request = {'jsonrpc': '2.0', 'id': 17, 'method': method, 'params': params if params is not None else {}}
    result = server.dispatch(request, transport=transport)
    return result if result is not None else responses.get(timeout=10)


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    home = tmp_path / '.eidolon'
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(server, '_eidolon_home', home)
    profile = create_profile(home)
    forbid_execution(monkeypatch)
    def forbidden_profile(*args, **kwargs):
        raise AssertionError('Owner policy widened authority through profile selection')
    monkeypatch.setattr(server, '_profile_home', forbidden_profile)
    return profile


def snapshot(profile):
    response = call(params={'expectedOrganizationId': profile.identity['organizationId']})
    assert 'error' not in response, response
    return response['result']


def params_for(profile, method, current=None):
    current = current or snapshot(profile)
    action = METHODS[method]
    if action is None:
        return {'expectedOrganizationId': current['organizationId']}
    if action == 'budgetObservation':
        params = observation_params(profile, current)
    else:
        configuration = routing() if action == 'modelRouting' else {'max_model_calls': 8}
        params = policy_params(current, configuration)
    if method in APPLY_METHODS:
        preview = owner_policy.preview_policy(profile.home, action, params)
        params.update(previewVersion=preview['previewVersion'], idempotencyKey=method, confirmed=True)
    return params


def test_real_rpc_all_endpoints_use_served_authority_without_service_or_store_construction(served):
    initial = snapshot(served)
    assert initial['scope'] == {'kind': 'served-profile', 'profile': 'default'}
    assert initial['state'] == 'ready'
    assert initial['organizationId'] == served.identity['organizationId']
    for preview_method, apply_method in (
        ('organization.previewModelRouting', 'organization.configureModelRouting'),
        ('organization.previewBudgetPolicy', 'organization.configureBudgetPolicy'),
        ('organization.previewBudgetObservation', 'organization.recordBudgetObservation'),
    ):
        before = durable_dump(served.path)
        params = params_for(served, preview_method)
        preview = call(preview_method, params)['result']
        assert preview['scope'] == initial['scope']
        assert durable_dump(served.path) == before
        request = {**params, 'previewVersion': preview['previewVersion'], 'idempotencyKey': apply_method, 'confirmed': True}
        applied = call(apply_method, request)
        assert 'error' not in applied, applied
        assert applied['result']['scope'] == initial['scope']
        assert call(apply_method, request) == applied
    assert not (served.home / 'state.db').exists()


@pytest.mark.parametrize('method', METHODS)
def test_missing_or_dead_transport_rejected_before_policy_access(served, method):
    params = params_for(served, method)
    before = durable_dump(served.path)
    request = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}
    assert server.handle_request(request)['error']['code'] == 4001
    assert call(method, params, SimpleNamespace(closed=True))['error']['code'] == 4001
    assert durable_dump(served.path) == before


@pytest.mark.parametrize('method', METHODS)
def test_unknown_profile_path_actor_and_route_fields_never_widen_authority(served, method):
    params = params_for(served, method)
    before = durable_dump(served.path)
    for extra in ({'profile': 'sibling'}, {'profile': 'default'}, {'path': str(served.home)},
                  {'home': str(served.home)}, {'actorId': 'owner'}, {'ownerId': served.identity['owner']['ownerId']},
                  {'organizationId': served.identity['organizationId']}, {'connectionId': 'another'},
                  {'providerCredentials': {'apiKey': 'do-not-store'}}):
        response = call(method, {**params, **extra})
        assert response['error']['code'] == -32602, response
    for malformed in ([], {}, {key: value for key, value in params.items() if key != 'expectedOrganizationId'}):
        assert call(method, malformed)['error']['code'] == -32602
    assert durable_dump(served.path) == before


@pytest.mark.parametrize('method', METHODS)
def test_expected_organization_is_cas_not_cross_profile_authority(served, method):
    params = params_for(served, method)
    before = durable_dump(served.path)
    response = call(method, {**params, 'expectedOrganizationId': str(uuid.uuid4())})
    assert 'error' in response
    assert str(served.home) not in str(response)
    assert durable_dump(served.path) == before


def test_context_override_and_copied_identity_cannot_redirect_policy_writes(served, monkeypatch):
    initial = snapshot(served)
    sibling = served.home / 'profiles' / 'sibling'
    sibling.mkdir(parents=True)
    sibling_path = sibling / 'organization' / 'state.db'
    sibling_path.parent.mkdir()
    (sibling / 'config.yaml').write_bytes((served.home / 'config.yaml').read_bytes())
    with closing(sqlite3.connect(served.path)) as source, closing(sqlite3.connect(sibling_path)) as target:
        source.backup(target)
    other_before = durable_dump(sibling_path)
    params = params_for(served, 'organization.configureBudgetPolicy', initial)
    token = set_eidolon_home_override(sibling)
    try:
        assert snapshot(served) == initial
        applied = call('organization.configureBudgetPolicy', params)
        assert 'error' not in applied, applied
        assert applied['result']['scope'] == initial['scope']
    finally:
        reset_eidolon_home_override(token)
    assert durable_dump(sibling_path) == other_before
    assert snapshot(served)['policyGeneration'] == initial['policyGeneration'] + 1
    monkeypatch.setattr(server, '_eidolon_home', sibling)
    independent = snapshot(served)
    assert independent['organizationId'] == initial['organizationId']
    assert independent['policyGeneration'] == initial['policyGeneration']
    assert independent['scope'] == {'kind': 'served-profile', 'profile': 'sibling'}
    assert independent['scope'] != initial['scope']


@pytest.mark.linux_only
def test_symlinked_ledger_is_rejected_before_any_endpoint_reads_or_writes(served):
    initial = snapshot(served)
    requests = {method: params_for(served, method, initial) for method in METHODS}
    old_directory = served.home / 'organization'
    outside = served.home.parent / 'unauthorized-ledger'
    old_directory.rename(outside)
    old_directory.symlink_to(outside, target_is_directory=True)
    before = durable_dump(outside / 'state.db')
    for method, params in requests.items():
        assert call(method, params)['error']['code'] == 4001
    assert durable_dump(outside / 'state.db') == before


def test_missing_identity_rpc_does_not_provision_any_organization_files(tmp_path, monkeypatch):
    home = tmp_path / '.eidolon'
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(server, '_eidolon_home', home)
    forbid_execution(monkeypatch)
    result = call(params={'expectedOrganizationId': str(uuid.uuid4())})['result']
    assert result['state'] == 'unconfigured' and result['reason']
    assert result['scope'] == {'kind': 'served-profile', 'profile': 'default'}
    assert not home.exists()


def test_rpc_rejects_changed_confirmation_and_preview_without_mutation(served):
    params = params_for(served, 'organization.configureBudgetPolicy')
    before = durable_dump(served.path)
    for change in ({'confirmed': False}, {'confirmed': 1}, {'previewVersion': '0' * 64},
                   {'configuration': {'max_model_calls': 9}}):
        response = call('organization.configureBudgetPolicy', {**deepcopy(params), **change})
        assert 'error' in response
        assert str(served.home) not in str(response)
    assert durable_dump(served.path) == before

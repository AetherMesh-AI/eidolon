"""Real served-transport dispatch through preview, bounded inference and creation."""
from copy import deepcopy
from pathlib import Path
from queue import Queue
import sqlite3
import threading
from types import SimpleNamespace
import uuid

import pytest

import tui_gateway.server as server
from eidolon_cli import organization_service as services
from eidolon_cli import organization_team_generation as generation
from eidolon_cli import organization_team_setup as team
from eidolon_constants import get_eidolon_home
from tests.eidolon_cli.test_organization_team_setup import profile, params, confirmed, dump
from tests.eidolon_cli.test_organization_team_admission import request, fake_provider, improvement_request


METHODS = ('teamSetup', 'previewTeam', 'generateTeam', 'createTeam', 'improveTeamInput')


def call(method, params, transport=None):
    responses = Queue()
    transport = transport or SimpleNamespace(closed=False)
    transport.write = responses.put
    result = server.dispatch({'jsonrpc': '2.0', 'id': 19, 'method': 'organization.' + method,
                              'params': params}, transport=transport)
    return result if result is not None else responses.get(timeout=15)


@pytest.fixture
def served(profile, monkeypatch):
    monkeypatch.setattr(server, '_eidolon_home', profile.home)
    return profile


def endpoint_params(profile, method):
    if method == 'teamSetup':
        return {'expectedOrganizationId': profile.identity['organizationId']}
    if method == 'previewTeam':
        return params(profile)
    if method == 'generateTeam':
        return request(profile)
    if method == 'improveTeamInput':
        return improvement_request(profile)
    return confirmed(profile)


def test_actual_rpc_setup_preview_create_and_exact_retry(served):
    current = call('teamSetup', endpoint_params(served, 'teamSetup'))['result']
    assert current['state'] == 'available'
    assert current['scope'] == {'kind': 'served-profile', 'profile': 'default'}
    before = dump(served)
    draft = params(served)
    preview = call('previewTeam', draft)['result']
    assert dump(served) == before
    apply = {**draft, 'previewVersion': preview['previewVersion'], 'idempotencyKey': 'rpc-create', 'confirmed': True}
    created = call('createTeam', apply)
    assert created['result']['state'] == 'created'
    assert call('createTeam', apply) == created
    assert call('teamSetup', endpoint_params(served, 'teamSetup'))['result']['state'] == 'configured'
    with sqlite3.connect(served.path) as conn:
        assert conn.execute('SELECT count(*) FROM requests').fetchone()[0] == 0


@pytest.mark.parametrize('method', METHODS)
def test_transport_identity_and_exact_fields_reject_before_mutation(served, method):
    value = endpoint_params(served, method)
    before = dump(served)
    raw = {'jsonrpc': '2.0', 'id': 19, 'method': 'organization.' + method, 'params': value}
    assert server.handle_request(raw)['error']['code'] == 4001
    assert call(method, value, SimpleNamespace(closed=True))['error']['code'] == 4001
    for extra in ({'profile': 'sibling'}, {'home': str(served.home)}, {'actorId': 'owner'}, {'provider': 'openai'}):
        assert call(method, {**value, **extra})['error']['code'] == -32602
    assert call(method, {**value, 'expectedOrganizationId': str(uuid.uuid4())})['error']['code'] == 5076
    assert dump(served) == before


def test_generation_rpc_uses_served_profile_and_replays_only_cached_result(served, monkeypatch):
    seen = []
    def generate(home, jobs, goals, naming, *, reserve_model_call, cancel):
        seen.append((home, get_eidolon_home(), cancel.is_set()))
        reserve_model_call(provider='fixture', model='fixture', input_limit=100, output_limit=100)
        return deepcopy(team.presets()[0]['team'])
    monkeypatch.setattr(generation, 'generate', generate)
    value = request(served)
    result = call('generateTeam', value)
    assert 'error' not in result, result
    assert result['result']['previewVersion']
    assert call('generateTeam', value) == result
    assert seen == [(served.home, served.home, False)]


def test_generation_runs_off_transport_loop_and_disconnect_keeps_charge(served, monkeypatch):
    entered, finish = threading.Event(), threading.Event()
    observed = []
    def generate(home, jobs, goals, naming, *, reserve_model_call, cancel):
        reserve_model_call(provider='fixture', model='fixture', input_limit=100, output_limit=100)
        entered.set()
        assert finish.wait(10)
        observed.append(cancel.is_set())
        raise InterruptedError('Disconnected after send')
    monkeypatch.setattr(generation, 'generate', generate)
    responses = Queue()
    transport = SimpleNamespace(closed=False, write=responses.put)
    req = {'jsonrpc': '2.0', 'id': 19, 'method': 'organization.generateTeam', 'params': request(served)}
    # Returning None proves that this blocking generation did not run inline.
    assert server.dispatch(req, transport=transport) is None
    try:
        assert entered.wait(10)
        transport.closed = True
    finally:
        finish.set()
    assert 'error' in responses.get(timeout=15)
    assert observed == [True]
    with sqlite3.connect(served.path) as conn:
        assert conn.execute('SELECT status,reserved_tokens FROM organization_team_generations').fetchone() == ('uncertain', 200)


def test_metadata_then_snapshot_initializes_fresh_team_without_starting_work(tmp_path, monkeypatch):
    home = tmp_path / '.eidolon'
    home.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(server, '_eidolon_home', home)
    services.stop_services()
    monkeypatch.setattr(services, '_services', {})
    def forbidden(*args, **kwargs):
        raise AssertionError('No execution during onboarding')
    monkeypatch.setattr(services, '_execute', forbidden)
    try:
        metadata = call('saveMetadata', {'displayName': 'Fresh', 'ownerDisplayName': 'Owner',
            'expectedOrganizationId': None, 'expectedRevision': None, 'idempotencyKey': 'initial'})['result']['metadata']
        expected = {'expectedOrganizationId': metadata['organizationId']}
        assert call('teamSetup', expected)['result']['state'] == 'unconfigured'
        snapshot = call('snapshot', {})['result']
        assert snapshot['objectives'] == []
        assert snapshot['requests'] == []
        assert call('teamSetup', expected)['result']['state'] == 'available'
        assert all(not service.running for service in services._services.values())
    finally:
        services.stop_services()


def test_improvement_rpc_is_served_scoped_off_loop_and_returns_only_review_text(served, monkeypatch):
    entered, finish = threading.Event(), threading.Event()
    seen = []
    def improve(home, field, jobs, goals, naming, *, reserve_model_call, cancel):
        seen.append((home, get_eidolon_home(), field, jobs, goals, naming, cancel.is_set()))
        reserve_model_call(provider='fixture', model='fixture', input_limit=100, output_limit=100)
        entered.set()
        assert finish.wait(10)
        return {'suggestion': 'Write evidence-backed reports.', 'assumptions': ['Reports should cite sources.']}
    monkeypatch.setattr(generation, 'improve', improve)
    value = improvement_request(served)
    responses = Queue()
    transport = SimpleNamespace(closed=False, write=responses.put)
    req = {'jsonrpc': '2.0', 'id': 19, 'method': 'organization.improveTeamInput', 'params': value}
    assert server.dispatch(req, transport=transport) is None
    try:
        assert entered.wait(10)
    finally:
        finish.set()
    response = responses.get(timeout=15)
    assert 'error' not in response, response
    result = response['result']
    assert result == {'capabilityVersion': 1, 'organizationId': value['expectedOrganizationId'],
        'sourceRevision': value['sourceRevision'], 'field': 'jobs',
        'suggestion': 'Write evidence-backed reports.', 'assumptions': ['Reports should cite sources.'],
        'generationAllowance': {'status': 'available', 'remainingCalls': 2},
        'scope': {'kind': 'served-profile', 'profile': 'default'}}
    assert call('improveTeamInput', value) == response
    assert seen == [(served.home, served.home, 'jobs', value['jobs'], '', '', False)]


@pytest.mark.parametrize('change', ['disconnect', 'served-home'])
def test_improvement_cannot_publish_into_changed_transport_scope(served, monkeypatch, tmp_path, change):
    transport = SimpleNamespace(closed=False)
    def improve(home, field, jobs, goals, naming, *, reserve_model_call, cancel):
        reserve_model_call(provider='fixture', model='fixture', input_limit=100, output_limit=100)
        if change == 'disconnect':
            transport.closed = True
        else:
            monkeypatch.setattr(server, '_eidolon_home', tmp_path / 'other')
        return {'suggestion': 'Suggested wording', 'assumptions': []}
    monkeypatch.setattr(generation, 'improve', improve)
    assert call('improveTeamInput', improvement_request(served), transport)['error']['code'] == 4001


def test_setup_budget_rpc_requires_exact_review_and_keeps_team_fresh(served):
    identity = {'expectedOrganizationId': served.identity['organizationId']}
    before = dump(served)
    state = call('setupBudgetPolicy', identity)['result']
    assert state['configuration'] is None
    assert state['suggestedConfiguration']['totalCallLimit'] == 7
    assert dump(served) == before
    value = {**identity, 'expectedGeneration': state['policyGeneration'],
             'sourceRevision': state['sourceRevision'], 'configuration': state['suggestedConfiguration']}
    assert call('previewSetupBudget', {**value, 'profile': 'sibling'})['error']['code'] == -32602
    preview = call('previewSetupBudget', value)['result']
    assert dump(served) == before
    apply = {**value, 'previewVersion': preview['previewVersion'], 'idempotencyKey': 'rpc-budget', 'confirmed': True}
    assert call('applySetupBudget', {**apply, 'confirmed': False})['error']['code'] == -32602
    applied = call('applySetupBudget', apply)
    assert applied['result']['receipt']['action'] == 'setupBudget'
    assert call('applySetupBudget', apply) == applied
    current = call('teamSetup', identity)['result']
    assert current['state'] == 'available'
    assert current['generationAllowance']['remainingCalls'] == 7
    state = call('setupBudgetPolicy', identity)['result']
    assert state['latestReceipt'] == applied['result']['receipt']
    with sqlite3.connect(served.path) as conn:
        assert conn.execute('SELECT count(*) FROM requests').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM organization_team_generations').fetchone()[0] == 0

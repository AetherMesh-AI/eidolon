"""Authenticated RPC, durable intake and admission using offline inference."""
import json
import time
import pytest
from eidolon_cli import organization_service as services
import tui_gateway.server as gateway


def rpc(method, **params):
    response = gateway.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
    assert 'error' not in response, response
    return response['result']


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    assert services.stop_services()
    monkeypatch.setattr(services, '_services', {})
    monkeypatch.setattr(services.OrganizationService, 'start', lambda _: None)
    yield
    assert services.stop_services()


def test_intake_rpc_admits_exactly_once_and_reads_are_passive(monkeypatch):
    calls = []
    def execute(prompt, *, reserve_model_call, cancel):
        reserve_model_call(provider='fixture', model='small', input_limit=1000, output_limit=1000)
        calls.append(prompt)
        return {'raw': json.dumps(dict(snapshotId=prompt['organization']['snapshotId'], routeId='manager',
            projectIds=[], deliveryMode='managed_artifact', priority='P4', rationale='Cosmetic work',
            evidenceIds=['owner-request'], acceptanceCriteria=['Describe requested emojis'], assumptions=[],
            duplicateCandidateIds=[], dependencyCandidateIds=[], question=None)),
            'usage': {'inputTokens': 50, 'outputTokens': 20}}
    monkeypatch.setattr('eidolon_cli.organization_intake_executor.execute', execute)
    first = rpc('organization.intake.submit', text='Describe emoji choices', idempotencyKey='one')
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        latest = rpc('organization.intake.read', intakeId=first['id'])
        if latest['status'] not in ('pending', 'proposed'):
            break
        time.sleep(.01)
    assert latest['status'] == 'admitted'
    assert latest['proposal']['managerId'] == 'manager'
    assert latest['usage']['inputTokens'] == 50
    again = rpc('organization.intake.submit', text='Describe emoji choices', idempotencyKey='one')
    assert again['objectiveId'] == latest['objectiveId'] and len(calls) == 1


def test_rpc_rejects_missing_transport_and_forged_authority():
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'organization.intake.submit',
               'params': {'text': 'work', 'idempotencyKey': 'x'}}
    assert gateway.handle_request(request)['error']['code'] == 4001
    for extra in ({'managerId': 'other'}, {'path': '/other'}, {'profile': '../other'}, {'ownerPriority': []}):
        response = gateway.dispatch({**request, 'params': {**request['params'], **extra}})
        assert response['error']['code'] == -32602


def test_recent_rpc_restores_durable_question_without_model_dispatch(monkeypatch):
    service = services.get_service()
    row, _ = service.store.begin_intake('Which project?', idempotency_key='persisted')
    service.store.reserve_intake_call(row['id'], provider='fixture', model='small', input_limit=1000, output_limit=1000)
    proposal = dict(snapshotId=row['snapshotId'], routeId=None, projectIds=[], deliveryMode='managed_artifact',
        priority='P3', rationale='Need target', evidenceIds=['owner-request'], acceptanceCriteria=[], assumptions=[],
        duplicateCandidateIds=[], dependencyCandidateIds=[], question={'text': 'Which project?',
            'neededFor': 'No unique target', 'checkedEvidenceIds': ['owner-request']})
    service.store.finish_intake(row['id'], json.dumps(proposal))
    assert services.stop_services()
    monkeypatch.setattr(services, '_services', {})
    monkeypatch.setattr('eidolon_cli.organization_intake_executor.execute', lambda **_: pytest.fail('read dispatched'))
    restored = rpc('organization.intake.list')
    assert len(restored['items']) == 1
    assert restored['items'][0]['id'] == row['id']
    assert restored['items'][0]['status'] == 'needs_answer'
    assert restored['items'][0]['profile'] == restored['profile']
    assert not services.get_service().store.snapshot()['objectives']


def test_cold_reads_never_construct_service_or_recover(monkeypatch):
    from eidolon_constants import get_eidolon_home
    from eidolon_cli.organization_store import OrganizationStore
    home = get_eidolon_home()
    store = OrganizationStore(home / 'organization' / 'state.db')
    row, _ = store.begin_intake('Interrupted', idempotency_key='orphan')
    store.reserve_intake_call(row['id'], provider='fixture', model='small', input_limit=1000, output_limit=1000)
    before = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob('*') if p.is_file() and not p.name.endswith('-shm') and not (p.name.endswith('-wal') and p.stat().st_size == 0)}
    monkeypatch.setattr(services, 'get_service', lambda: pytest.fail('passive read constructed service'))
    monkeypatch.setattr('eidolon_cli.organization_intake_executor.execute', lambda **_: pytest.fail('read dispatched'))
    assert rpc('organization.intake.list')['items'][0]['status'] == 'pending'
    assert rpc('organization.intake.read', intakeId=row['id'])['usage']['reservedTokens'] == 2000
    assert not services._services
    assert {str(p.relative_to(home)): p.read_bytes() for p in home.rglob('*') if p.is_file() and not p.name.endswith('-shm') and not (p.name.endswith('-wal') and p.stat().st_size == 0)} == before


def test_recovery_requires_authenticated_transport():
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'organization.intake.recover', 'params': {}}
    assert gateway.handle_request(request)['error']['code'] == 4001


def test_inspection_policy_read_on_empty_profile_never_constructs_service(monkeypatch):
    from eidolon_constants import get_eidolon_home
    home = get_eidolon_home()
    before = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob('*') if p.is_file()}
    monkeypatch.setattr(services, 'get_service', lambda: pytest.fail('inspection read constructed service'))
    view = rpc('organization.intake.inspection.read')
    assert view['enabled'] is False and view['revision'] == 0
    assert not services._services
    assert {str(p.relative_to(home)): p.read_bytes() for p in home.rglob('*') if p.is_file()} == before


def test_inspection_configuration_requires_owner_transport_and_confirmation():
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'organization.intake.inspection.configure',
        'params': {'expectedOrganizationId': 'synthetic', 'expectedRevision': 0, 'enabled': False,
                   'workerId': None, 'projectIds': [], 'paths': [], 'confirmed': False, 'idempotencyKey': 'policy'}}
    assert gateway.handle_request(request)['error']['code'] == 4001
    assert gateway.dispatch(request)['error']['code'] == -32602

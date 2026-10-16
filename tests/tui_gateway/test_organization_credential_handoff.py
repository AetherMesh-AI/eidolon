"""Actual owner RPC -> live scheduler capsule -> synthetic native storage."""
import json

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_credential_agent import scheduler_capsule
from eidolon_cli.organization_credential_security import CredentialError
from eidolon_cli.organization_credential_service import credential_agents, owner_catalog
from eidolon_cli.organization_store import OrganizationStore
from tests.organization_package_helpers import claim_after_decomposition
from tests.tui_gateway.test_organization_credentials import call, mutation, served  # noqa: F401
from tui_gateway.organization_credential_transport import register_owner, retire_owner


@pytest.fixture
def delegated(served):
    home, base, transport, backend = served
    store = OrganizationStore(home / 'organization' / 'state.db', OrganizationSettings())
    store.create_objective('Synthetic delegated replacement', idempotency_key='objective')
    planner = claim_after_decomposition(store)
    store.finish(planner, {'tasks': [{'title': 'Maintain catalog', 'description': 'Synthetic local catalog operation',
        'type': 'work.draft', 'team': 'general', 'dependsOn': []}]})
    claim = store.claim_next()
    entry = call(transport, 'mutate', mutation(base))['result']
    catalog, owner = owner_catalog(home, base['expectedOrganizationId'], base['expectedOwnerId'])
    actor = next(item for item in credential_agents(home) if item['id'] == claim['agent_id'])
    catalog.configure_grant(owner, actor_id=actor['identityId'], scope=entry['entryId'],
        permissions=['replace_value'], bindings=[], expected_revision=0, operation_id='grant')
    capsule = scheduler_capsule(home, store, claim, lambda: False)
    response = call(transport, 'replacementTargets', {**base, 'entryId': entry['entryId'], 'expectedRevision': 1})
    assert 'result' in response, response
    target = response['result']['targets'][0]
    params = {**base, **{key: target[key] for key in ('actorId', 'requestId', 'generation')},
        'entryId': entry['entryId'], 'expectedRevision': 1, 'expectedGrantRevision': target['grantRevision'], 'operationId': 'prepare',
        'value': 'SYNTHETIC_DELEGATED_RPC_7ee2', 'confirmed': True}
    yield home, base, transport, backend, store, claim, capsule, catalog, owner, params
    capsule.retire()


def test_owner_prepares_worker_replaces_once_without_input_handle_or_value_exposure(delegated, caplog):
    home, base, transport, backend, store, claim, capsule, catalog, owner, params = delegated
    before = set(catalog.inputs._pending)
    preparation = call(transport, 'prepareReplacement', params)['result']
    private_handles = set(catalog.inputs._pending) - before
    assert len(private_handles) == 1
    metadata = capsule.invoke('organization_credential_catalog', {})
    assert metadata['entries'] == []  # Replacement does not imply metadata discovery.
    assert metadata['replacementHandoffs'][0]['handoffId'] == preparation['handoffId']
    assert call(transport, 'prepareReplacement', {**params, 'value': 'SYNTHETIC_IGNORED_DUPLICATE'})['result'] == preparation
    assert set(catalog.inputs._pending) - before == private_handles
    args = {'handoffId': preparation['handoffId']}
    result = capsule.invoke('organization_credential_replace', args)
    assert result['action'] == 'replace_value' and result['revision'] == 2 and result['version'] == 1
    assert list(backend.values.values()) == [params['value'].encode()]
    assert capsule.invoke('organization_credential_replace', args) == result
    assert len(backend.values) == 1
    status = call(transport, 'replacementStatus', {**base, 'operationId': 'prepare'})['result']
    cancelled = call(transport, 'cancelReplacement', {**base, 'operationId': 'prepare', 'confirmed': True})['result']
    assert status['state'] == cancelled['state'] == 'committed'
    assert status['receipt'] == cancelled['receipt'] == result
    exposed = json.dumps([preparation, metadata, result, status, cancelled]) + caplog.text + catalog.path.read_bytes().decode('latin1')
    assert params['value'] not in exposed
    assert all(handle not in exposed for handle in private_handles)
    for raw in ({**args, 'value': 'SYNTHETIC_MODEL_TEXT'}, {**args, 'actorId': owner.actor_id},
                {**args, 'entryId': params['entryId']}):
        with pytest.raises(CredentialError, match='invalid_parameters'):
            capsule.invoke('organization_credential_replace', raw)


@pytest.mark.parametrize('retire', ['disconnect', 'same-object-new-generation', 'capsule'])
def test_input_authority_never_survives_route_or_worker_retirement(delegated, retire):
    home, base, transport, backend, store, claim, capsule, catalog, owner, params = delegated
    preparation = call(transport, 'prepareReplacement', params)['result']
    if retire == 'capsule':
        capsule.retire()
        capsule.retire()
    else:
        retire_owner(transport)
        register_owner(transport, home)
        if retire == 'disconnect':
            transport.closed = True
    with pytest.raises(CredentialError):
        capsule.invoke('organization_credential_replace', {'handoffId': preparation['handoffId']})
    assert not backend.values
    transport.closed = False
    register_owner(transport, home)
    status = call(transport, 'replacementStatus', {**base, 'operationId': 'prepare'})['result']
    assert status['state'] == 'unavailable'
    cancelled = call(transport, 'cancelReplacement', {**base, 'operationId': 'prepare', 'confirmed': True})['result']
    assert cancelled['state'] == 'cancelled'


def test_cancel_before_prepare_and_prepared_cancel_block_delayed_worker(delegated):
    home, base, transport, backend, store, claim, capsule, catalog, owner, params = delegated
    cancel = {**base, 'operationId': 'prepare', 'confirmed': True}
    assert call(transport, 'cancelReplacement', {**cancel, 'confirmed': False})['error']['data']['reason'] == 'confirmation_required'
    assert call(transport, 'cancelReplacement', cancel)['result']['state'] == 'cancelled'
    assert call(transport, 'prepareReplacement', params)['error']['data']['reason'] == 'operation_cancelled'
    next_params = {**params, 'operationId': 'next-prepare'}
    preparation = call(transport, 'prepareReplacement', next_params)['result']
    assert call(transport, 'cancelReplacement', {**cancel, 'operationId': 'next-prepare'})['result']['state'] == 'cancelled'
    with pytest.raises(CredentialError):
        capsule.invoke('organization_credential_replace', {'handoffId': preparation['handoffId']})
    assert not backend.values


def test_owner_admission_and_live_request_generation_are_not_client_asserted(delegated):
    home, base, transport, backend, store, claim, capsule, catalog, owner, params = delegated
    for override in ({'expectedOwnerId': 'other'}, {'actorId': 'other'}, {'requestId': 'other'},
                     {'generation': 'other'}, {'expectedRevision': 2}, {'expectedGrantRevision': 2},
                     {'expectedGrantRevision': True}, {'confirmed': False}):
        assert 'error' in call(transport, 'prepareReplacement', {**params, **override})
    assert 'error' in call(transport, 'prepareReplacement', {**params, 'inputHandle': 'client-asserted'})
    with store._write() as conn:
        conn.execute('UPDATE requests SET token=? WHERE id=?', ('reassigned', claim['id']))
    assert call(transport, 'replacementTargets', {**base, 'entryId': params['entryId'], 'expectedRevision': 1})['result']['targets'] == []
    assert 'error' in call(transport, 'prepareReplacement', params)
    assert not backend.values

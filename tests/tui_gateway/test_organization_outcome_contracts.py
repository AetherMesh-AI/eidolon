"""Outcome judgment authority is the served endpoint, not a model/profile field."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import tui_gateway.server as server
from eidolon_cli import organization_service as services
from eidolon_cli.organization_metadata import save_metadata
from eidolon_cli.organization_store import OrganizationStore
from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override
from tests.eidolon_cli.test_organization_outcome_contracts import prepare, judgment_args
from tests.tui_gateway.test_organization_owner_policy import call

METHOD = 'organization.recordOutcomeJudgment'


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    home=tmp_path/'served'; home.mkdir()
    monkeypatch.setattr(server,'_eidolon_home',home)
    store=OrganizationStore(home/'organization'/'state.db')
    identity=save_metadata(store.path,display_name='Example',owner_display_name='Owner',
        expected_organization_id=None,expected_revision=None,idempotency_key='metadata')['metadata']
    objective=prepare(store,'human_judgment')
    integrate=store.claim_next();store.finish(integrate,{'summary':'Combined','deliverable':'Choose A.'})
    service=SimpleNamespace(store=store,start=Mock())
    monkeypatch.setattr(services,'get_service',lambda: service)
    args=judgment_args(store)
    params={'expectedOrganizationId':identity['organizationId'],'objectiveId':objective['id'],
        'criterionId':args['criterion_id'],'contractSha256':args['contract_sha256'],
        'deliverableSha256':args['deliverable_sha256'],'approved':True,'reason':args['reason'],
        'idempotencyKey':'rpc-owner','confirmed':True}
    return service,params,home


def test_owner_rpc_uses_immutable_served_home_and_preserves_ambient_override(fixture,tmp_path):
    service,params,home=fixture
    token=set_eidolon_home_override(tmp_path/'unrelated')
    try:
        response=call(METHOD,params)
    finally:
        reset_eidolon_home_override(token)
    assert 'error' not in response,response
    assert response['result']['judgment']['recorded'] is True
    assert service.start.call_count==1
    assert service.store.claim_next()['type']=='request.accept'


@pytest.mark.parametrize('change',[{'profile':'sibling'},{'confirmed':False},{'expectedOrganizationId':'other'},
    {'contractSha256':'0'*64},{'deliverableSha256':'0'*64},{'actorId':'owner'}])
def test_owner_rpc_rejects_spoofed_scope_or_stale_proof(fixture,change):
    service,params,_=fixture
    response=call(METHOD,{**params,**change})
    assert 'error' in response,response
    assert service.start.call_count==0
    with service.store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM objective_human_judgments').fetchone()[0]==0


def test_closed_transport_cannot_record_judgment(fixture):
    service,params,_=fixture
    response=call(METHOD,params,transport=SimpleNamespace(closed=True))
    assert 'error' in response
    assert service.start.call_count==0

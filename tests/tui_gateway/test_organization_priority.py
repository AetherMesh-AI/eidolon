"""Profile-bound owner priority RPC without scheduler startup or provider work."""
from types import SimpleNamespace

from eidolon_cli import organization_service as services
from eidolon_cli.organization_store import OrganizationStore
from eidolon_constants import get_eidolon_home
import tui_gateway.server as server


def test_priority_rpc_uses_authenticated_profile_and_rejects_cross_profile_or_extra_authority(tmp_path, monkeypatch):
    from eidolon_cli import profiles
    root = tmp_path/'profiles-root'
    monkeypatch.setattr(profiles, '_get_profiles_root', lambda: root/'profiles')
    monkeypatch.setattr(profiles, '_get_default_eidolon_home', lambda: root)
    stores = {}
    for name in ('alpha','beta'):
        home = root/'profiles'/name
        home.mkdir(parents=True)
        (home/'config.yaml').write_text('organization: {}\n')
        stores[home.resolve()] = OrganizationStore(home/'organization'/'state.db')
    monkeypatch.setattr(services, 'get_service', lambda: SimpleNamespace(store=stores[get_eidolon_home().resolve()]))
    def forbidden(*args, **kwargs):raise AssertionError('Priority must not execute or start work')
    monkeypatch.setattr(services, '_execute', forbidden)
    monkeypatch.setattr(services.OrganizationService, 'start', forbidden)
    alpha = stores[(root/'profiles'/'alpha').resolve()]
    objective = alpha.create_objective('Goal', priority='P5', idempotency_key='goal')
    params = {'profile':'alpha','id':objective['id'],'priority':'P1','expectedRevision':0,'idempotencyKey':'change'}
    def rpc(values):return server.dispatch({'jsonrpc':'2.0','id':1,'method':'organization.changePriority','params':values})
    response = rpc(params)
    assert 'error' not in response, response
    assert response['result']['receipt']['priority'] == 'P1'
    assert response['result']['snapshot']['runtime']['profile'] == 'alpha'
    assert rpc(params)['result']['receipt'] == response['result']['receipt']
    for changes in ({'profile':'beta'}, {'profile':'../alpha'}, {'actorId':'executive'}, {'priority':'P0'}, {'expectedRevision':True}):
        assert 'error' in rpc({**params, **changes})
    with monkeypatch.context() as unbound:
        unbound.setattr(server, 'current_transport', lambda: None)
        assert rpc(params)['error']['code'] == 4001
    assert alpha._objective_view(objective['id'])['priorityRevision'] == 1
    assert stores[(root/'profiles'/'beta').resolve()].snapshot()['objectives'] == []

    # Actual archive projection excludes the row; exact provenance and receipt survive.
    alpha.cancel(objective['id'])
    alpha.set_objective_archived(objective['id'], True, expected_revision=0, idempotency_key='archive')
    assert alpha.snapshot()['objectives'] == []
    replay = rpc(params)['result']
    assert replay['receipt'] == response['result']['receipt']
    assert replay['profile'] == 'alpha' and replay['snapshot']['runtime']['profile'] == 'alpha'
    assert replay['snapshot']['objectives'] == []
    assert replay['objective']['id'] == objective['id']
    assert replay['objective']['history']['archived'] is True
    assert replay['objective']['priority'] == 'P1' and replay['objective']['priorityRevision'] == 1
    assert 'error' in rpc({**params, 'idempotencyKey':'new', 'priority':'P2', 'expectedRevision':1})
    assert 'error' in rpc({**params, 'profile':'beta'})
    with alpha._connect() as conn:
        assert conn.execute('SELECT count(*) FROM priority_changes').fetchone()[0] == 1

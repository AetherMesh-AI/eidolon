"""Offline accounting fixtures: no provider calls or real goal enablement."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import time

import pytest

from eidolon_cli.organization_budget import OrganizationModelCost
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_goal_allowances import goal_projection, goal_for_objective, require_goal_admission, inherit_goal_membership
from eidolon_cli.organization_store import OrganizationStore


def limits(**changes):
    return {'modelCalls':10,'tokens':10000,'costUsd':None,'stages':100,'projectRuns':5,
            'deadlineTimestamp':int(time.time())+3600,'completionReserve':{'modelCalls':0,'tokens':0,'costUsd':None}, **changes}


def setup(tmp_path, priced=False):
    settings = OrganizationSettings(model_costs=(OrganizationModelCost('local','fixture','1','1'),) if priced else ())
    store = OrganizationStore(tmp_path/'state.db',settings)
    objectives = [store.create_objective('Work '+str(n), idempotency_key='objective'+str(n))['id'] for n in range(2)]
    claims = []
    # Construct concurrently owned claims solely for reservation-race testing.
    with store._write() as conn:
        for index, objective in enumerate(objectives):
            request = conn.execute('SELECT * FROM requests WHERE objective_id=?',(objective,)).fetchone()
            store._stamp_claim_policy(conn,request['id'])
            conn.execute("UPDATE requests SET status='running',token=?,lease=?,agent_id='executive' WHERE id=?",(str(index),time.time()+3600,request['id']))
            claims.append(dict(conn.execute('SELECT * FROM requests WHERE id=?',(request['id'],)).fetchone()))
    return store,objectives,claims


def configure(store, objectives, value=None, goal='goal', rev=0, key=None, paused=False):
    return store.configure_goal_allowance(goal,objective_ids=objectives,limits=value or limits(),paused=paused,
        expected_revision=rev,idempotency_key=key or f'{goal}-{rev}',confirmed=True)


def reserve(store, claim):
    return store.reserve_model_call(claim,provider='local',model='fixture',input_limit=100,output_limit=100)


def view(store):
    with store._connect() as conn:
        return goal_projection(conn,'goal')


def test_concurrent_objectives_share_one_atomic_call_ceiling(tmp_path):
    store, objectives, claims = setup(tmp_path)
    configure(store,objectives,limits(modelCalls=1))
    peers = [OrganizationStore(store.path,store.settings) for _ in claims]
    def attempt(pair):
        try: return reserve(*pair)
        except ValueError: return None
    with ThreadPoolExecutor(2) as pool:
        values=list(pool.map(attempt,zip(peers,claims)))
    assert len([value for value in values if value]) == 1
    assert view(store)['reserved']['modelCalls'] == 1
    assert view(store)['reserved']['tokens'] == 200
    assert view(store)['reserved']['costUsd'] is None


def test_existing_reservations_count_and_membership_cannot_launder_spend(tmp_path):
    store,objectives,claims=setup(tmp_path)
    reserve(store,claims[0])
    configure(store,objectives,limits(modelCalls=1))
    with pytest.raises(ValueError): reserve(store,claims[1])
    with pytest.raises(ValueError,match='cannot move'): configure(store,[objectives[0]],goal='other')
    with pytest.raises(ValueError,match='cannot be removed'): configure(store,[objectives[1]],rev=1)
    assert view(store)['reserved']['modelCalls'] == 1
    configure(store,objectives,limits(modelCalls=2),rev=1)
    reserve(store,claims[1])
    assert view(store)['reserved']['modelCalls'] == 2


def test_configured_money_uses_exact_canonical_reservations(tmp_path):
    store,objectives,claims=setup(tmp_path,priced=True)
    value=limits(costUsd='0.0003',completionReserve={'modelCalls':0,'tokens':0,'costUsd':'0'})
    configure(store,objectives,value)
    reserve(store,claims[0])
    assert view(store)['reserved']['costUsd'] == '0.000200000'
    with pytest.raises(ValueError,match='configured-money'): reserve(store,claims[1])
    assert view(store)['reserved']['modelCalls'] == 1


def test_unknown_price_or_legacy_usage_never_becomes_zero(tmp_path):
    store,objectives,claims=setup(tmp_path)
    value=limits(costUsd='1',completionReserve={'modelCalls':0,'tokens':0,'costUsd':'0'})
    configure(store,objectives,value)
    with pytest.raises(ValueError,match='known conservative'): reserve(store,claims[0])
    with store._write() as conn: conn.execute('UPDATE objective_budgets SET legacy_usage_unknown=1 WHERE objective_id=?',(objectives[1],))
    assert view(store)['legacyUsageUnknown'] is True
    with pytest.raises(ValueError,match='unknown historical'): reserve(store,claims[0])


def test_completion_reserve_protects_headroom_across_objectives(tmp_path):
    store,objectives,claims=setup(tmp_path)
    configure(store,objectives,limits(modelCalls=2,completionReserve={'modelCalls':1,'tokens':200,'costUsd':None}))
    reserve(store,claims[0])
    with pytest.raises(ValueError,match='completion reserve'): reserve(store,claims[1])
    with store._write() as conn:
        ident=store._request(conn,objectives[1],'request.accept',store.settings.team,0)
        store._stamp_claim_policy(conn,ident)
        conn.execute("UPDATE requests SET status='running',token='completion',lease=?,agent_id='executive' WHERE id=?",(time.time()+3600,ident))
        completion_claim=dict(conn.execute('SELECT * FROM requests WHERE id=?',(ident,)).fetchone())
    reserve(store,completion_claim)
    assert view(store)['reserved']['modelCalls'] == 2


def test_pause_deadline_and_project_stage_caps_do_not_refund(tmp_path):
    store,objectives,claims=setup(tmp_path)
    configure(store,objectives,limits(projectRuns=0))
    reserve(store,claims[0])
    with store._connect() as conn:
        with pytest.raises(ValueError,match='projectRuns'): require_goal_admission(conn,objectives[0],project_run=True)
    configure(store,objectives,limits(),rev=1,paused=True)
    with pytest.raises(ValueError,match='paused'): reserve(store,claims[1])
    configure(store,objectives,limits(deadlineTimestamp=int(time.time())-1),rev=2)
    with pytest.raises(ValueError,match='deadline'): reserve(store,claims[1])
    assert view(store)['reserved']['modelCalls'] == 1


def test_replacement_membership_retains_original_cost_after_restart(tmp_path):
    store,objectives,claims=setup(tmp_path)
    configure(store,[objectives[0]],limits(modelCalls=1));reserve(store,claims[0])
    with store._write() as conn: inherit_goal_membership(conn,objectives[0],objectives[1])
    reopened=OrganizationStore(store.path,store.settings)
    with pytest.raises(ValueError): reserve(reopened,claims[1])
    with reopened._connect() as conn:
        assert goal_for_objective(conn,objectives[1])['reserved']['modelCalls'] == 1
        with pytest.raises(sqlite3.IntegrityError,match='immutable'): conn.execute('DELETE FROM organization_goal_members')


def test_receipt_replay_and_failure_rollback(tmp_path):
    store,objectives,_=setup(tmp_path)
    value=limits();receipt=configure(store,objectives,value)
    assert configure(store,objectives,value) == receipt
    with pytest.raises(ValueError,match='different input'): configure(store,objectives,limits(modelCalls=2))
    with store._connect() as conn:
        conn.execute("CREATE TRIGGER block_goal_receipt BEFORE INSERT ON organization_goal_receipts BEGIN SELECT RAISE(ABORT,'Injected'); END;")
    with pytest.raises(sqlite3.IntegrityError): configure(store,objectives,limits(),rev=1)
    assert view(store)['revision'] == 1


def test_passive_legacy_and_partial_storage(tmp_path):
    store,objectives,_=setup(tmp_path)
    with store._connect() as conn:
        for table in ('organization_goal_members','organization_goal_receipts','organization_goal_revisions'): conn.execute('DROP TABLE '+table)
        before='\n'.join(conn.iterdump())
        assert goal_for_objective(conn,objectives[0]) is None
        assert '\n'.join(conn.iterdump()) == before
        conn.execute('CREATE TABLE organization_goal_members(x TEXT)')
        with pytest.raises(ValueError,match='incomplete'): goal_for_objective(conn,objectives[0])


def test_concurrent_goal_creation_cannot_claim_same_objective_twice(tmp_path):
    store,objectives,_=setup(tmp_path)
    peers=[OrganizationStore(store.path,store.settings) for _ in range(2)]
    def attempt(pair):
        peer,goal=pair
        try: return configure(peer,[objectives[0]],goal=goal)
        except ValueError: return None
    with ThreadPoolExecutor(2) as pool: results=list(pool.map(attempt,zip(peers,['one','two'])))
    assert sum(value is not None for value in results)==1
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM organization_goal_revisions').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM organization_goal_members').fetchone()[0]==1


def test_actual_objective_replacement_inherits_exhausted_stage_ceiling(tmp_path,monkeypatch):
    from tests.eidolon_cli.test_organization_replacement import expired,submit
    store,objective,_,_=expired(tmp_path,monkeypatch)
    with store._connect() as conn: spent=conn.execute('SELECT count(*) FROM objective_usage').fetchone()[0]
    assert spent>0
    configure(store,[objective['id']],limits(stages=spent))
    replacement=submit(store,store.preview_replacement(objective['id']))
    with store._connect() as conn:
        value=goal_for_objective(conn,replacement['id'])
        assert value['reserved']['stages']==spent
        assert set(value['objectiveIds'])=={objective['id'],replacement['id']}
    assert store.claim_next() is None
    assert view(store)['reserved']['stages']==spent


@pytest.mark.parametrize('bad', [{'modelCalls':True},{'tokens':0},{'projectRuns':-1},{'deadlineTimestamp':float('nan')},
                                  {'costUsd':'NaN'},{'completionReserve':{'modelCalls':11,'tokens':0,'costUsd':None}}])
def test_invalid_goal_limits_fail_without_state_changes(tmp_path,bad):
    store,objectives,_=setup(tmp_path)
    with pytest.raises(ValueError): configure(store,objectives,limits(**bad))
    with store._connect() as conn: assert conn.execute('SELECT count(*) FROM organization_goal_revisions').fetchone()[0]==0


def test_retroactive_goal_adoption_requires_entire_replacement_lineage(tmp_path,monkeypatch):
    from tests.eidolon_cli.test_organization_replacement import expired,submit
    store,objective,_,_=expired(tmp_path,monkeypatch)
    replacement=submit(store,store.preview_replacement(objective['id']))
    with store._connect() as conn:
        spent=conn.execute('SELECT count(*) FROM objective_usage').fetchone()[0]
    for ident in [objective['id'],replacement['id']]:
        with pytest.raises(ValueError,match='complete replacement lineage'):
            configure(store,[ident],limits(stages=spent))
    configure(store,[objective['id'],replacement['id']],limits(stages=spent))
    assert view(store)['reserved']['stages']==spent
    assert store.claim_next() is None
    with pytest.raises(ValueError,match='cannot move'):
        configure(store,[objective['id'],replacement['id']],goal='fresh')


def test_recovered_undispatched_claim_cannot_reset_goal_stage_ceiling(tmp_path):
    from tests.eidolon_cli.test_organization_selective_recovery import prepare,expire,status
    store=prepare(tmp_path/'state.db')
    claim=store.claim_next(recoverable=True)
    configure(store,[claim['objective_id']],limits(stages=1))
    expire(store,claim)
    assert store.recover_expired()==1
    assert status(store,claim)['status']=='queued'
    with store._write() as conn:
        conn.execute('UPDATE requests SET available=0 WHERE id=?',(claim['id'],))
    assert store.claim_next(recoverable=True) is None
    assert view(store)['reserved']['stages']==1


def test_cancel_and_archive_keep_spent_goal_member_reservations(tmp_path):
    store,objectives,claims=setup(tmp_path)
    configure(store,objectives,limits(modelCalls=1))
    reserve(store,claims[0])
    store.cancel(objectives[0])
    store.set_objective_archived(objectives[0],True,expected_revision=0,idempotency_key='archive')
    assert view(store)['reserved']['modelCalls']==1
    with pytest.raises(ValueError): reserve(store,claims[1])

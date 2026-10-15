"""Finite continuation tests use only synthetic ledger state, never providers."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import time

import pytest

from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_goal_continuation import _records
from tests.eidolon_cli.test_organization_goal_allowances import configure,limits


def setup(tmp_path,settings=None):
    store=OrganizationStore(tmp_path/'state.db',settings)
    seed=store.create_objective('Seed',idempotency_key='seed',delivery_mode='managed_artifact')['id']
    configure(store,[seed],limits())
    return store,seed


def template(**changes):
    return {'id':'one','title':'Follow up','description':'Use the approved scope','priority':'normal',
            'acceptanceCriteria':[{'id':'human','text':'Owner must approve','kind':'human_judgment'}],
            'deliveryMode':'managed_artifact','requiredChecks':[],'projectIds':[],
            'executiveId':'executive','managerId':'manager','dependencyIds':[],'templateDependencyIds':[],**changes}


def approve(store,templates=None):
    templates=templates or [template()]
    deadline=int(time.time())+1800
    preview=store.preview_goal_continuation('goal',templates=templates,deadline_timestamp=deadline)
    args=dict(templates=templates,deadline_timestamp=deadline,preview_sha256=preview['sha256'],idempotency_key='approve',confirmed=True)
    result=store.approve_goal_continuation('goal',**args)
    return result,args


def enable(store,enabled=True,rev=1,key='enable'):
    return store.set_goal_continuation_enabled('goal',enabled=enabled,expected_revision=rev,idempotency_key=key,confirmed=True)


def test_approval_does_not_launch_and_restart_retains_exact_typed_scope(tmp_path):
    store,seed=setup(tmp_path)
    receipt,args=approve(store)
    assert receipt['enabled'] is False
    assert store.approve_goal_continuation('goal',**args)==receipt
    assert store.advance_goal_continuation()['launched'] is None
    enable(store)
    reopened=OrganizationStore(store.path,store.settings)
    launched=reopened.advance_goal_continuation()['launched']
    assert launched is not None
    with reopened._connect() as conn:
        assert conn.execute('SELECT count(*) FROM objectives').fetchone()[0]==2
        contract=json.loads(conn.execute('SELECT record FROM objective_contract_revisions WHERE objective_id=?',(launched['objectiveId'],)).fetchone()[0])
        assert contract['criteria'][0]['kind']=='human_judgment'
        assert conn.execute('SELECT goal_id FROM organization_goal_members WHERE objective_id=?',(launched['objectiveId'],)).fetchone()[0]=='goal'
    assert reopened.advance_goal_continuation()['launched'] is None


def test_concurrent_and_lost_acknowledgment_never_duplicate_launch(tmp_path):
    store,_=setup(tmp_path);approve(store);enable(store)
    peers=[OrganizationStore(store.path,store.settings) for _ in range(4)]
    with ThreadPoolExecutor(4) as pool:
        results=list(pool.map(lambda p:p.advance_goal_continuation(),peers))
    assert sum(r['launched'] is not None for r in results)==1
    assert OrganizationStore(store.path,store.settings).advance_goal_continuation()['launched'] is None
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM organization_continuation_launches').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM objectives').fetchone()[0]==2


def test_launch_receipt_failure_rolls_back_objective_and_membership(tmp_path):
    store,_=setup(tmp_path);approve(store);enable(store)
    with store._connect() as conn:
        conn.execute("CREATE TRIGGER block_launch BEFORE INSERT ON organization_continuation_launches BEGIN SELECT RAISE(ABORT,'Injected'); END")
    with pytest.raises(sqlite3.IntegrityError): store.advance_goal_continuation()
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM objectives').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM organization_goal_members').fetchone()[0]==1
        conn.execute('DROP TRIGGER block_launch')
    assert store.advance_goal_continuation()['launched']


@pytest.mark.parametrize('change',['cancel','goal_revision','policy','deadline','stage_cap'])
def test_changed_or_exhausted_authority_stops_future_launch(tmp_path,change,monkeypatch):
    store,seed=setup(tmp_path);approve(store);enable(store)
    if change=='cancel': store.cancel(seed)
    elif change=='goal_revision': configure(store,[seed],limits(),rev=1)
    elif change=='policy':
        with store._write() as conn: conn.execute('UPDATE organization_policy SET generation=generation+1')
    elif change=='deadline': monkeypatch.setattr('eidolon_cli.organization_goal_continuation.time.time',lambda:4102444800)
    else:
        with store._write() as conn:
            request=conn.execute('SELECT id FROM requests WHERE objective_id=?',(seed,)).fetchone()[0]
            for n in range(100): conn.execute('INSERT INTO objective_usage(request_id,token,objective_id,stage) VALUES (?,?,?,?)',(request,'token'+str(n),seed,'request.plan'))
    result=store.advance_goal_continuation()
    assert result['launched'] is None and result['blocked']
    with store._connect() as conn: assert conn.execute('SELECT count(*) FROM objectives').fetchone()[0]==1


def test_disable_reenable_never_resets_finite_templates(tmp_path):
    store,_=setup(tmp_path);approve(store,[template(),template(id='two')]);enable(store)
    assert store.advance_goal_continuation()['launched']['templateId']=='one'
    enable(store,False,2,'disable')
    assert store.advance_goal_continuation()['launched'] is None
    enable(store,True,3,'enable-again')
    assert store.advance_goal_continuation()['launched']['templateId']=='two'
    assert store.advance_goal_continuation()['launched'] is None


def test_stale_preview_and_same_key_different_input_reject(tmp_path):
    store,seed=setup(tmp_path)
    preview=store.preview_goal_continuation('goal',templates=[template()],deadline_timestamp=int(time.time())+1000)
    configure(store,[seed],limits(),rev=1)
    with pytest.raises(ValueError,match='stale'):
        store.approve_goal_continuation('goal',templates=[template()],deadline_timestamp=preview['plan']['deadlineTimestamp'],preview_sha256=preview['sha256'],idempotency_key='stale',confirmed=True)
    _,args=approve(store)
    with pytest.raises(ValueError,match='different input'):
        store.approve_goal_continuation('goal',**{**args,'templates':[template(title='Changed')]})


def test_passive_legacy_and_partial_schema(tmp_path):
    store,_=setup(tmp_path)
    with store._connect() as conn:
        for table in ('organization_continuation_receipts','organization_continuation_launches','organization_continuation_controls','organization_continuation_plans'):
            conn.execute('DROP TABLE '+table)
        before='\n'.join(conn.iterdump())
        assert _records(conn)==[]
        assert '\n'.join(conn.iterdump())==before
        conn.execute('CREATE TABLE organization_continuation_plans(x TEXT)')
        with pytest.raises(ValueError,match='incomplete'): _records(conn)


def test_dependency_requires_exact_complete_milestone_and_workspace(tmp_path):
    from tests.eidolon_cli.test_organization_projects import configured
    from tests.eidolon_cli.test_organization_milestones import save,accept
    store,seed=setup(tmp_path,configured(tmp_path))
    predecessor=store.create_objective('First',idempotency_key='first',project_ids=['alpha'])['id']
    save(store,'first',[predecessor])
    approve(store,[template(projectIds=['alpha'],deliveryMode='source_project',dependencyIds=['first'])]);enable(store)
    assert store.advance_goal_continuation()['launched'] is None
    accept(store,predecessor)
    result=store.advance_goal_continuation()['launched']
    assert result
    with store._connect() as conn:
        project=json.loads(conn.execute('SELECT binding FROM objective_projects WHERE objective_id=?',(result['objectiveId'],)).fetchone()[0])
        assert project['id']=='alpha'


def test_changed_dependency_revision_never_launches(tmp_path):
    from tests.eidolon_cli.test_organization_projects import configured
    from tests.eidolon_cli.test_organization_milestones import save,accept
    store,_=setup(tmp_path,configured(tmp_path))
    predecessor=store.create_objective('First',idempotency_key='first',project_ids=['alpha'])['id']
    save(store,'first',[predecessor]);accept(store,predecessor)
    approve(store,[template(projectIds=['alpha'],dependencyIds=['first'])]);enable(store)
    save(store,'first',[predecessor],rev=1)
    assert store.advance_goal_continuation()['launched'] is None


def test_directory_replacement_invalidates_launch(tmp_path):
    from pathlib import Path
    from tests.eidolon_cli.test_organization_projects import configured
    store,_=setup(tmp_path,configured(tmp_path))
    approve(store,[template(projectIds=['alpha'])]);enable(store)
    root=Path(store.settings.read_roots[0]);root.rename(root.with_name(root.name+'-old'));root.mkdir()
    result=store.advance_goal_continuation()
    assert result['launched'] is None and 'identity changed' in result['blocked'][0]['reason']


def test_creation_failure_cannot_leave_orphan_objective(tmp_path,monkeypatch):
    store,_=setup(tmp_path)
    approve(store);enable(store)
    original=store._create_objective
    def interrupted(*args,**kwargs):
        original(*args,**kwargs)
        raise ValueError('Injected after objective creation')
    monkeypatch.setattr(store,'_create_objective',interrupted)
    result=store.advance_goal_continuation()
    assert result['launched'] is None and result['blocked']
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM objectives').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM organization_goal_members').fetchone()[0]==1


def test_preview_rejects_invalid_leader_before_owner_approval(tmp_path):
    store,_=setup(tmp_path)
    with pytest.raises(ValueError,match='leadership'):
        approve(store,[template(managerId='executive')])


def test_scheduler_continuation_obeys_existing_dispatch_and_capacity_gate(tmp_path,monkeypatch):
    from eidolon_cli.organization_service import OrganizationService
    store,_=setup(tmp_path);approve(store);enable(store)
    service=OrganizationService(store,home=tmp_path,executor=lambda *args: {'intervention':'Offline fixture'},can_dispatch=lambda:False)
    service._fill_slots()
    with store._connect() as conn: assert conn.execute('SELECT count(*) FROM objectives').fetchone()[0]==1
    service.can_dispatch=lambda:True
    monkeypatch.setattr(service,'_execution_slot',lambda:None)
    service._fill_slots()
    with store._connect() as conn: assert conn.execute('SELECT count(*) FROM objectives').fetchone()[0]==1


def test_two_validated_templates_preserve_independent_typed_criteria(tmp_path):
    store,_=setup(tmp_path)
    approve(store,[template(),template(id='two',acceptanceCriteria=[{'id':'source','text':'Run managed proof','kind':'authorized_check','checkKind':'managed_validation'}])]);enable(store)
    first=store.advance_goal_continuation()['launched'];second=store.advance_goal_continuation()['launched']
    assert first['objectiveId']!=second['objectiveId']
    with store._connect() as conn:
        control=conn.execute('SELECT required_checks FROM objective_control WHERE objective_id=?',(second['objectiveId'],)).fetchone()
        assert json.loads(control[0])==['managed_validation']
        assert conn.execute('SELECT count(*) FROM organization_continuation_launches').fetchone()[0]==2


def test_finite_template_dependencies_wait_for_exact_accepted_scope(tmp_path):
    from tests.eidolon_cli.test_organization_milestones import accept
    store,_=setup(tmp_path)
    approve(store,[template(),template(id='two',templateDependencyIds=['one'])]);enable(store)
    first=store.advance_goal_continuation()['launched']
    assert store.advance_goal_continuation()['launched'] is None
    accept(store,first['objectiveId'])
    assert store.advance_goal_continuation()['launched']['templateId']=='two'
    assert store.advance_goal_continuation()['launched'] is None


def test_accepted_predecessor_with_changed_scope_cannot_unlock_next_template(tmp_path):
    from tests.eidolon_cli.test_organization_milestones import accept
    store,_=setup(tmp_path)
    approve(store,[template(),template(id='two',templateDependencyIds=['one'])]);enable(store)
    first=store.advance_goal_continuation()['launched']
    accept(store,first['objectiveId'])
    with store._write() as conn:
        conn.execute('UPDATE objective_control SET amended_scope=? WHERE objective_id=?',('Changed',first['objectiveId']))
    assert store.advance_goal_continuation()['launched'] is None


def test_future_or_cyclic_template_dependency_rejected(tmp_path):
    store,_=setup(tmp_path)
    with pytest.raises(ValueError,match='earlier'):
        approve(store,[template(templateDependencyIds=['two']),template(id='two')])

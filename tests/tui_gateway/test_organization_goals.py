from unittest.mock import Mock
import time

import pytest

from eidolon_cli import organization_service as services
from tests.tui_gateway.test_organization_milestones import setup  # noqa: F401
from tests.tui_gateway.test_organization_owner_policy import call
from tests.eidolon_cli.test_organization_goal_allowances import limits
from tests.eidolon_cli.test_organization_goal_continuation import template


def allowance(first,params):
    return dict(params,goalId='goal',objectiveIds=[first],limits=limits(),paused=False,expectedRevision=0,idempotencyKey='goal',confirmed=True)


def test_goal_controls_preview_approve_enable_and_receipt_without_start(setup,monkeypatch):
    service,first,params=setup
    monkeypatch.setattr('eidolon_cli.organization_config.load_profile_configuration',lambda *args,**kwargs:(service.store.settings,{}))
    saved=call('organization.configureGoal',allowance(first,params))
    assert 'error' not in saved,saved
    templates=[template(projectIds=['alpha'])]
    draft=dict(params,goalId='goal',templates=templates,deadlineTimestamp=int(time.time())+1800)
    monkeypatch.setattr(services,'get_service',Mock(side_effect=AssertionError('Read cannot start service')))
    with service.store._connect() as conn: before='\n'.join(conn.iterdump())
    preview=call('organization.previewGoalContinuation',draft)
    assert 'error' not in preview,preview
    assert call('organization.goalReceipt',dict(params,receiptKind='allowance',idempotencyKey='goal'))['result']['receipt']==saved['result']['receipt']
    read=call('organization.goals',params)
    assert 'error' not in read,read
    assert len(read['result']['goals'])==1
    with service.store._connect() as conn: assert '\n'.join(conn.iterdump())==before
    monkeypatch.setattr(services,'get_service',lambda:service)
    intent=dict(draft,previewSha256=preview['result']['preview']['sha256'],idempotencyKey='approve',confirmed=True)
    approval=call('organization.approveGoalContinuation',intent)
    assert 'error' not in approval,approval
    assert approval['result']['receipt']['enabled'] is False
    assert call('organization.approveGoalContinuation',intent)==approval
    enabled=call('organization.setGoalContinuationEnabled',dict(params,goalId='goal',enabled=True,expectedRevision=1,idempotencyKey='enable',confirmed=True))
    assert 'error' not in enabled,enabled
    assert service.start.call_count==0
    with service.store._connect() as conn: assert conn.execute('SELECT count(*) FROM organization_continuation_launches').fetchone()[0]==0


@pytest.mark.parametrize('change',[{'profile':'other'},{'expectedOrganizationId':'wrong'},{'expectedOwnerId':'wrong'},{'confirmed':False}])
def test_goal_wrong_scope_and_unconfirmed_writes_reject(setup,change):
    service,first,params=setup
    assert 'error' in call('organization.configureGoal',{**allowance(first,params),**change})
    with service.store._connect() as conn: assert conn.execute('SELECT count(*) FROM organization_goal_revisions').fetchone()[0]==0


def test_passive_goal_receipt_rejects_partial_storage(setup):
    service,_,params=setup
    with service.store._connect() as conn: conn.execute('DROP TABLE organization_goal_members')
    assert 'error' in call('organization.goalReceipt',dict(params,receiptKind='allowance',idempotencyKey='missing'))


def test_pending_template_prerequisite_is_visible_but_does_not_prevent_enable(setup,monkeypatch):
    service,first,params=setup
    monkeypatch.setattr('eidolon_cli.organization_config.load_profile_configuration',lambda *args,**kwargs:(service.store.settings,{}))
    assert 'error' not in call('organization.configureGoal',allowance(first,params))
    templates=[template(projectIds=['alpha']),template(id='two',projectIds=['alpha'],templateDependencyIds=['one'])]
    draft=dict(params,goalId='goal',templates=templates,deadlineTimestamp=int(time.time())+1800)
    preview=call('organization.previewGoalContinuation',draft)['result']['preview']
    approved=call('organization.approveGoalContinuation',dict(draft,previewSha256=preview['sha256'],idempotencyKey='approve',confirmed=True))
    assert 'error' not in approved,approved
    read=call('organization.goals',params)['result']['plans'][0]
    assert read['canEnable'] is True and read['enabled'] is False
    assert read['templateStates'][1]['blockers']==['template:one']
    assert read['waitingReason']
    with service.store._connect() as conn: assert conn.execute('SELECT count(*) FROM organization_continuation_launches').fetchone()[0]==0

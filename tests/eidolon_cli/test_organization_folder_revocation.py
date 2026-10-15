"""Revocation commits its deny before draining and retains immutable history."""
from dataclasses import replace
import json
import os
import sqlite3

import pytest
from tests.eidolon_cli.test_organization_root_grants import folder_profile
from eidolon_cli.organization_folder_setup import register_folder
from eidolon_cli.organization_folder_revocation import folder_access, revoke_folder
from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_owner_policy import OwnerPolicyError


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Folder grants require no-follow POSIX descriptors')

def revoke_params(p):
    view = folder_access(p.home, p.identity['organizationId'])
    grant = view['grants'][0]
    return {'expectedOrganizationId': view['organizationId'], 'expectedGeneration': view['policyGeneration'],
            'alias': grant['alias'], 'grantHash': grant['grantHash'], 'confirmed': True, 'idempotencyKey': 'revoke'}


@pytest.mark.parametrize('drift', ['none', 'missing', 'replacement', 'invalid_source', 'source_prefix'])
def test_revoke_without_live_source_and_preserve_alias(folder_profile, drift):
    p=folder_profile; register_folder(p.home, p.apply)
    params=revoke_params(p)
    if drift=='missing': p.root.rmdir()
    if drift=='replacement': p.root.rename(p.root.with_name('old'));p.root.mkdir()
    if drift=='invalid_source': (p.home/'config.yaml').write_text('organization: [')
    if drift=='source_prefix': (p.home/'config.yaml').write_text('organization:\n  read_roots: [/different]\n')
    result=revoke_folder(p.home,params)
    assert result['receipt']['accessRevoked']
    assert revoke_folder(p.home,params)==result
    view=folder_access(p.home,p.identity['organizationId'])
    assert view['grants'][0]['revoked'] and view['paused']
    assert register_folder(p.home,p.apply)['receipt']['candidate']['project']['root']=='root0'
    with sqlite3.connect(p.path) as conn:
        assert conn.execute('SELECT count(*) FROM organization_root_revocations').fetchone()[0]==1
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            conn.execute('DELETE FROM organization_root_revocations')
    # Reopening retained settings does not touch the revoked directory.
    reopened=OrganizationStore(p.path)
    assert reopened.settings.read_roots==(str(p.root),)
    assert reopened.settings.revoked_root_aliases==('root0',)
    assert reopened.claim_next() is None
    with pytest.raises(ValueError,match='revoked'):
        reopened.create_objective('New',project_ids=['notes'],delivery_mode='managed_artifact',idempotency_key='new')


def test_fence_precedes_worker_drain_and_late_result(folder_profile):
    from eidolon_cli.organization_service import _ExecutionLock
    p=folder_profile;register_folder(p.home,p.apply)
    store=OrganizationStore(p.path)
    store.create_objective('Plan',project_ids=['notes'],delivery_mode='managed_artifact',idempotency_key='plan')
    claim=store.claim_next()
    with _ExecutionLock(p.home/'organization'/'capacity-locks','0') as held:
        assert held
        result=revoke_folder(p.home,revoke_params(p))
        assert result['receipt']['accessRevoked']
        assert folder_access(p.home,p.identity['organizationId'])['executionStillStopping']
        assert not store.heartbeat(claim)
        assert store.claim_next() is None
    assert not folder_access(p.home,p.identity['organizationId'])['executionStillStopping']
    # A source refresh cannot bypass the outstanding revocation review hold.
    store.reload_configuration(store.settings)
    assert store.claim_next() is None


def test_revoked_alias_never_opened_even_with_missing_path(folder_profile,monkeypatch):
    from eidolon_cli.organization_root_grants import active_root_aliases,expected_root_identities
    from tools.organization_file_read import organization_file_read_scope,OrganizationFileReadError
    p=folder_profile;register_folder(p.home,p.apply);revoke_folder(p.home,revoke_params(p));p.root.rmdir()
    store=OrganizationStore(p.path)
    def forbidden(*args): raise AssertionError('Revoked root opened')
    monkeypatch.setattr('tools.organization_file_read._open_root',forbidden)
    with organization_file_read_scope(store.settings.read_roots,1000,
        allowed_root_aliases=active_root_aliases(store.settings),expected_root_identities=expected_root_identities(store.settings)) as scope:
        assert scope.root_aliases==()
        with pytest.raises(OrganizationFileReadError):scope.read_exact_source('root0/private.txt')


def test_owner_review_resumes_unaffected_work_but_never_restores_root(folder_profile):
    from eidolon_cli.organization_folder_revocation import review_revocation
    p=folder_profile;register_folder(p.home,p.apply);revoke_folder(p.home,revoke_params(p))
    view=folder_access(p.home,p.identity['organizationId'])
    params={'expectedOrganizationId':view['organizationId'],'expectedGeneration':view['policyGeneration'],
            'alias':'root0','grantHash':view['grants'][0]['grantHash'],'confirmed':True,
            'reviewVersion':view['reviewVersion'],'acknowledgeUncertain':False}
    result=review_revocation(p.home,params)
    assert review_revocation(p.home,params)==result
    store=OrganizationStore(p.path)
    store.create_objective('Unrelated plan',delivery_mode='managed_artifact',idempotency_key='other')
    assert store.claim_next() is not None
    assert store.settings.revoked_root_aliases==('root0',)
    assert folder_access(p.home,p.identity['organizationId'])['grants'][0]['reviewed']


@pytest.mark.parametrize('field,value', [('expectedGeneration',999),('grantHash','0'*64),('alias','root7'),('confirmed',False)])
def test_revoke_rejects_stale_or_wrong_target_atomically(folder_profile,field,value):
    p=folder_profile;register_folder(p.home,p.apply);params=revoke_params(p);params[field]=value
    with sqlite3.connect(p.path) as conn: before='\n'.join(conn.iterdump())
    with pytest.raises((ValueError,OwnerPolicyError)):revoke_folder(p.home,params)
    with sqlite3.connect(p.path) as conn:assert '\n'.join(conn.iterdump())==before


def test_review_cannot_bypass_stopping_executor(folder_profile):
    from eidolon_cli.organization_folder_revocation import review_revocation
    from eidolon_cli.organization_service import _ExecutionLock
    p=folder_profile;register_folder(p.home,p.apply);revoke_folder(p.home,revoke_params(p))
    view=folder_access(p.home,p.identity['organizationId']);grant=view['grants'][0]
    params={'expectedOrganizationId':view['organizationId'],'expectedGeneration':view['policyGeneration'],
            'alias':'root0','grantHash':grant['grantHash'],'confirmed':True,'reviewVersion':view['reviewVersion'],'acknowledgeUncertain':True}
    with _ExecutionLock(p.home/'organization'/'capacity-locks','0') as held:
        assert held
        with pytest.raises(OwnerPolicyError,match='stopping'):review_revocation(p.home,params)
    assert folder_access(p.home,p.identity['organizationId'])['paused']


def test_unknown_outcomes_remain_unknown_after_explicit_acknowledgment(folder_profile):
    from eidolon_cli.organization_folder_revocation import review_revocation
    p=folder_profile;register_folder(p.home,p.apply)
    store=OrganizationStore(p.path);store.create_objective('Plan',delivery_mode='managed_artifact',idempotency_key='plan');claim=store.claim_next()
    with store._write() as conn:
        conn.execute("INSERT INTO tool_receipts(id,request_id,attempt,tool_call_id,tool_name,arguments,status,created) VALUES ('unknown',?,1,'call','read_file','{}','running',0)",(claim['id'],))
    revoke_folder(p.home,revoke_params(p));view=folder_access(p.home,p.identity['organizationId'])
    assert view['uncertainToolReceipts'][0]['status']=='unknown'
    params={'expectedOrganizationId':view['organizationId'],'expectedGeneration':view['policyGeneration'],
            'alias':'root0','grantHash':view['grants'][0]['grantHash'],'confirmed':True,'reviewVersion':view['reviewVersion'],'acknowledgeUncertain':False}
    with pytest.raises(OwnerPolicyError,match='uncertain'):review_revocation(p.home,params)
    review_revocation(p.home,{**params,'acknowledgeUncertain':True})
    assert folder_access(p.home,p.identity['organizationId'])['uncertainToolReceipts'][0]['status']=='unknown'


def test_archival_receipts_remain_readable_after_missing_folder_and_invalid_config(folder_profile):
    from eidolon_cli.organization_observation_reads import read_detail
    p=folder_profile;register_folder(p.home,p.apply)
    store=OrganizationStore(p.path);store.create_objective('Plan',delivery_mode='managed_artifact',idempotency_key='plan');claim=store.claim_next()
    revoke_folder(p.home,revoke_params(p));p.root.rmdir();(p.home/'config.yaml').write_text('organization: [')
    result=read_detail(p.home,'organization.toolReceipts',{'id':claim['id']})
    assert result['state']=='available'


def test_new_grants_append_without_reusing_revoked_identity(folder_profile):
    from eidolon_cli.organization_folder_revocation import review_revocation
    from eidolon_cli.organization_folder_setup import folder_setup,preview_folder
    p=folder_profile;register_folder(p.home,p.apply);revoke_folder(p.home,revoke_params(p))
    view=folder_access(p.home,p.identity['organizationId'])
    review_revocation(p.home,{'expectedOrganizationId':view['organizationId'],'expectedGeneration':view['policyGeneration'],
        'alias':'root0','grantHash':view['grants'][0]['grantHash'],'confirmed':True,'reviewVersion':view['reviewVersion'],'acknowledgeUncertain':False})
    # Allocate the distinct inode before removing the revoked directory: an
    # immediate mkdir after rmdir may legitimately reuse its retained inode.
    other=p.root.with_name('second');other.mkdir();p.root.rmdir()
    view=folder_setup(p.home,p.identity['organizationId']);params={'expectedOrganizationId':view['organizationId'],
        'expectedGeneration':view['policyGeneration'],'sourceRevision':view['sourceRevision'],'folder':{'path':str(other),'projectId':'second','team':'general'}}
    preview=preview_folder(p.home,params)
    result=register_folder(p.home,{**{k:v for k,v in params.items() if k!='folder'},'candidate':preview['candidate'],
        'previewVersion':preview['previewVersion'],'confirmed':True,'idempotencyKey':'second'})
    assert result['receipt']['candidate']['project']['root']=='root1'
    reopened=OrganizationStore(p.path);assert reopened.settings.revoked_root_aliases==('root0',)
    assert reopened.settings.read_roots==(str(p.root),str(other))


def test_setup_never_resolves_or_offers_revoked_path(folder_profile, monkeypatch):
    from pathlib import Path
    from eidolon_cli.organization_project_setup import project_setup, project_draft, project_save
    p=folder_profile;register_folder(p.home,p.apply);revoke_folder(p.home,revoke_params(p))
    p.root.rmdir();p.root.symlink_to(p.home, target_is_directory=True)
    original=Path.resolve
    def guarded(path,*args,**kwargs):
        assert path != p.root, 'Revoked source must not be resolved'
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'resolve',guarded)
    view=project_setup()
    assert view['roots']==[] and view['projects']==[] and view['revokedRoots']==['root0']
    project={'id':'another','name':'Another','root':'root0','recipe':None,'team':'default'}
    with pytest.raises(ValueError,match='revoked'):
        project_draft(project,view['revision'])
    with pytest.raises(ValueError,match='revoked|configuration changed'):
        project_save(project,project_setup()['revision'],'another',confirm_save=True)


@pytest.mark.parametrize('record', [
    None,
    {'status':'cleanup_failed'},
    {'status':'unsupported','cleanupConfirmed':False},
    {'status':'unsupported','reason':'Execution cleanup did not close its process streams within the bounded deadline.'},
    {'status':'passed','containerName':'bounded-test','cleanupConfirmed':False},
])
def test_cleanup_uncertainty_blocks_resume(folder_profile,record):
    from eidolon_cli.organization_folder_revocation import review_revocation
    p=folder_profile;register_folder(p.home,p.apply)
    store=OrganizationStore(p.path);objective=store.create_objective('Plan',delivery_mode='managed_artifact',idempotency_key='plan');claim=store.claim_next()
    with store._write() as conn:
        conn.execute('INSERT INTO project_run_starts VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                     ('unclean',claim['id'],'token',claim['objective_id'],1,'[]','hash','{}',None,1,0))
        if record is not None:
            conn.execute('INSERT INTO project_run_results VALUES (?,?,?,?)',('unclean',json.dumps(record),'hash',0))
    revoke_folder(p.home,revoke_params(p));view=folder_access(p.home,p.identity['organizationId'])
    assert view['unsettledProjectRuns']==[{'id':'unclean','request_id':claim['id']}]
    params={'expectedOrganizationId':view['organizationId'],'expectedGeneration':view['policyGeneration'],
            'alias':'root0','grantHash':view['grants'][0]['grantHash'],'confirmed':True,
            'reviewVersion':view['reviewVersion'],'acknowledgeUncertain':True}
    with pytest.raises(OwnerPolicyError,match='reconciliation'):review_revocation(p.home,params)


def test_python_cleanup_failure_records_structured_uncertainty():
    from eidolon_cli.organization_project_runner import _apply_process_result
    receipt={'isolation':{}}
    _apply_process_result(receipt, None, {'stdout':b'', 'stderr':b'', 'status':b''},
                          {'stdout':0,'stderr':0,'status':0}, 'cleanup_failed', {})
    assert receipt['status']=='unsupported'
    assert receipt['cleanupConfirmed'] is False


def test_unknown_source_effect_can_be_acknowledged_without_success(folder_profile):
    from eidolon_cli.organization_folder_revocation import review_revocation
    p=folder_profile;register_folder(p.home,p.apply)
    store=OrganizationStore(p.path);store.create_objective('Plan',delivery_mode='managed_artifact',idempotency_key='plan');claim=store.claim_next()
    with store._write() as conn:
        conn.execute("INSERT INTO requests(id,objective_id,type,team,priority,status,created,payload) VALUES ('source-unknown',?,'request.source_integrate',?,0,'queued',0,?)",
                     (claim['objective_id'],claim['team'],json.dumps({'runId':'missing-source-receipt'})))
        conn.execute("INSERT INTO organization_execution_journal VALUES ('source-unknown','old-token','hash',1,1)")
    revoke_folder(p.home,revoke_params(p));view=folder_access(p.home,p.identity['organizationId'])
    assert view['uncertainSourceRequests']==[{'id':'source-unknown','request_id':'source-unknown'}]
    assert view['unsettledProjectRuns']==[]
    params={'expectedOrganizationId':view['organizationId'],'expectedGeneration':view['policyGeneration'],
            'alias':'root0','grantHash':view['grants'][0]['grantHash'],'confirmed':True,
            'reviewVersion':view['reviewVersion'],'acknowledgeUncertain':False}
    with pytest.raises(OwnerPolicyError,match='uncertain'):review_revocation(p.home,params)
    params['acknowledgeUncertain']=True
    assert review_revocation(p.home,params)['reviewed']
    with sqlite3.connect(p.path) as conn:
        assert conn.execute('SELECT count(*) FROM project_source_receipts').fetchone()[0]==0
        assert conn.execute("SELECT status FROM requests WHERE id='source-unknown'").fetchone()[0]=='pending_intervention'
        reviewed=json.loads(conn.execute('SELECT receipt FROM organization_root_revocation_reviews').fetchone()[0])
        assert reviewed['acknowledgeUncertain'] is True
        assert reviewed['activity']['uncertainSourceRequests']==view['uncertainSourceRequests']
    assert folder_access(p.home,p.identity['organizationId'])['uncertainSourceRequests']==view['uncertainSourceRequests']


def test_moved_revoked_inode_cannot_be_granted_under_another_alias(folder_profile):
    from eidolon_cli.organization_root_grants import check_new_root
    p=folder_profile;register_folder(p.home,p.apply);revoke_folder(p.home,revoke_params(p))
    moved=p.root.with_name('moved-revoked');p.root.rename(moved)
    store=OrganizationStore(p.path)
    with pytest.raises(ValueError,match='revoked recorded filesystem identity'):
        check_new_root(str(moved),store.settings)


def test_reused_inode_remains_conservatively_denied_without_probing_revoked_path(folder_profile,monkeypatch):
    from eidolon_cli import organization_root_grants as roots
    p=folder_profile;register_folder(p.home,p.apply);revoke_folder(p.home,revoke_params(p))
    store=OrganizationStore(p.path);retained=roots.expected_root_identities(store.settings)['root0']
    other=p.root.with_name('independent');other.mkdir();p.root.rmdir()
    def reused_identity(path):
        assert path==str(other), 'Never probe the revoked path'
        return {'device':retained[0],'inode':retained[1]}
    monkeypatch.setattr(roots,'directory_identity',reused_identity)
    with pytest.raises(ValueError,match='may be inode reuse'):
        roots.check_new_root(str(other),store.settings)

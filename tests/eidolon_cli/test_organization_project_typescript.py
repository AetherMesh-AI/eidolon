"""Typecheck claims require the actual pinned compiler and isolation runtime."""
from dataclasses import asdict, replace
import hashlib
import os
import threading
import time

import pytest

from eidolon_cli.organization_project_typescript import TypecheckGrant, normalize_typecheck_grant, run_typecheck, valid_compiler_outcome
from eidolon_cli.organization_project_runner import normalize_execution_grant


def snapshot(source='export const answer: number = 42;', config='{}', extra=None):
    files={'main.ts':source,'tsconfig.json':config,**(extra or {})}
    return [{'path':'root0/'+path,'content':content,'sha256':hashlib.sha256(content.encode()).hexdigest(),'revision':1} for path,content in files.items()]


def grant():
    return TypecheckGrant(allow_local_docker=True,compiler_directory=os.environ.get('EIDOLON_TEST_TYPESCRIPT_BUNDLE','/not-configured'))


@pytest.mark.parametrize('change',[
 {'allow_local_docker':False},{'allow_local_docker':1},{'runtime_image':'node:latest'},
 {'compiler_directory':'relative'},{'compiler_directory':'/tmp/../compiler'}, {'configuration':'../tsconfig.json'},
 {'root':'/tmp'},{'timeout_seconds':True},{'memory_mb':1},{'argv':['tsc']},
])
def test_typecheck_grant_rejects_untrusted_or_unbounded_configuration(change):
    with pytest.raises(ValueError): normalize_typecheck_grant({**asdict(grant()),**change})


def test_typecheck_production_dispatch_requires_exact_opt_in():
    assert normalize_execution_grant(asdict(grant())) == grant()
    with pytest.raises(ValueError): normalize_execution_grant({'recipe':'typescript_check'})


def test_typecheck_invalid_selection_and_cancel_fail_before_runtime():
    for files in (snapshot()[:1],snapshot(source='')[1:]):
        result=run_typecheck(files,grant())
        assert result['status']=='blocked' and 'containerName' not in result
    cancel=threading.Event();cancel.set()
    result=run_typecheck(snapshot(),grant(),cancel)
    assert result['status']=='cancelled' and 'containerName' not in result


@pytest.fixture(scope='module')
def native():
    if os.environ.get('EIDOLON_REQUIRE_NODE_SANDBOX')!='1':
        pytest.skip('Actual native compiler execution was not explicitly enabled')
    assert os.environ.get('EIDOLON_TEST_TYPESCRIPT_BUNDLE'), 'Pinned compiler prerequisite missing'
    result=run_typecheck(snapshot(),grant())
    assert result['status']=='passed',result
    assert result['cleanupConfirmed'] is True and result['isolation']['established'] is True
    return result


def test_real_compiler_pass_exact_input_and_runtime_evidence(native):
    assert native['inputCount']==1 and native['diagnosticCount']==0
    assert valid_compiler_outcome(native,snapshot(),grant())
    assert native['runtime']['compilerInventorySha256']
    assert 'testCount' not in native
    assert any(item['kind']=='compiler_library' for item in native['actualInputs'])
    assert not valid_compiler_outcome({**native,'diagnosticCount':1},snapshot(),grant())
    assert not valid_compiler_outcome(native,snapshot('export const answer: number = 41;'),grant())


@pytest.mark.parametrize('source,config,extra',[
 ('export const answer: number = "wrong";','{}',None),
 ('import {x} from "./missing"; export {x};','{}',None),
 ('import fs from "node:fs";','{}',None),
 ('/// <reference path="../../outside.ts" />\nexport const x=1;','{}',None),
 ('// @ts-nocheck\nexport const x: number="wrong";','{}',None),
 ('export const x=1;','{"compilerOptions":{"noCheck":true}}',None),
 ('export const x=1;','{"extends":"../../other.json"}',None),
 ('export const x=1;','{"compilerOptions":{"plugins":[{"name":"./evil"}]}}',None),
 ('export const x=1;','{"compilerOptions":{"libReplacement":true}}',None),
])
def test_real_compiler_rejects_errors_and_unauthorized_configuration(native,source,config,extra):
    result=run_typecheck(snapshot(source,config,extra),grant())
    assert result['status']=='failed',result
    assert result['cleanupConfirmed'] is True


def test_project_code_and_lifecycle_scripts_are_never_executed(native):
    source='declare const process: {exit(code: number): never}; process.exit(91); export const x: number=1;'
    result=run_typecheck(snapshot(source,extra={'package.json':'{"scripts":{"pretest":"exit 92"}}'}),grant())
    assert result['status']=='passed',result
    assert result['inputCount']==1 and result['cleanupConfirmed'] is True


def test_real_relative_import_with_selected_closure(native):
    files=snapshot('import {value} from "./value"; export const answer: number=value;',extra={'value.ts':'export const value=42;'})
    result=run_typecheck(files,grant())
    assert result['status']=='passed' and result['inputCount']==2,result
    assert valid_compiler_outcome(result,files,grant())


def test_real_unicode_paths_and_large_revision_identity(native):
    files=snapshot('import {value} from "./数据🧪"; export const answer: number=value;',extra={'数据🧪.ts':'export const value=42;'})
    for item in files:
        item['revision']=2**63-1
    result=run_typecheck(files,grant())
    assert result['status']=='passed',result
    assert valid_compiler_outcome(result,files,grant())
    assert all(item['revision']==str(2**63-1) for item in result['actualInputs'] if item['kind']=='source')


def test_real_large_input_manifest_is_fully_retained(native):
    extra={('路径🧪'*8+str(index)+'.ts'):'export const value=42;' for index in range(55)}
    files=snapshot(extra=extra)
    result=run_typecheck(files,grant())
    assert result['status']=='passed' and result['inputCount']==56,result
    assert valid_compiler_outcome(result,files,grant())


def test_oversized_typecheck_manifest_rejected_before_runtime():
    extra={('a'*990+str(index)+'.ts'):'export const x=1;' for index in range(60)}
    result=run_typecheck(snapshot(extra=extra),grant())
    assert result['status']=='blocked' and 'control budget' in result['reason']
    assert 'containerName' not in result


def test_selected_dependency_declarations_are_data_only(native):
    files=snapshot('import {value} from "example-types"; export const answer: number=value;',extra={
        'node_modules/example-types/package.json':'{"name":"example-types","types":"index.d.ts","scripts":{"install":"exit 93"}}',
        'node_modules/example-types/index.d.ts':'export declare const value: number;',
    })
    result=run_typecheck(files,grant())
    assert result['status']=='passed' and result['inputCount']==1,result
    assert valid_compiler_outcome(result,files,grant())


def test_typecheck_lost_create_reply_resolves_owned_container(native,monkeypatch):
    from eidolon_cli import organization_project_typescript as module
    original=module._docker
    created=[]
    def lose_reply(args,config,budget,limit=1048576):
        response=original(args,config,budget,limit)
        if args[0]=='create' and response[0]==0:
            created.append(response[1]['stdout'].decode().strip())
            return -9,{'stdout':bytearray(),'stderr':bytearray()}, {'stdout':0,'stderr':0},'timed_out'
        return response
    monkeypatch.setattr(module,'_docker',lose_reply)
    result=run_typecheck(snapshot(),grant())
    assert created and result['containerId']==created[0]
    assert result['status']=='timed_out',result
    assert result['cleanupConfirmed'] is True
    assert result['isolation']['established'] is False


def test_typecheck_cleanup_failure_cannot_remain_passed(native,monkeypatch,tmp_path):
    from eidolon_cli import organization_project_typescript as module
    original=module._docker
    def fail_cleanup(args,config,budget,limit=1048576):
        if args[0]=='rm':
            return 1,{'stdout':bytearray(),'stderr':bytearray(b'injected cleanup failure')},{'stdout':0,'stderr':24},None
        return original(args,config,budget,limit)
    monkeypatch.setattr(module,'_docker',fail_cleanup)
    result=run_typecheck(snapshot(),grant())
    monkeypatch.setattr(module,'_docker',original)
    try:
        assert result['status']=='cleanup_failed',result
        assert result['cleanupConfirmed'] is False
        assert result['isolation']['established'] is True
    finally:
        identifier=result.get('containerId')
        if identifier:
            config=tmp_path/'docker';config.mkdir();(config/'config.json').write_text('{}')
            budget=module._Budget(None,5,time.monotonic())
            record=module._json_command(['inspect',identifier],config,budget)[0]
            assert record['Config']['Labels']['ai.eidolon.execution']==result['containerName']
            code,_,_,forced=original(['rm','--force',identifier],config,budget)
            assert code==0 and forced is None


def test_typecheck_diagnostic_flood_is_bounded(native):
    source='\n'.join(f'export const value{i}: number="wrong";' for i in range(500))
    result=run_typecheck(snapshot(source),replace(grant(),output_bytes=1024))
    assert result['status']=='output_limit',result
    assert result['cleanupConfirmed'] is True and result['isolation']['established'] is True


@pytest.mark.parametrize('mode',['timeout','cancel'])
def test_typecheck_supervisor_handles_stuck_compiler(native,tmp_path,monkeypatch,mode):
    # Negative lifecycle injection only: hang the trusted compiler child after
    # its real readiness handshake. Never use this fixture for a passing claim.
    import shutil
    from eidolon_cli import organization_project_typescript as module
    assets=tmp_path/'runtime';shutil.copytree(module.ASSETS,assets)
    harness=assets/'harness.mjs'
    harness.write_text(harness.read_text().replace("lines.close();", "lines.close();while(true){}"))
    monkeypatch.setattr(module,'ASSETS',assets)
    cancel=threading.Event();timer=None
    original=module._docker
    def observe_start(args,config,budget,limit=1048576):
        nonlocal timer
        if args[0]=='start' and mode=='cancel':
            timer=threading.Timer(1,cancel.set);timer.start()
        return original(args,config,budget,limit)
    monkeypatch.setattr(module,'_docker',observe_start)
    start=time.monotonic()
    try:
        result=run_typecheck(snapshot(),replace(grant(),timeout_seconds=4),cancel)
    finally:
        if timer:timer.cancel()
    assert result['status']==('cancelled' if mode=='cancel' else 'timed_out'),result
    assert result['cleanupConfirmed'] is True and result['isolation']['established'] is True
    assert time.monotonic()-start<16


@pytest.mark.parametrize('mutation',['omit','extra','duplicate','revision','hash','library','options'])
def test_real_compiler_receipt_mutations_rejected_even_with_rehashed_fields(native,mutation):
    import copy
    from eidolon_cli.organization_project_validation import digest
    result=copy.deepcopy(native)
    inputs=result['actualInputs']
    if mutation=='omit':inputs.pop()
    elif mutation=='extra':inputs.append({**inputs[0],'path':'/source/root0/unselected.ts'})
    elif mutation=='duplicate':inputs.append(dict(inputs[0]))
    elif mutation=='revision':
        next(item for item in inputs if item['kind']=='source')['revision']='999'
    elif mutation=='hash':inputs[0]['sha256']='0'*64
    elif mutation=='library':
        next(item for item in inputs if item['kind']=='compiler_library')['bytes']+=1
    else:result['effectiveConfiguration']['strict']=False
    result['actualInputsSha256']=digest(inputs)
    result['effectiveConfigurationSha256']=digest(result['effectiveConfiguration'])
    assert not valid_compiler_outcome(result,snapshot(),grant())


@pytest.mark.parametrize('field,value',[
 ('cleanupConfirmed',False),('sourceWritesPerformed',True),('checkKind','project_tests'),
 ('exitCode',False),('testCount',1),('snapshotSha256','0'*64),('containerId','bad'),
])
def test_full_compiler_receipt_rejects_changed_runtime_or_outcome(native,field,value):
    from eidolon_cli.organization_project_typescript import valid_typecheck_receipt
    assert valid_typecheck_receipt(native,asdict(grant()),snapshot())
    assert not valid_typecheck_receipt({**native,field:value},asdict(grant()),snapshot())


def _typecheck_reviewed_store(tmp_path):
    import json
    from eidolon_cli.organization_project_config import parse_project_grants
    from eidolon_cli.organization_store import OrganizationStore
    from tests.eidolon_cli.test_organization_edits import _setup,_read,_review
    store,source,objective=_setup(tmp_path,content='export const value: number = 1;')
    moved=source.with_name('main.ts');source.rename(moved);source=moved
    (source.parent/'tsconfig.json').write_text('{}')
    original=source.read_bytes()
    grants=parse_project_grants([{'id':'typecheck','files':['root0/main.ts','root0/tsconfig.json'],'execution':asdict(grant())}],1)
    staff=tuple(replace(member,tool_grants=tuple(dict.fromkeys((*member.tool_grants,'run_tests')))) for member in store.settings.roster)
    store=OrganizationStore(store.path,replace(store.settings,roster=staff,tool_grants=(*store.settings.tool_grants,'run_tests'),project_grants=grants))
    with store._write() as conn:
        conn.execute("UPDATE objective_control SET delivery_mode='managed_artifact',required_checks=? WHERE objective_id=?",(json.dumps(['project_typecheck']),objective['id']))
    claim=store.claim_next();data,_=_read(store,claim,path='root0/main.ts')
    assert store.finish(claim,{'summary':'Reviewed TypeScript value change','edit':{'path':'root0/main.ts','baseRevision':data['workspaceRevision'],'baseSha256':data['sourceSha256'],'oldText':'= 1;','newText':'= 2;'}})
    _review(store)
    assert store.finish(store.claim_next(),{})
    assert store.finish(store.claim_next(),{})
    claim=store.claim_next();assert claim['type']=='request.project_test'
    return store,source,objective,claim,original


def test_real_store_typecheck_review_is_distinct_from_tests_and_survives_reopen(native,tmp_path):
    import json
    from eidolon_cli.organization_store import OrganizationStore
    store,source,objective,claim,original=_typecheck_reviewed_store(tmp_path)
    with pytest.raises(ValueError):store.finish(claim,{'status':'passed'})
    store.run_project_stage(claim,threading.Event())
    with store._connect() as conn:
        row=conn.execute('SELECT run_id,record FROM project_run_results').fetchone()
        result=json.loads(row['record']);assert result['status']=='passed',result
        store._verify_project_run(conn,row['run_id'],review=False)
    assert store.finish(claim,{})
    store=OrganizationStore(store.path,store.settings)
    with store._connect() as conn:
        with pytest.raises(ValueError):store.verify_project_check_runs(conn,objective['id'],'project_typecheck')
    review=store.claim_next();payload=json.loads(review['payload'])
    assert review['type']=='request.test_review'
    assert store.finish(review,{'approved':True,'summary':'Independent exact TypeScript compiler receipt review','evidenceIds':payload['evidenceIds']})
    store=OrganizationStore(store.path,store.settings)
    with store._connect() as conn:
        pairs=store.verify_project_check_runs(conn,objective['id'],'project_typecheck')
        assert pairs[0][0]['id']==payload['runId'] and pairs[0][1]['kind']=='project_execution'
        with pytest.raises(ValueError,match='Compiler evidence'):store.verify_project_tests(conn,objective['id'])
        with pytest.raises(ValueError):store.verify_project_check_runs(conn,objective['id'],'project_build')
    assert source.read_bytes()==original
    with store._write() as conn:
        conn.execute('SAVEPOINT handoff_control')
        conn.execute("UPDATE objective_control SET delivery_mode='source_project',required_checks=? WHERE objective_id=?",
                     (json.dumps(['project_tests','project_typecheck']),objective['id']))
        with pytest.raises(ValueError,match='Compiler evidence'):
            store.verify_required_project_check_runs(conn,objective['id'])
        proposal=conn.execute('SELECT * FROM edit_proposals WHERE objective_id=?',(objective['id'],)).fetchone()
        request=conn.execute("SELECT * FROM requests WHERE task_id=? AND type='request.validate'",(proposal['task_id'],)).fetchone()
        store._queue_source_handoff(conn,request,proposal)
        assert conn.execute("SELECT count(*) FROM requests WHERE objective_id=? AND type='request.merge'",(objective['id'],)).fetchone()[0]==0
        conn.execute('UPDATE objective_control SET required_checks=? WHERE objective_id=?',
                     (json.dumps(['project_typecheck']),objective['id']))
        store._queue_source_handoff(conn,request,proposal)
        assert conn.execute("SELECT count(*) FROM requests WHERE objective_id=? AND type='request.merge'",(objective['id'],)).fetchone()[0]==1
        conn.execute('ROLLBACK TO handoff_control')
        conn.execute('RELEASE handoff_control')
    settings=store.settings;current=settings.project_grants[0]
    changed=OrganizationStore(store.path,replace(settings,project_grants=(replace(current,execution={**current.execution,'timeout_seconds':31}),)))
    with changed._connect() as conn:
        with pytest.raises(ValueError):changed.verify_project_check_runs(conn,objective['id'],'project_typecheck')
    revoked=OrganizationStore(store.path,replace(settings,tool_grants=tuple(item for item in settings.tool_grants if item!='run_tests')))
    with revoked._connect() as conn:
        with pytest.raises(ValueError):revoked.verify_project_check_runs(conn,objective['id'],'project_typecheck')
    source.write_text('export const value: number = 99;')
    with store._connect() as conn:
        with pytest.raises(ValueError):store.verify_project_check_runs(conn,objective['id'],'project_typecheck')


def test_typecheck_stage_schedules_without_host_runtime_probe(tmp_path):
    store,source,objective,claim,original=_typecheck_reviewed_store(tmp_path)
    assert claim['type']=='request.project_test'
    assert source.read_bytes()==original
    with store._connect() as conn:
        with pytest.raises(ValueError):store.verify_project_check_runs(conn,objective['id'],'project_typecheck')
        assert conn.execute('SELECT count(*) FROM project_run_results').fetchone()[0]==0


@pytest.mark.parametrize('recipe,kind', [('typescript_check','project_typecheck'), ('node_test','project_tests'), ('python_unittest','project_tests')])
def test_check_kind_uses_complete_verified_row_not_latest_id_only(monkeypatch,recipe,kind):
    import json
    from eidolon_cli import organization_project_execution as module
    store=module.OrganizationProjectExecutionStore()
    monkeypatch.setattr(store,'_execution_projects',lambda *args: [])
    monkeypatch.setattr(store,'_execution_project',lambda *args: None)
    monkeypatch.setattr(module,'_latest_run',lambda *args: {'id':'run'})
    pair=({'id':'run','grant_record':json.dumps({'execution':{'recipe':recipe}})}, {'id':'evidence'})
    monkeypatch.setattr(store,'_verify_project_run',lambda *args:pair)
    assert store.verify_project_check_runs(None,'objective',kind)==[pair]
    if recipe=='typescript_check':
        with pytest.raises(ValueError,match='Compiler evidence'):
            store.verify_project_tests(None,'objective')


@pytest.mark.parametrize('section,field,value',[
 ('runtime','image','untrusted'),('runtime','imageId','bad'),('runtime','architecture','arm64'),
 ('runtime','bootstrapSha256','0'*64),('runtime','compilerInventorySha256','0'*64),
 ('runtime','seccompSha256','0'*64),('isolation','established',False),
 ('isolation','network','host'),('isolation','sourceReadOnly',False),
 ('isolation','runtimeReadOnly',False),('isolation','workloadUid',0),
 ('isolation','seccompSha256','0'*64),('grant','allow_local_docker',False),
 ('grant','timeout_seconds',31),
])
def test_full_compiler_receipt_binds_nested_security_identity(native,section,field,value):
    import copy
    from eidolon_cli.organization_project_typescript import valid_typecheck_receipt
    changed=copy.deepcopy(native);changed[section][field]=value
    assert not valid_typecheck_receipt(changed,asdict(grant()),snapshot())


def test_manual_handoff_requires_kind_verified_receipts(monkeypatch):
    from eidolon_cli.organization_project_workspace import OrganizationProjectStore
    from eidolon_cli import organization_projects
    store=OrganizationProjectStore();observed=[]
    monkeypatch.setattr(store,'_automatic_source',lambda *args:False,raising=False)
    monkeypatch.setattr(store,'_execution_required',lambda *args:True,raising=False)
    monkeypatch.setattr(organization_projects,'task_project',lambda *args:None)
    def reject(*args,**kwargs):
        observed.append('typed verification')
        raise ValueError('Compiler evidence cannot satisfy project tests')
    monkeypatch.setattr(store,'verify_required_project_check_runs',reject,raising=False)
    store._queue_source_handoff(None,{'objective_id':'objective','task_id':'task'}, {})
    assert observed==['typed verification']

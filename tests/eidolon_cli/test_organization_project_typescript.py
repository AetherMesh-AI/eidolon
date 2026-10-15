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


def test_typecheck_not_in_production_dispatch():
    with pytest.raises(ValueError): normalize_execution_grant(asdict(grant()))


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

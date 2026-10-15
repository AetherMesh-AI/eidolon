"""Fixed compiler builds require real isolated execution; no script fallback."""
from dataclasses import asdict, replace
import copy
import os
import threading
import time

import pytest

from eidolon_cli.organization_project_typescript_build import BuildGrant, run_build, valid_build_receipt
from eidolon_cli.organization_project_build_outputs import validate_build_outputs
from eidolon_cli.organization_project_runner import normalize_execution_grant
from eidolon_cli.organization_project_validation import digest
from tests.eidolon_cli.test_organization_project_typescript import snapshot


def grant():
    return BuildGrant(allow_local_docker=True,compiler_directory=os.environ.get('EIDOLON_TEST_TYPESCRIPT_BUNDLE','/not-configured'))


def test_build_is_not_enabled_in_production_dispatch():
    with pytest.raises(ValueError):normalize_execution_grant(asdict(grant()))


def test_invalid_build_selection_or_cancel_never_launches():
    for files in (snapshot()[:1],snapshot(extra={'node_modules/pkg/main.ts':'export const x=1;'})):
        result=run_build(files,grant())
        assert result['status']=='blocked' and 'containerName' not in result
    cancel=threading.Event();cancel.set()
    result=run_build(snapshot(),grant(),cancel)
    assert result['status']=='cancelled' and 'outputs' not in result


@pytest.fixture(scope='module')
def native():
    if os.environ.get('EIDOLON_REQUIRE_NODE_SANDBOX')!='1':pytest.skip('Native build boundary not explicitly enabled')
    assert os.environ.get('EIDOLON_TEST_TYPESCRIPT_BUNDLE')
    result=run_build(snapshot(),grant())
    assert result['status']=='passed',result
    assert result['cleanupConfirmed'] is True
    return result


def test_actual_compiler_emits_bounded_verified_javascript(native):
    assert valid_build_receipt(native,asdict(grant()),snapshot())
    artifacts=validate_build_outputs(native,snapshot(),'root0')
    assert artifacts[0]['path']=='main.js'
    assert artifacts[0]['content']=='export const answer = 42;\n'
    assert native['checkKind']=='project_build' and 'testCount' not in native
    assert native['sourceWritesPerformed'] is False


@pytest.mark.parametrize('source,extra',[
 ('export const x:number="wrong";',None),
 ('import {x} from "./missing.js"; export {x};',None),
 ('import {x} from "./other.ts"; export {x};',{'other.ts':'export const x=1;'}),
 ('export const huge="'+('x'*270000)+'";',None),
])
def test_actual_build_errors_and_output_flood_never_promote(native,source,extra):
    result=run_build(snapshot(source,extra=extra),grant())
    assert result['status']!='passed',result
    assert result['cleanupConfirmed'] is True and 'outputs' not in result
    if source=='export const x:number="wrong";':assert 'TS2322' in result['stderr']


def test_actual_build_unicode_relative_import_and_nonexecution(native):
    files=snapshot('import {snow} from "./雪.js"; declare const process:{exit(n:number):never}; process.exit(99); export {snow};',extra={'雪.ts':'export const snow="❄️";','package.json':'{"scripts":{"build":"exit 91"}}'})
    result=run_build(files,grant())
    assert result['status']=='passed',result
    assert valid_build_receipt(result,asdict(grant()),files)
    assert [item['path'] for item in result['outputs']]==['main.js','雪.js']


@pytest.mark.parametrize('mutation',['omit','extra','source','bytes','hash','base64','cleanup','options'])
def test_recomputed_build_output_mutations_rejected(native,mutation):
    result=copy.deepcopy(native)
    if mutation=='omit':result['outputs']=[]
    elif mutation=='extra':result['outputs'].append({**result['outputs'][0],'path':'extra.js'})
    elif mutation=='source':result['outputs'][0]['sources']=['/source/root0/other.ts']
    elif mutation=='bytes':result['outputs'][0]['bytes']+=1
    elif mutation=='hash':result['outputs'][0]['sha256']='0'*64
    elif mutation=='base64':result['outputs'][0]['base64']='AAAA'
    elif mutation=='cleanup':result['cleanupConfirmed']=False
    else:result['effectiveConfiguration']['noEmit']=True
    result['outputManifestSha256']=digest([{key:item[key] for key in ('path','sources','bytes','sha256')} for item in result['outputs']])
    result['effectiveConfigurationSha256']=digest(result['effectiveConfiguration'])
    assert not valid_build_receipt(result,asdict(grant()),snapshot())


def test_actual_build_mixed_bmp_astral_paths_have_canonical_order(native):
    files=snapshot(extra={'\ue000.ts':'export const privateUse=1;','😀.ts':'export const astral=2;'})
    result=run_build(files,grant())
    assert result['status']=='passed',result
    assert valid_build_receipt(result,asdict(grant()),files)
    assert [item['path'] for item in result['outputs']]==['main.js','\ue000.js','😀.js']


def test_build_cleanup_failure_cannot_remain_passed(native,monkeypatch,tmp_path):
    from eidolon_cli import organization_project_typescript_build as module
    original=module._docker
    def fail_cleanup(args,config,budget,limit=1048576):
        if args[0]=='rm':
            return 1,{'stdout':bytearray(),'stderr':bytearray(b'injected cleanup failure')},{'stdout':0,'stderr':24},None
        return original(args,config,budget,limit)
    monkeypatch.setattr(module,'_docker',fail_cleanup)
    result=run_build(snapshot(),grant())
    monkeypatch.setattr(module,'_docker',original)
    try:
        assert result['status']=='cleanup_failed',result
        assert result['cleanupConfirmed'] is False
        assert 'outputs' not in result
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


def test_build_diagnostic_flood_is_bounded(native):
    source='\n'.join(f'export const value{i}: number="wrong";' for i in range(500))
    result=run_build(snapshot(source),replace(grant(),output_bytes=1024))
    assert result['status']=='output_limit',result
    assert result['cleanupConfirmed'] is True and result['isolation']['established'] is True


@pytest.mark.parametrize('mode',['timeout','cancel'])
def test_build_supervisor_handles_stuck_compiler(native,tmp_path,monkeypatch,mode):
    # Negative lifecycle injection only: hang the trusted compiler child after compiling and preparing bounded output, before
    # transport. Never use this fixture for a passing claim.
    import shutil
    from eidolon_cli import organization_project_typescript_build as module
    assets=tmp_path/'runtime';shutil.copytree(module.ASSETS,assets)
    harness=assets/'harness.mjs'
    harness.write_text(harness.read_text().replace("const frame=", "fs.writeFileSync('/tmp/build-outputs-prepared','yes');process.stderr.write('OUTPUTS_PREPARED\\n');while(true){};const frame="))
    monkeypatch.setattr(module,'ASSETS',assets)
    cancel=threading.Event();observer=None;stop=threading.Event();prepared=threading.Event()
    original=module._docker
    def observe_start(args,config,budget,limit=1048576):
        nonlocal observer
        if args[0]=='start' and mode=='cancel':
            def observe_phase():
                deadline=time.monotonic()+10
                while not stop.is_set() and time.monotonic()<deadline:
                    code,_,_,forced=original(['exec',args[2],'/usr/bin/test','-f','/tmp/build-outputs-prepared'],config,module._Budget(None,2,time.monotonic()))
                    if code==0 and forced is None:
                        prepared.set();cancel.set();return
                    stop.wait(.1)
            observer=threading.Thread(target=observe_phase,daemon=True);observer.start()
        return original(args,config,budget,limit)
    monkeypatch.setattr(module,'_docker',observe_start)
    start=time.monotonic()
    try:
        result=run_build(snapshot(),replace(grant(),timeout_seconds=8),cancel)
    finally:
        stop.set()
        if observer:observer.join(timeout=3)
    assert result['status']==('cancelled' if mode=='cancel' else 'timed_out'),result
    assert result['cleanupConfirmed'] is True and result['isolation']['established'] is True
    if mode=='cancel':assert prepared.is_set(), 'Cancellation must follow observed completed output preparation'
    else:assert 'OUTPUTS_PREPARED' in result['stderr']
    assert 'outputs' not in result and 'outputManifestSha256' not in result
    assert time.monotonic()-start<20


@pytest.mark.parametrize('mode',['truncated','oversized','duplicate'])
def test_actual_supervisor_rejects_damaged_output_transport(native,tmp_path,monkeypatch,mode):
    # Negative transport injection after real compiler execution, never positive proof.
    import shutil
    from eidolon_cli import organization_project_typescript_build as module
    assets=tmp_path/'runtime';shutil.copytree(module.ASSETS,assets)
    harness=assets/'harness.mjs';text=harness.read_text()
    replacement={'truncated':"frame.slice(0,-1)", 'oversized':"'x'.repeat(1048577)", 'duplicate':"frame+'\\n'+frame"}[mode]
    text=text.replace("process.stdout.write(frame+'\\n'", "process.stdout.write(("+replacement+")+'\\n'")
    harness.write_text(text);monkeypatch.setattr(module,'ASSETS',assets)
    result=run_build(snapshot(),grant())
    assert result['status']!='passed',result
    assert result['cleanupConfirmed'] is True and 'outputs' not in result


def test_controller_death_after_output_preparation_is_watchdog_bounded(native,tmp_path):
    # Negative lifecycle fixture: compiler finishes, then fixed harness signals
    # its exact phase in bounded container scratch and hangs before transport.
    import json
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    from eidolon_cli import organization_project_typescript_build as module
    assets=tmp_path/'runtime';shutil.copytree(module.ASSETS,assets)
    harness=assets/'harness.mjs'
    harness.write_text(harness.read_text().replace('const frame=',"fs.writeFileSync('/tmp/build-outputs-prepared','yes');while(true){};const frame="))
    marker=tmp_path/'controller.json'
    program='''import json,sys
from pathlib import Path
from eidolon_cli import organization_project_typescript_build as n
n.ASSETS=Path(sys.argv[3])
original=n._docker
def capture(args,config,budget,limit=1048576):
 if args[:2]==['start','--attach']:
  record=n._json_command(['inspect',args[2]],config,budget)[0]
  marker=Path(sys.argv[1]);pending=marker.with_suffix('.pending')
  pending.write_text(json.dumps({'id':args[2],'config':str(config),'name':record['Name'],'image':record['Image'],'label':record['Config']['Labels']['ai.eidolon.execution']}))
  pending.replace(marker)
 return original(args,config,budget,limit)
n._docker=capture
n.run_build(json.loads(sys.argv[2]),json.loads(sys.argv[4]))
'''
    process=subprocess.Popen([sys.executable,'-c',program,str(marker),json.dumps(snapshot()),str(assets),json.dumps(asdict(replace(grant(),timeout_seconds=10)))],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    container=None;config=None
    def docker(*args):
        return subprocess.run(['/usr/bin/docker','--config',config,'--host','unix:///var/run/docker.sock',*args],capture_output=True,timeout=5,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'})
    try:
        deadline=time.monotonic()+15
        while not marker.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.05)
        assert marker.exists(),'Controller did not reach native start'
        identity=json.loads(marker.read_text());container=identity['id'];config=identity['config']
        phase=None
        while time.monotonic()<deadline:
            phase=docker('exec',container,'/usr/bin/test','-f','/tmp/build-outputs-prepared')
            if phase.returncode==0:break
            time.sleep(.05)
        assert phase is not None and phase.returncode==0,'Compiler did not reach post-output phase'
        process.kill();process.wait(timeout=5)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            state=json.loads(docker('inspect',container).stdout)[0]['State']
            if not state['Running']:break
            time.sleep(.1)
        assert state['Running'] is False and state['Pid']==0 and state['ExitCode']==124,state
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=5)
        if container:
            owned=json.loads(docker('inspect',container).stdout)[0]
            assert len(container)==64 and all(c in '0123456789abcdef' for c in container)
            assert owned['Id']==container and owned['Name']==identity['name'] and owned['Image']==identity['image']
            assert owned['Config']['Labels']['ai.eidolon.execution']==identity['label']
            assert identity['name']=='/'+identity['label'] and identity['label'].startswith('eidolon-test-')
            assert docker('rm','--force',container).returncode==0
            base=Path(config).parent
            assert base.name.startswith('eidolon-node-') and Path(config).name=='docker'
            # Killed controller cannot run TemporaryDirectory cleanup. Remove
            # only its recorded staging directory after confirmed container rm.
            for root,dirs,_ in os.walk(base):
                Path(root).chmod(0o700)
                for directory in dirs:(Path(root)/directory).chmod(0o700)
            shutil.rmtree(base)

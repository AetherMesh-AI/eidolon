"""Experimental adapter contracts; native successes require the actual container."""
from dataclasses import asdict, replace
import hashlib
import json
import os
import threading
import time

import pytest

from eidolon_cli.organization_project_node import NodeTestGrant, normalize_node_grant, run_node_tests
from eidolon_cli.organization_project_runner import ProjectExecutionGrant, normalize_execution_grant


def snapshot(content, name='example.test.cjs'):
    return [{'path': 'root0/' + name, 'content': content,
             'sha256': hashlib.sha256(content.encode()).hexdigest(), 'revision': 1}]


GOOD = "const {test}=require('node:test');const assert=require('node:assert/strict');test('sum',()=>assert.equal(2+2,4));"
GRANT = NodeTestGrant(allow_local_docker=True)


@pytest.mark.parametrize('change', [
    {'allow_local_docker': False}, {'allow_local_docker': 1}, {'runtime_image': 'node:latest'},
    {'recipe': 'shell'}, {'argv': ['sh']}, {'root': '/tmp'}, {'test_directory': '../outside'},
    {'pattern': '--test'}, {'memory_mb': True}, {'memory_mb': 2048}, {'scratch_mb': 0},
    {'timeout_seconds': 999}, {'output_bytes': 999999},
])
def test_node_grant_requires_explicit_trusted_bounded_configuration(change):
    with pytest.raises(ValueError):
        normalize_node_grant({**asdict(GRANT), **change})


def test_node_dispatch_requires_opt_in_and_preserves_python_grant():
    assert normalize_execution_grant(asdict(GRANT)) == GRANT
    with pytest.raises(ValueError):
        normalize_execution_grant({'recipe': 'node_test'})
    assert asdict(normalize_execution_grant({})) == asdict(ProjectExecutionGrant())


def test_no_runtime_access_without_owner_opt_in():
    result = run_node_tests(snapshot(GOOD), NodeTestGrant())
    assert result['status'] == 'blocked'
    assert result['isolation']['established'] is False
    assert 'containerName' not in result


def test_invalid_snapshot_and_empty_selection_fail_before_runtime_access():
    for files in ([{**snapshot(GOOD)[0], 'sha256': '0' * 64}], snapshot(GOOD, 'other.cjs')):
        result = run_node_tests(files, GRANT)
        assert result['status'] == 'blocked'
        assert 'containerName' not in result


@pytest.fixture(scope='module')
def native():
    if os.environ.get('EIDOLON_REQUIRE_NODE_SANDBOX') != '1':
        pytest.skip('Native Docker execution is not explicitly enabled for this test invocation')
    result = run_node_tests(snapshot(GOOD), GRANT)
    if result['status'] != 'passed':
        import json
        pytest.fail(json.dumps(result,sort_keys=True))
    assert result['cleanupConfirmed'] is True
    assert result['isolation']['established'] is True
    return result


def test_live_node_pass_and_exact_source_identity(native):
    assert native['testCount'] == 1
    assert native['runtime']['image'].startswith('node@sha256:')
    assert native['isolation']['workloadUid'] == 65534


@pytest.mark.parametrize('content', [
    "const {test}=require('node:test');test('fail',()=>{throw Error('failure')});",
    "console.log('empty file');", "const {test}=require('node:test');test.skip('skip',()=>{});",
    "const {test}=require('node:test');test.todo('later');", "process.exit(0);", 'syntax error @@@',
])
def test_live_false_successes_rejected(native, content):
    result = run_node_tests(snapshot(content), GRANT)
    assert result['status'] != 'passed', result
    assert result['cleanupConfirmed'] is True


def test_live_esm(native):
    result = run_node_tests(snapshot("import {test} from 'node:test';test('works',()=>{});", 'example.test.mjs'),
                            replace(GRANT, pattern='*.test.mjs'))
    assert result['status'] == 'passed', json.dumps(result)


def test_live_supervisor_signal_descriptor_and_source_boundaries(native):
    code = r"""
const {test}=require('node:test'); const a=require('node:assert/strict'); const fs=require('node:fs');
test('isolated workload',()=>{
 a.equal(process.getuid(),65534); a.equal(process.getgid(),65534); a.deepEqual(process.getgroups(),[65534]);
 a.equal(/^Groups:[ \t]*(.*)$/m.exec(fs.readFileSync('/proc/self/status','utf8'))[1].trim(),'');
 for(const sig of ['SIGUSR1','SIGSTOP','SIGTERM','SIGKILL']) a.throws(()=>process.kill(1,sig));
 for(const path of ['/proc/1/fd/1','/proc/1/mem','/var/run/docker.sock','/root/.ssh/id_rsa']) a.throws(()=>fs.openSync(path,'r+'));
 for(const path of ['/source/root0/example.test.cjs','/runner/supervisor.mjs','/usr/local/bin/node']) a.throws(()=>fs.writeFileSync(path,'bad'));
 a.throws(()=>process.setuid(0)); a.throws(()=>process.setgid(0));
});
"""
    result = run_node_tests(snapshot(code), GRANT)
    assert result['status'] == 'passed', json.dumps(result)


def test_live_minimum_output_budget_runs_a_real_test(native):
    result=run_node_tests(snapshot(GOOD),replace(GRANT,output_bytes=1024))
    assert result['status']=='passed',json.dumps(result)
    assert result['testCount']==1


def test_live_wall_timeout_and_output_flood(native):
    for code in ('while(true){}', "while(true)process.stdout.write('x'.repeat(8192));"):
        started = time.monotonic()
        result = run_node_tests(snapshot(code), replace(GRANT, timeout_seconds=3, output_bytes=1024))
        assert result['status'] != 'passed', result
        assert time.monotonic() - started < 15
        assert result['isolation']['established'] is True, result
        assert result['cleanupConfirmed'] is True


def test_live_cancellation(native):
    cancel=threading.Event()
    timer=threading.Timer(2,cancel.set)
    timer.start()
    try:
        result=run_node_tests(snapshot('while(true){}'),GRANT,cancel)
    finally:
        timer.cancel()
    assert result['status'] == 'cancelled', result
    assert result['cleanupConfirmed'] is True


def test_live_external_network_and_namespace_denied(native):
    import socket
    import json
    listeners=[]
    endpoints=[]
    try:
        for family,host in [(socket.AF_INET,'127.0.0.1'),(socket.AF_INET6,'::1')]:
            server=socket.socket(family,socket.SOCK_STREAM)
            server.bind((host,0));server.listen(4);listeners.append(server)
            port=server.getsockname()[1]
            with socket.create_connection((host,port),timeout=1):
                pass
            endpoints.append({'host':host,'port':port})
        code = r"""
const {test}=require('node:test');const a=require('node:assert/strict');const net=require('node:net');const cp=require('node:child_process');
test('host listeners are unreachable',async()=>{
 for(const endpoint of ENDPOINTS) await new Promise((resolve,reject)=>{
  const socket=net.createConnection(endpoint);socket.setTimeout(1000);
  socket.on('connect',()=>{socket.destroy();reject(Error('reached host listener'));});
  socket.on('error',error=>['ECONNREFUSED','ENETUNREACH'].includes(error.code)?resolve():reject(error));
  socket.on('timeout',()=>{socket.destroy();reject(Error('unproven timeout'));});
 });
 const proc=cp.spawnSync('/usr/bin/unshare',['--user','--map-root-user','/usr/bin/id'],{encoding:'utf8'});
 a.equal(proc.error,undefined);a.equal(typeof proc.status,'number');a.notEqual(proc.status,0);
 a.match(proc.stderr,/Operation not permitted/);
});
""".replace('ENDPOINTS',json.dumps(endpoints))
        result=run_node_tests(snapshot(code),GRANT)
        assert result['status']=='passed',json.dumps(result)
    finally:
        for server in listeners:
            server.close()


@pytest.mark.parametrize('content', [
    "const chunks=[];while(true)chunks.push(Buffer.alloc(16*1024*1024,1));",
    "const {Worker}=require('node:worker_threads');while(true)new Worker('setInterval(()=>{},1000)',{eval:true});",
    "const fs=require('node:fs');for(let i=0;;i++)fs.writeFileSync('/tmp/'+i,Buffer.alloc(1024*1024,1));",
    "const fs=require('node:fs');for(let i=0;;i++)fs.writeFileSync('/tmp/'+i,'');",
])
def test_live_resource_exhaustion_is_bounded(native,content):
    started=time.monotonic()
    result=run_node_tests(snapshot(content),replace(GRANT,timeout_seconds=5,memory_mb=256,scratch_mb=1))
    assert result['status']!='passed',result
    assert time.monotonic()-started<17
    assert result['isolation']['established'] is True,result
    assert result['cleanupConfirmed'] is True


def test_live_controller_death_does_not_leave_running_descendants(native,tmp_path):
    import json
    import subprocess
    import sys
    marker=tmp_path/'started.json'
    code=r"""
const {spawn}=require('node:child_process');
spawn('/usr/local/bin/node',['--disable-sigusr1','-e',"require('node:fs').writeFileSync('/tmp/descendant-started','yes');setInterval(()=>{},1000)"],{detached:true,stdio:'ignore'}).unref();
while(true){}
"""
    program='''import json,sys\nfrom pathlib import Path\nfrom dataclasses import replace\nfrom eidolon_cli import organization_project_node as n\noriginal=n._docker\ndef capture(args,config,budget,limit=1048576):\n if args[:2]==['start','--attach']: Path(sys.argv[1]).write_text(json.dumps({'id':args[2],'config':str(config)}))\n return original(args,config,budget,limit)\nn._docker=capture\nn.run_node_tests(json.loads(sys.argv[2]),n.NodeTestGrant(allow_local_docker=True,timeout_seconds=6))\n'''
    process=subprocess.Popen([sys.executable,'-c',program,str(marker),json.dumps(snapshot(code))],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    container=None
    config=None
    def docker(*args):
        return subprocess.run(['/usr/bin/docker','--config',config,'--host','unix:///var/run/docker.sock',*args],
                              capture_output=True,timeout=5,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'})
    try:
        deadline=time.monotonic()+10
        while not marker.exists() and process.poll() is None and time.monotonic()<deadline:
            time.sleep(.05)
        assert marker.exists(),'Controller did not reach actual native start'
        data=json.loads(marker.read_text());container=data['id'];config=data['config']
        running=False
        while time.monotonic()<deadline:
            record=json.loads(docker('inspect',container).stdout)[0]
            if record['State']['Running']:
                running=True;break
            time.sleep(.05)
        assert running,'Container never started'
        while time.monotonic()<deadline:
            marker_check=docker('exec',container,'/usr/bin/test','-f','/tmp/descendant-started')
            if marker_check.returncode==0:
                break
            time.sleep(.05)
        assert marker_check.returncode==0,'Detached project descendant never started'
        process.kill();process.wait(timeout=5)
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            record=json.loads(docker('inspect',container).stdout)[0]
            if not record['State']['Running']:
                break
            time.sleep(.1)
        assert record['State']['Running'] is False,record['State']
        assert record['State']['Pid']==0
        assert record['State']['ExitCode']==124,record['State']
    finally:
        if process.poll() is None:
            process.kill();process.wait(timeout=5)
        if container:
            record=json.loads(docker('inspect',container).stdout)[0]
            assert record['Config']['Labels']['ai.eidolon.execution'].startswith('eidolon-test-')
            assert docker('rm','--force',container).returncode==0


def test_live_lost_create_reply_resolves_owned_container(native,monkeypatch):
    from eidolon_cli import organization_project_node as module
    original=module._docker
    created=[]
    def lose_reply(args,config,budget,limit=1048576):
        response=original(args,config,budget,limit)
        if args[0]=='create' and response[0]==0:
            created.append(response[1]['stdout'].decode().strip())
            return -9,{'stdout':bytearray(),'stderr':bytearray()}, {'stdout':0,'stderr':0},'timed_out'
        return response
    monkeypatch.setattr(module,'_docker',lose_reply)
    result=run_node_tests(snapshot(GOOD),GRANT)
    assert created and result['containerId']==created[0]
    assert result['status']=='timed_out',result
    assert result['cleanupConfirmed'] is True
    assert result['isolation']['established'] is False


def test_live_start_failure_never_becomes_pass(native,monkeypatch):
    from eidolon_cli import organization_project_node as module
    original=module._docker
    def fail_start(args,config,budget,limit=1048576):
        if args[0]=='start':
            return 1,{'stdout':bytearray(),'stderr':bytearray(b'injected start failure')},{'stdout':0,'stderr':22},None
        return original(args,config,budget,limit)
    monkeypatch.setattr(module,'_docker',fail_start)
    result=run_node_tests(snapshot(GOOD),GRANT)
    assert result['status']=='failed',result
    assert result['cleanupConfirmed'] is True
    assert result['isolation']['established'] is False


@pytest.mark.parametrize('abi',['i386','x32','native','alg','vsock'])
def test_live_compat_socketcall_is_denied_by_profile(native,tmp_path,abi):
    """Actual rejection of the x86 compatibility ABI; not a fake adapter pass."""
    import json
    import platform
    import subprocess
    import uuid
    from eidolon_cli import organization_project_node as module
    if platform.machine()!='x86_64':
        pytest.skip('x86 socketcall compatibility ABI does not apply to this architecture')
    assembly=tmp_path/'probe.S'
    assembly.write_text('''.global _start\n.section .text\n_start:\n mov $102,%eax\n mov $1,%ebx\n mov $args,%ecx\n int $0x80\n cmp $-1,%eax\n jne failed\n xor %ebx,%ebx\n jmp done\nfailed:\n mov $1,%ebx\ndone:\n mov $1,%eax\n int $0x80\n.section .data\nargs:\n .long 2,1,0\n''')
    if abi=='x32':
        assembly.write_text('.global _start\n.section .text\n_start:\n mov $0x40000027,%eax\n syscall\n mov $1,%edi\n mov $60,%eax\n syscall\n')
    if abi=='native':
        assembly.write_text('.global _start\n.section .text\n_start:\n mov $39,%eax\n syscall\n test %rax,%rax\n jle failed\n xor %edi,%edi\n jmp done\nfailed:\n mov $1,%edi\ndone:\n mov $60,%eax\n syscall\n')
    if abi in ('alg','vsock'):
        domain=38 if abi=='alg' else 40
        assembly.write_text(f'.global _start\n.section .text\n_start:\n mov $41,%eax\n mov ${domain},%edi\n mov $1,%esi\n xor %edx,%edx\n syscall\n cmp $-1,%rax\n jne failed\n xor %edi,%edi\n jmp done\nfailed:\n mov $1,%edi\ndone:\n mov $60,%eax\n syscall\n')
    source=tmp_path/'source';source.mkdir(mode=0o755)
    (tmp_path/'runner').mkdir(mode=0o755)
    compiled=subprocess.run(['/usr/bin/gcc','-m32' if abi=='i386' else '-m64','-nostdlib','-static','-Wl,--build-id=none','-o',str(source/'probe'),str(assembly)],capture_output=True,timeout=10)
    assert compiled.returncode==0,compiled.stderr.decode()
    (source/'probe').chmod(0o555)
    (tmp_path/'seccomp.json').write_bytes((module.ASSETS/'seccomp.json').read_bytes())
    config=tmp_path/'docker';config.mkdir();(config/'config.json').write_text('{}')
    name='eidolon-test-'+uuid.uuid4().hex
    args=module._create_arguments(name,tmp_path,GRANT)[:-2]
    args=[arg.replace('--entrypoint=/usr/local/bin/node','--entrypoint=/source/probe').replace('--user=0:0','--user=65534:65534') for arg in args if not arg.startswith('--cap-add=')]
    budget=module._Budget(None,10,time.monotonic())
    identifier=None
    try:
        code,output,_,forced=module._docker(args,config,budget)
        assert code==0 and forced is None,output
        identifier=output['stdout'].decode().strip()
        code,output,_,forced=module._docker(['start','--attach',identifier],config,budget)
        expected=159 if abi in ('i386','x32') else 0
        assert code==expected and forced is None,(code,output)
        state=module._json_command(['inspect',identifier],config,budget)[0]['State']
        assert state['ExitCode']==expected and state['Running'] is False and state['OOMKilled'] is False,state
    finally:
        if identifier:
            code,_,_,forced=module._docker(['rm','--force',identifier],config,module._Budget(None,5,time.monotonic()))
            assert code==0 and forced is None


def test_live_cleanup_failure_cannot_remain_passed(native,monkeypatch,tmp_path):
    from eidolon_cli import organization_project_node as module
    original=module._docker
    def fail_cleanup(args,config,budget,limit=1048576):
        if args[0]=='rm':
            return 1,{'stdout':bytearray(),'stderr':bytearray(b'injected cleanup failure')},{'stdout':0,'stderr':24},None
        return original(args,config,budget,limit)
    monkeypatch.setattr(module,'_docker',fail_cleanup)
    result=run_node_tests(snapshot(GOOD),GRANT)
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


def test_node_owner_configuration_is_exact_and_inert():
    from eidolon_cli.organization_project_config import parse_project_grants
    from eidolon_cli.organization_project_recipes import describe_execution_recipe
    value={'id':'node','files':['root0/example.test.cjs'],'execution':asdict(GRANT)}
    parsed=parse_project_grants([value],1)[0]
    assert parsed.execution==asdict(GRANT)
    description=describe_execution_recipe(parsed.execution)
    assert description['state']=='runtime_unverified'
    assert description['backend']=='linux-docker-pinned'
    assert description['dependencyInstallation'] is False
    for execution in ({'recipe':'node_test'}, {**asdict(GRANT),'allow_local_docker':False},
                      {**asdict(GRANT),'runtime_image':'node:latest'}, {**asdict(GRANT),'command':'npm test'}):
        with pytest.raises(ValueError):
            parse_project_grants([{**value,'execution':execution}],1)


def test_live_receipt_binds_runtime_grant_source_and_cleanup(native):
    import copy
    from eidolon_cli.organization_project_node import valid_node_receipt
    assert valid_node_receipt(native,asdict(GRANT),snapshot(GOOD))
    changes=[('cleanupConfirmed',False),('status','unsupported'),('exitCode',1),('testCount',0),
             ('snapshotSha256','0'*64),('selectedTests',[]),('files',[]),('grant',{}),
             ('sourceWritesPerformed',True),('containerId','bad'),('containerConfigSha256','bad')]
    for key,value in changes:
        modified=copy.deepcopy(native);modified[key]=value
        assert not valid_node_receipt(modified,asdict(GRANT),snapshot(GOOD)),key
    for section,key,value in [('runtime','image','node:latest'),('runtime','bootstrapSha256','0'*64),
        ('runtime','seccompSha256','0'*64),('runtime','architecture','arm64'),
        ('isolation','established',False),('isolation','workloadUid',0),('isolation','network','host'),
        ('isolation','sourceReadOnly',False),('isolation','backend','host')]:
        modified=copy.deepcopy(native);modified[section][key]=value
        assert not valid_node_receipt(modified,asdict(GRANT),snapshot(GOOD)),(section,key)


def _node_reviewed_store(tmp_path):
    from dataclasses import replace
    from eidolon_cli.organization_project_config import parse_project_grants
    from eidolon_cli.organization_store import OrganizationStore
    from tests.eidolon_cli.test_organization_edits import _setup,_propose,_review,PATH
    store,source,objective=_setup(tmp_path)
    test_path=source.parent/'example.test.cjs'
    test_path.write_text("const {test}=require('node:test');const a=require('node:assert/strict');const fs=require('node:fs');test('reviewed value',()=>a.match(fs.readFileSync('example.txt','utf8'),/new value/));")
    original=source.read_bytes()
    grants=parse_project_grants([{'id':'node','files':[PATH,'root0/example.test.cjs'],'execution':asdict(GRANT)}],1)
    staff=tuple(replace(member,tool_grants=tuple(dict.fromkeys((*member.tool_grants,'run_tests')))) for member in store.settings.roster)
    store=OrganizationStore(store.path,replace(store.settings,roster=staff,tool_grants=(*store.settings.tool_grants,'run_tests'),project_grants=grants))
    with store._write() as conn:
        conn.execute("UPDATE objective_control SET delivery_mode='managed_artifact',required_checks=? WHERE objective_id=?",(json.dumps(['project_tests']),objective['id']))
    _propose(store);_review(store)
    assert store.finish(store.claim_next(),{})
    assert store.finish(store.claim_next(),{})
    claim=store.claim_next();assert claim['type']=='request.project_test'
    return store,source,objective,claim,original


def test_live_store_dispatch_and_durable_review_gate(native,tmp_path):
    from eidolon_cli.organization_store import OrganizationStore
    store,source,objective,claim,original=_node_reviewed_store(tmp_path)
    with pytest.raises(ValueError):
        store.finish(claim,{'status':'passed'})
    store.run_project_stage(claim,threading.Event())
    with store._connect() as conn:
        row=conn.execute('SELECT run_id,record FROM project_run_results').fetchone()
        outcome=json.loads(row['record'])
        assert outcome['status']=='passed',outcome
        assert outcome['cleanupConfirmed'] is True
        store._verify_project_run(conn,row['run_id'],review=False)
    assert store.finish(claim,{})
    reopened=OrganizationStore(store.path,store.settings)
    assert not reopened.finish(claim,{})
    review=reopened.claim_next();assert review['type']=='request.test_review'
    assert source.read_bytes()==original
    with reopened._connect() as conn:
        with pytest.raises(ValueError):
            reopened.verify_project_tests(conn,objective['id'])
    payload=json.loads(review['payload'])
    assert reopened.finish(review,{'approved':True,'summary':'Independent review of actual Node execution and exact selected source','evidenceIds':payload['evidenceIds']})
    reopened=OrganizationStore(store.path,store.settings)
    with reopened._connect() as conn:
        assert reopened.verify_project_tests(conn,objective['id'])[0]['id']==payload['runId']
    original_settings=reopened.settings
    current_grant=original_settings.project_grants[0]
    changed=OrganizationStore(store.path,replace(original_settings,project_grants=(replace(current_grant,
        execution={**current_grant.execution,'timeout_seconds':21}),)))
    with changed._connect() as conn:
        with pytest.raises(ValueError):
            changed.verify_project_tests(conn,objective['id'])
    revoked=OrganizationStore(store.path,replace(original_settings,
        tool_grants=tuple(value for value in original_settings.tool_grants if value!='run_tests')))
    with revoked._connect() as conn:
        with pytest.raises(ValueError):
            revoked.verify_project_tests(conn,objective['id'])
    reopened=OrganizationStore(store.path,original_settings)
    source.write_text('external source changed')
    with reopened._connect() as conn:
        with pytest.raises(ValueError):
            reopened._verify_project_run(conn,row['run_id'],review=False)


def test_live_unknown_cleanup_survives_lost_finish_and_blocks_replay(native,tmp_path,monkeypatch):
    from eidolon_cli import organization_project_node as module
    from eidolon_cli.organization_store import OrganizationStore
    store,source,objective,claim,original_source=_node_reviewed_store(tmp_path)
    original=module._docker
    def fail_cleanup(args,config,budget,limit=1048576):
        if args[0]=='rm':
            return 1,{'stdout':bytearray(),'stderr':bytearray(b'injected cleanup failure')},{'stdout':0,'stderr':24},None
        return original(args,config,budget,limit)
    monkeypatch.setattr(module,'_docker',fail_cleanup)
    store.run_project_stage(claim,threading.Event())
    monkeypatch.setattr(module,'_docker',original)
    with store._connect() as conn:
        receipt=json.loads(conn.execute('SELECT record FROM project_run_results').fetchone()[0])
    try:
        assert receipt['status']=='cleanup_failed',receipt
        assert receipt['isolation']['established'] is True
        # Crash before finish acknowledgement: reopen and recover an expired lease.
        reopened=OrganizationStore(store.path,store.settings)
        with reopened._write() as conn:
            conn.execute('UPDATE requests SET lease=? WHERE id=?',(time.time()-1,claim['id']))
        assert reopened.recover_expired()==1
        assert reopened.retry(claim['id'])
        assert reopened.claim_next() is None
        with reopened._connect() as conn:
            row=conn.execute('SELECT status,reason FROM requests WHERE id=?',(claim['id'],)).fetchone()
            assert row['status']=='pending_intervention'
            assert 'unknown cleanup outcome' in row['reason']
            assert conn.execute('SELECT count(*) FROM project_run_starts').fetchone()[0]==1
            assert conn.execute('SELECT count(*) FROM project_run_results').fetchone()[0]==1
            assert conn.execute("SELECT count(*) FROM requests WHERE type='request.source_integrate'").fetchone()[0]==0
        assert source.read_bytes()==original_source
    finally:
        config=tmp_path/'cleanup-docker';config.mkdir();(config/'config.json').write_text('{}')
        budget=module._Budget(None,5,time.monotonic())
        record=module._json_command(['inspect',receipt['containerId']],config,budget)[0]
        assert record['Config']['Labels']['ai.eidolon.execution']==receipt['containerName']
        code,_,_,forced=original(['rm','--force',receipt['containerId']],config,budget)
        assert code==0 and forced is None


def test_live_durable_node_receipt_recovers_without_second_execution(native, tmp_path, monkeypatch):
    from eidolon_cli.organization_store import OrganizationStore
    from eidolon_cli import organization_project_runner as runner
    store, source, objective, claim, original = _node_reviewed_store(tmp_path)
    with store._write() as conn:
        store._journal_claim(conn, claim['id'], claim['token'])
    assert store.begin_dispatch(claim)
    store.run_project_stage(claim, threading.Event())
    with store._write() as conn:
        conn.execute('UPDATE requests SET lease=0 WHERE id=?', (claim['id'],))
    reopened = OrganizationStore(store.path, store.settings)
    assert reopened.recover_expired() == 1
    with reopened._write() as conn:
        conn.execute('UPDATE requests SET available=0 WHERE id=?', (claim['id'],))
    retry = reopened.claim_next(recoverable=True)
    assert retry is not None and retry['id'] == claim['id']
    assert not reopened.finish(claim, {})
    monkeypatch.setattr(runner, 'run_project_tests', lambda *args, **kwargs:
                        pytest.fail('Exact retained Node receipt must not execute another container'))
    assert reopened.begin_dispatch(retry)
    assert reopened.run_project_stage(retry, threading.Event()) == {}
    assert reopened.finish(retry, {})
    review = reopened.claim_next()
    assert review['type'] == 'request.test_review'
    payload = json.loads(review['payload'])
    assert reopened.finish(review, {'approved': True, 'summary': 'Independent review of exact recovered native Node receipt',
                                    'evidenceIds': payload['evidenceIds']})
    with reopened._connect() as conn:
        assert reopened.verify_project_tests(conn, objective['id'])[0]['id'] == payload['runId']
        assert conn.execute('SELECT count(*) FROM project_run_starts').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM project_run_results').fetchone()[0] == 1
    assert source.read_bytes() == original

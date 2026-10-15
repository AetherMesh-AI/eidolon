"""Experimental adapter contracts; native successes require the actual container."""
from dataclasses import asdict, replace
import hashlib
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


def test_node_experiment_does_not_enable_production_dispatch_or_change_python_grant():
    with pytest.raises(ValueError):
        normalize_execution_grant(asdict(GRANT))
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
    assert result['status'] == 'passed', result
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
    assert result['status'] == 'passed', result


def test_live_supervisor_signal_descriptor_and_source_boundaries(native):
    code = r"""
const {test}=require('node:test'); const a=require('node:assert/strict'); const fs=require('node:fs');
test('isolated workload',()=>{
 a.equal(process.getuid(),65534); a.equal(process.getgid(),65534); a.deepEqual(process.getgroups(),[]);
 for(const sig of ['SIGUSR1','SIGSTOP','SIGTERM','SIGKILL']) a.throws(()=>process.kill(1,sig));
 for(const path of ['/proc/1/fd/1','/proc/1/mem','/var/run/docker.sock','/root/.ssh/id_rsa']) a.throws(()=>fs.openSync(path,'r+'));
 for(const path of ['/source/root0/example.test.cjs','/runner/supervisor.mjs','/usr/local/bin/node']) a.throws(()=>fs.writeFileSync(path,'bad'));
 a.throws(()=>process.setuid(0)); a.throws(()=>process.setgid(0));
});
"""
    result = run_node_tests(snapshot(code), GRANT)
    assert result['status'] == 'passed', result


def test_live_minimum_output_budget_runs_a_real_test(native):
    result=run_node_tests(snapshot(GOOD),replace(GRANT,output_bytes=1024))
    assert result['status']=='passed',result
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
        assert result['status']=='passed',result
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

"""Real local pipes and fixed protocol code, without importing project packages.

These transport regressions do not establish native sandbox or tool compatibility.
"""
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import time

import pytest


PROTOCOL = (Path(__file__).resolve().parents[2] / 'eidolon_cli'
            / 'project_workspace_runtime' / 'protocol.mjs')
IMPORTS = r"""
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { performance } from 'node:perf_hooks';
import { syncBuiltinESMExports } from 'node:module';
const protocol = await import(process.argv[1]);
const { writeControlFrameSync, CONTROL_BYTES } = protocol;
"""
LARGE_FRAME = r"""
const frame = Buffer.from(JSON.stringify({kind:'result',files:Array.from({length:3000},
  (_,index)=>'/source/root0/apps/desktop/src/long-path-'+index+'.tsx')})+'\n');
assert.ok(frame.length > 65536 && frame.length < CONTROL_BYTES);
// Supervisor preflight initializes stdout, making the pipe nonblocking in Node.
void process.stdout.isTTY;
"""


@pytest.fixture
def node():
    executable = shutil.which('node')
    if executable is None:
        pytest.skip('Node is required for fixed transport tests; no native support is inferred')
    return executable


def command(node, program):
    return [node, '--input-type=module', '-e', IMPORTS + program, PROTOCOL.as_uri()]


def checked(node, program):
    result = subprocess.run(command(node, program), capture_output=True, timeout=15, check=False)
    assert result.returncode == 0, (result.returncode, result.stderr)
    return result


def stalled_pipe(node, program):
    """Do not drain until the writer exits; every path has a finite test deadline."""
    with subprocess.Popen(command(node, program), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE) as process:
        try:
            process.wait(timeout=8)
        except BaseException:
            process.kill()
            process.wait(timeout=3)
            raise
        stdout, stderr = process.communicate(timeout=3)
        return process.returncode, stdout, stderr


@pytest.mark.linux_only
def test_one_shot_stdout_write_reproduces_truncated_success(node):
    code, output, error = stalled_pipe(node, LARGE_FRAME + r"""
const written=fs.writeSync(1,frame);
fs.writeSync(2,JSON.stringify({expected:frame.length,written})+'\n');
process.exit(0);
""")
    sizes = json.loads(error)
    assert code == 0
    assert 0 < sizes['written'] == len(output) < sizes['expected']
    assert not output.endswith(b'\n')
    with pytest.raises(json.JSONDecodeError):
        json.loads(output)


def slow_pipe(node, program):
    """Force repeated short writes by slowly draining a small, real OS pipe."""
    import fcntl

    read_fd, write_fd = os.pipe()
    process = None
    try:
        fcntl.fcntl(write_fd, fcntl.F_SETPIPE_SZ, 4096)
        process = subprocess.Popen(command(node, program), stdout=write_fd, stderr=subprocess.PIPE)
        os.close(write_fd)
        write_fd = None
        chunks = []
        size = 0
        deadline = time.monotonic() + 12
        time.sleep(0.1)
        with selectors.DefaultSelector() as selector:
            selector.register(read_fd, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                assert remaining > 0, 'Fixed transport did not complete within test deadline'
                assert selector.select(remaining), 'Fixed transport pipe stalled'
                data = os.read(read_fd, 1024)
                if not data:
                    break
                size += len(data)
                assert size <= 1048576
                chunks.append(data)
                time.sleep(0.001)
        _, error = process.communicate(timeout=3)
        assert process.returncode == 0, (process.returncode, error)
        return b''.join(chunks)
    finally:
        if write_fd is not None:
            os.close(write_fd)
        os.close(read_fd)
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            process.stderr.close()


@pytest.mark.linux_only
def test_complete_frame_survives_real_fd3_and_stdout_backpressure(node):
    setup = r"""
const config=protocol.validateConfig({root:'root0',workspace:'apps/desktop',
  recipe:'workspace_typescript',configuration:'apps/desktop/tsconfig.json',
  toolEntry:'node_modules/typescript/bin/tsc',tests:[],timeoutMs:60000,
  memoryBytes:1073741824,scratchBytes:67108864,outputBytes:65536,
  preparedToolchain:{platform:'linux',arch:'x64',libc:'glibc',nodeVersion:'22.20.0',
    npmVersion:'10.9.3',nodeAbi:'127',tools:{typescript:'6.0.3',vitest:'4.1.10'}},
  selectedSources:Array.from({length:4000},(_,index)=>'apps/desktop/src/component-'+index+'-界.tsx')});
const files=protocol.selectedFiles(config);
const expected=protocol.resultRecord(config,{status:'passed',toolExitCode:0,
  reportedFiles:files,sourceCount:files.length});
const frame=value=>Buffer.from(JSON.stringify(value)+'\n');
"""
    child_program = IMPORTS + setup + r"""
writeControlFrameSync(3,frame({kind:'ready',status:{
  Uid:'65534\t65534\t65534\t65534',Gid:'65534\t65534\t65534\t65534',
  Groups:'',NoNewPrivs:'1',Seccomp:'2',CapEff:'0000',CapPrm:'0000',CapInh:'0000',CapAmb:'0000'}}));
writeControlFrameSync(3,frame(expected));
process.exit(0);
"""
    output = slow_pipe(node, setup + r"""
const { spawn }=await import('node:child_process');
void process.stdout.isTTY;
let started=0;
const receiver=new protocol.ControlFrames(config,()=>started++);
const child=spawn(process.execPath,['--input-type=module','-e',
""" + json.dumps(child_program) + r""",process.argv[1]],{stdio:['ignore','ignore','pipe','pipe']});
child.stderr.on('data',data=>process.stderr.write(data));
child.stdio[3].on('data',data=>receiver.push(data));
child.stdio[3].pause();
setTimeout(()=>child.stdio[3].resume(),100);
child.on('close',(code,signal)=>{
  const result=receiver.finish(code,signal);
  assert.equal(started,1);
  assert.deepEqual(result,expected);
  assert.ok(frame(result).length > 65536);
  writeControlFrameSync(1,frame(result),performance.now()+3000);
  process.exit(0);
});
""")
    assert output.endswith(b'\n') and output.count(b'\n') == 1
    record = json.loads(output)
    assert record['status'] == 'passed'
    assert record['sourceCount'] == len(record['reportedFiles']) == len(record['selectedFiles'])
    assert set(record['reportedFiles']) == set(record['selectedFiles'])
    assert record['evidenceOrigin'] == 'workload-claim'


@pytest.mark.linux_only
def test_stalled_reader_fails_at_existing_deadline_without_success(node):
    code, output, error = stalled_pipe(node, LARGE_FRAME + r"""
const began=performance.now();
try {
  writeControlFrameSync(1,frame,began+75);
  process.exit(0);
} catch(error) {
  fs.writeSync(2,JSON.stringify({reason:error.message,elapsed:performance.now()-began})+'\n');
  process.exit(79);
}
""")
    details = json.loads(error)
    assert code == 79
    assert details['reason'] == 'Control frame write deadline exceeded'
    assert 50 <= details['elapsed'] < 2000
    assert 0 < len(output) < 65536 * 3
    assert not output.endswith(b'\n')


@pytest.mark.linux_only
def test_closed_reader_fails_without_retrying_success(node):
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        result = subprocess.run(command(node, r"""
void process.stdout.isTTY;
assert.throws(()=>writeControlFrameSync(1,Buffer.from('{}\n')),{code:'EPIPE'});
"""), stdout=write_fd, stderr=subprocess.PIPE, timeout=5, check=False)
        assert result.returncode == 0, result.stderr
    finally:
        os.close(write_fd)


@pytest.mark.parametrize('invalid_progress', ['0', '-1', '1000', '0.5', 'NaN'])
def test_invalid_write_progress_is_rejected(node, invalid_progress):
    checked(node, r"""
let calls=0;
fs.writeSync=()=>{calls++;return """ + invalid_progress + r""";};
syncBuiltinESMExports();
assert.throws(()=>writeControlFrameSync(1,Buffer.from('{}\n')),/invalid progress/);
assert.equal(calls,1);
""")


def test_partial_byte_offsets_retry_only_transient_errors(node):
    checked(node, r"""
const data=Buffer.from('{"value":"界"}\n'), accepted=[];
let calls=0, offset=0;
fs.writeSync=(descriptor,buffer,position,length)=>{
  assert.equal(descriptor,1);
  assert.equal(buffer,data);
  assert.equal(position,offset);
  assert.equal(length,data.length-offset);
  calls++;
  if(calls===2 || calls===3 || calls===4)
    throw Object.assign(new Error('transient'),{code:['EAGAIN','EWOULDBLOCK','EINTR'][calls-2]});
  const count=Math.min(2,length);
  accepted.push(buffer.subarray(position,position+count));offset+=count;
  return count;
};
syncBuiltinESMExports();
writeControlFrameSync(1,data,performance.now()+1000);
assert.deepEqual(Buffer.concat(accepted),data);
assert.ok(calls>data.length/2);
let failed=0;
fs.writeSync=()=>{failed++;throw Object.assign(new Error('closed'),{code:'EPIPE'});};
syncBuiltinESMExports();
assert.throws(()=>writeControlFrameSync(1,data),{code:'EPIPE'});
assert.equal(failed,1);
""")


def test_invalid_or_oversized_frame_is_rejected_before_any_write(node):
    checked(node, r"""
let writes=0;
fs.writeSync=()=>{writes++;return 1;};syncBuiltinESMExports();
const oversized=Buffer.alloc(CONTROL_BYTES+1,10);
for(const [descriptor,data,deadline] of [[1,oversized,Infinity],[1,Buffer.alloc(0),Infinity],
  [1,Buffer.from('{}'),Infinity],[2,Buffer.from('{}\n'),Infinity],
  [1,Buffer.from('{}\n'),NaN],[1,'{}\n',Infinity]])
  assert.throws(()=>writeControlFrameSync(descriptor,data,deadline),/Invalid control frame write/);
assert.equal(writes,0);
""")


def test_backpressure_wait_is_bounded_by_both_deadlines(node):
    checked(node, r"""
// Isolated module instance with deterministic clock/syscall faults. The real
// pipes above establish delivery; this checks ceilings without a five-second sleep.
let now=100, waits=0, calls=0;
Object.defineProperty(performance,'now',{value:()=>now});
Atomics.wait=(_array,_index,_value,delay)=>{
  assert.ok(delay>0 && delay<=1);waits++;now+=delay;return 'timed-out';
};
fs.writeSync=()=>{calls++;throw Object.assign(new Error('busy'),{code:'EAGAIN'});};
syncBuiltinESMExports();
const bounded=await import(process.argv[1]+'?deterministic-write-deadline');
for(const [limit,expectedWait] of [[Infinity,5000],[75,75],[-1,0]]) {
  const began=now;waits=0;calls=0;
  const absolute=limit===Infinity ? Infinity : began+limit;
  assert.throws(()=>bounded.writeControlFrameSync(1,Buffer.from('{}\n'),absolute),/deadline exceeded/);
  assert.equal(now-began,expectedWait);
  assert.equal(waits,expectedWait);
  assert.equal(calls,waits);
}
calls=0;
fs.writeSync=(_fd,_buffer,_offset,length)=>{calls++;now++;return length;};
syncBuiltinESMExports();
// Neither an already-expired deadline nor a complete write crossing it may pass.
assert.throws(()=>bounded.writeControlFrameSync(1,Buffer.from('{}\n'),now-1),/deadline exceeded/);
assert.equal(calls,0);
assert.throws(()=>bounded.writeControlFrameSync(1,Buffer.from('{}\n'),now+1),/deadline exceeded/);
assert.equal(calls,1);
""")


@pytest.mark.linux_only
def test_inherited_fd3_is_nonblocking_and_honors_stalled_reader_deadline(node):
    child_program = IMPORTS + r"""
const frame=Buffer.alloc(CONTROL_BYTES-1,97);frame[frame.length-1]=10;
const began=performance.now();
try {
  writeControlFrameSync(3,frame,began+75);
  process.exit(0);
} catch(error) {
  fs.writeSync(2,JSON.stringify({reason:error.message,elapsed:performance.now()-began})+'\n');
  process.exit(79);
}
"""
    result = checked(node, r"""
const {spawn}=await import('node:child_process');
const child=spawn(process.execPath,['--input-type=module','-e',
""" + json.dumps(child_program) + r""",process.argv[1]],{stdio:['ignore','ignore','pipe','pipe']});
let bytes=0, error='', forced=false;
child.stdio[3].on('data',data=>{bytes+=data.length;assert.ok(bytes<CONTROL_BYTES);});
child.stdio[3].pause();
child.stderr.on('data',data=>{error+=data;assert.ok(error.length<4096);});
const guard=setTimeout(()=>{forced=true;child.kill('SIGKILL');},3000);
child.once('exit',()=>child.stdio[3].resume());
child.once('close',(code,signal)=>{
  clearTimeout(guard);
  assert.equal(forced,false,'fd3 blocked inside writeSync despite its deadline');
  assert.equal(signal,null);assert.equal(code,79);
  const details=JSON.parse(error);
  assert.equal(details.reason,'Control frame write deadline exceeded');
  assert.ok(details.elapsed>=50 && details.elapsed<2000);
  assert.ok(bytes>0 && bytes<CONTROL_BYTES-1);
  process.stdout.write(JSON.stringify(details)+'\n');
});
""")
    assert json.loads(result.stdout)['reason'] == 'Control frame write deadline exceeded'

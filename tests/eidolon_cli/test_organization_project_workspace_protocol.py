"""Fixed transport behavior only; these tests do not execute project code.

Native sandbox and real prepared tool compatibility require a separate lane.
"""
from pathlib import Path
import shutil
import subprocess

import pytest


PROTOCOL = (Path(__file__).resolve().parents[2] / 'eidolon_cli'
            / 'project_workspace_runtime' / 'protocol.mjs')
COMMON = r"""
import assert from 'node:assert/strict';
const protocol = await import(process.argv[1]);
const { validateConfig, absolutePath, resultRecord, validateResult, ControlFrames,
  typescriptArguments, summarizeVitestModules, boundUiProject, vitestOptions,
  verifyRuntimeToolchain, workloadEnvironment, CONTROL_BYTES } = protocol;
const toolchain = {platform:'linux', arch:'x64', libc:'glibc', nodeVersion:'22.20.0',
  npmVersion:'10.9.3', nodeAbi:'127', tools:{typescript:'6.0.3',vitest:'4.1.10'}};
const common = {root:'root0',workspace:'apps/desktop',timeoutMs:60000,
  memoryBytes:1073741824,scratchBytes:67108864,outputBytes:65536,preparedToolchain:toolchain};
const ts = validateConfig({...common,recipe:'workspace_typescript',configuration:'apps/desktop/tsconfig.json',
  toolEntry:'node_modules/typescript/bin/tsc',tests:[],
  selectedSources:['apps/shared/src/types.ts','apps/desktop/src/App.tsx']});
const ui = validateConfig({...common,recipe:'workspace_vitest_ui',configuration:'apps/desktop/vitest.config.ts',
  toolEntry:'node_modules/vitest/dist/node.js',selectedSources:[],tests:['apps/desktop/src/App.test.tsx']});
const uiPath = absolutePath(ui,ui.tests[0]);
const passed = resultRecord(ui,{status:'passed',toolExitCode:0,testCount:2,
  reportedFiles:[uiPath],modules:[{path:uiPath,passed:2,failed:0,skipped:0,state:'passed'}]});
const identity = {Uid:'65534\t65534\t65534\t65534',Gid:'65534\t65534\t65534\t65534',
  Groups:'',NoNewPrivs:'1',Seccomp:'2',CapEff:'0000',CapPrm:'0000',CapInh:'0000',CapAmb:'0000'};
const ready = {kind:'ready',status:identity};
const frame = value => Buffer.from(JSON.stringify(value)+'\n');
"""


def run_protocol(program):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is required for fixed protocol tests; no runtime support is inferred')
    completed = subprocess.run(
        [node, '--input-type=module', '-e', COMMON + program, PROTOCOL.as_uri()],
        text=True, capture_output=True, timeout=20, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_fixed_scope_options_and_complete_workload_results():
    run_protocol(r"""
assert.deepEqual(workloadEnvironment(),{
  PATH:'/usr/local/bin:/usr/bin:/bin',LANG:'C.UTF-8',HOME:'/nonexistent',TMPDIR:'/tmp',
  XDG_CACHE_HOME:'/tmp/cache',CI:'1',NO_COLOR:'1',RAYON_NUM_THREADS:'1',UV_THREADPOOL_SIZE:'2'});
const mutatedEnvironment=workloadEnvironment(); mutatedEnvironment.RAYON_NUM_THREADS='100';
assert.equal(workloadEnvironment().RAYON_NUM_THREADS,'1');
const args = typescriptArguments(ts);
assert.deepEqual(args,['--disable-sigusr1','/source/root0/node_modules/typescript/bin/tsc',
  '--project','/source/root0/apps/desktop/tsconfig.json','--noEmit','--noCheck','false',
  '--incremental','false','--pretty','false','--listFiles']);
const sources = ts.selectedSources.map(value=>absolutePath(ts,value));
const typed = resultRecord(ts,{status:'passed',toolExitCode:0,sourceCount:sources.length,
  reportedFiles:sources});
assert.equal(validateResult(typed,ts),typed);
for (const changed of [
  {...typed,sourceCount:0,reportedFiles:[]},
  {...typed,sourceCount:1,reportedFiles:[sources[0]]},
  {...typed,toolExitCode:1},
  {...typed,testCount:1},
  {...typed,configuration:'apps/desktop/other.json'},
  {...typed,sourceCount:3,reportedFiles:[...sources,'/source/root0/apps/desktop/src/globals.d.ts']},
]) assert.throws(()=>validateResult(changed,ts));
for (const changed of [
  {...ts,selectedSources:[]}, {...ts,selectedSources:['apps/desktop/src/global.d.ts']},
  {...ts,selectedSources:['../outside.ts']}, {...ts,configuration:'/tmp/tsconfig.json'},
  {...ts,toolEntry:'node_modules/typescript/bin/other'}, {...ts,outputBytes:true},
  {...ts,command:'npm run check'}, {...ts,tests:['apps/desktop/src/App.test.tsx']},
]) assert.throws(()=>validateConfig(changed));
assert.equal(validateResult(passed,ui),passed);
for (const changed of [
  {...passed,testCount:0}, {...passed,testCount:true}, {...passed,toolExitCode:1},
  {...passed,reportedFiles:[]}, {...passed,reportedFiles:[uiPath,uiPath]},
  {...passed,evidenceOrigin:'trusted-assertion-proof'},
  {...passed,modules:[{...passed.modules[0],passed:0,skipped:2}],testCount:0,skippedCount:2},
  {...passed,modules:[{...passed.modules[0],state:'failed'}]},
  {...passed,modules:[{...passed.modules[0],state:'queued'}]},
  {...passed,modules:[{...passed.modules[0],path:'/source/root0/apps/desktop/src/Other.test.ts'}]},
  {...passed,unexpected:'metadata'},
]) assert.throws(()=>validateResult(changed,ui));
const original = {extends:'./vite.config.ts',test:{name:'ui',environment:'jsdom',
  setupFiles:['./vitest.setup.ts'],include:['src/**/*.test.{ts,tsx}'],browser:{enabled:true},
  pool:'threads',maxWorkers:8,cache:{dir:'/source/root0/node_modules/.vite'}}};
const selected = boundUiProject([original,{test:{name:'electron'}}]);
assert.equal(selected.length,1);
assert.equal(selected[0].extends,original.extends);
assert.equal(selected[0].test.environment,original.test.environment);
assert.deepEqual(selected[0].test.setupFiles,original.test.setupFiles);
assert.deepEqual(selected[0].test.include,original.test.include);
assert.equal(selected[0].test.maxWorkers,1);
assert.equal(selected[0].test.pool,'forks');
assert.equal(selected[0].test.browser.enabled,false);
assert.equal(selected[0].test.cache.dir,'/tmp/vite-cache/ui');
assert.equal(original.test.maxWorkers,8);
assert.throws(()=>boundUiProject([original,original]));
assert.throws(()=>boundUiProject(['./alternate.config.ts']));
const options = vitestOptions(ui,{});
assert.equal(options.config,absolutePath(ui,ui.configuration));
assert.equal(options.configLoader,'runner');
assert.deepEqual(options.project,['ui']);
assert.equal(options.run,true);
for(const name of ['watch','standalone','ui','open','api','fileParallelism','passWithNoTests','allowOnly','update'])
  assert.equal(options[name],false);
assert.equal(options.coverage.enabled,false);
assert.equal(options.browser.enabled,false);
assert.equal(options.maxWorkers,1);
assert.equal(options.maxConcurrency,1);
const observed = {platform:'linux',arch:'x64',libc:'glibc',glibcVersion:'2.36',nodeVersion:'22.20.0',nodeAbi:'127'};
assert.equal(verifyRuntimeToolchain(toolchain,observed),observed);
for(const key of ['platform','arch','libc','nodeVersion','nodeAbi'])
  assert.throws(()=>verifyRuntimeToolchain({...toolchain,[key]:'different'},observed));
assert.throws(()=>verifyRuntimeToolchain(toolchain,{...observed,glibcVersion:undefined}));
assert.throws(()=>verifyRuntimeToolchain({...toolchain,libc:'musl'},{...observed,libc:'musl'}));
""")


def test_control_protocol_rejects_incomplete_extra_and_hollow_results():
    run_protocol(r"""
let started = 0;
const parser = new ControlFrames(ui,()=>started++);
const transcript = Buffer.concat([frame(ready),frame(passed)]);
for(let index=0;index<transcript.length;index+=7) parser.push(transcript.subarray(index,index+7));
assert.equal(started,1);
assert.deepEqual(parser.finish(0,null),passed);
assert.throws(()=>parser.push(frame(passed)));
for(const records of [[],[ready],[passed],[ready,ready],[ready,passed,passed],
  [{kind:'ready',status:{...identity,Uid:'0\t0\t0\t0'}}],
  [{kind:'ready',status:{...identity,Groups:'0'}}],
  [{kind:'ready',status:{...identity,CapEff:'c0'}}],
  [{kind:'ready',status:{...identity,extra:'claimed'}}],
  [ready,{...passed,status:'failed'}],
]) {
  assert.throws(()=>{
    const receiver = new ControlFrames(ui,()=>{});
    for(const record of records) receiver.push(frame(record));
    receiver.finish(0,null);
  });
}
for(const suffix of ['unframed','\n','{"kind":"result"}']) {
  assert.throws(()=>{
    const receiver = new ControlFrames(ui,()=>{});
    receiver.push(Buffer.concat([transcript,Buffer.from(suffix)]));
    receiver.finish(0,null);
  });
}
for(const [code,signal] of [[1,null],[0,'SIGKILL'],[null,'SIGTERM']]) {
  const receiver = new ControlFrames(ui,()=>{}); receiver.push(transcript);
  assert.throws(()=>receiver.finish(code,signal));
}
const excessive = new ControlFrames(ui,()=>{});
assert.throws(()=>excessive.push(Buffer.alloc(CONTROL_BYTES+1)));
const fakeModule = (path,states,state='passed',errors=[]) => ({moduleId:path,project:{name:'ui'},
  state:()=>state,ok:()=>state==='passed',errors:()=>errors,
  children:{*allTests(){for(const value of states) yield {result:()=>({state:value})};}}});
const summaries = summarizeVitestModules([fakeModule(uiPath,['passed','passed'])]);
assert.deepEqual(summaries,passed.modules);
const pending = summarizeVitestModules([fakeModule(uiPath,['passed','pending'])]);
assert.equal(pending[0].failed,1);
const broken = summarizeVitestModules([fakeModule(uiPath,['passed'],'passed',[new Error('hook failure')])]);
assert.equal(broken[0].state,'failed');
const hollow = summarizeVitestModules([fakeModule(uiPath,[])]);
assert.throws(()=>validateResult({...passed,testCount:0,modules:hollow},ui));
assert.throws(()=>summarizeVitestModules([{...fakeModule(uiPath,['passed']),project:{name:'electron'}}]));
// An internally consistent forged reporter result is indistinguishable here:
// transport validation must retain its workload-claim label, not certify honesty.
assert.equal(validateResult({...passed,modules:summaries},ui).evidenceOrigin,'workload-claim');
""")

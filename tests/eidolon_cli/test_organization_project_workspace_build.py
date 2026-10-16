"""Renderer metadata protocol and host boundary checks, never native build proof."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from eidolon_cli import organization_project_workspace_node as runtime
from eidolon_cli.organization_project_snapshot_bundle import (
    ProjectSnapshotBundle, ProjectSnapshotFile, ProjectSnapshotLimits,
)
from eidolon_cli.organization_project_validation import digest

PROTOCOL = (Path(__file__).resolve().parents[2] / 'eidolon_cli'
            / 'project_workspace_runtime' / 'protocol.mjs')
COMMON = r"""
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createHash} from 'node:crypto';
const protocol = await import(process.argv[1]);
const base = process.argv[2];
const {validateConfig, resultRecord, validateResult, validateOutputManifest, manifestDigest,
  collectBuildOutput, viteBuildOptions, validateViteBuildConfig, BUILD_OUTPUT_LIMITS, CONTROL_BYTES} = protocol;
const config = validateConfig({root:'root0',workspace:'apps/desktop',recipe:'workspace_vite_build',
  configuration:'apps/desktop/vite.config.ts',toolEntry:'node_modules/vite/dist/node/index.js',
  tests:[],selectedSources:[],timeoutMs:60000,memoryBytes:1073741824,scratchBytes:268435456,outputBytes:65536,
  preparedToolchain:{platform:'linux',arch:'x64',libc:'glibc',nodeVersion:'22.20.0',npmVersion:'10.9.3',
    nodeAbi:'127',tools:{vite:'8.2.0',vitest:'4.1.10',typescript:'6.0.3'}}});
const root='/source/root0/apps/desktop';
const options=viteBuildOptions(config);
const resolved={...options,command:'build',isProduction:true,publicDir:root+'/public',
  environments:{client:{}},consumer:'client',devtools:{enabled:false},env:{BASE_URL:'/',MODE:'production',DEV:false,PROD:true}};
const html=Buffer.from('<script type="module" src="/assets/main.js"></script>');
const chunk=Buffer.from('console.log("renderer")');
const binary=Buffer.from([0,255,128,13,10,0,1]);
const result={output:[{fileName:'index.html',type:'asset'},
  {fileName:'assets/main.js',type:'chunk',isEntry:true}]};
function outputDir(name) {
  const directory=base+'/'+name;
  fs.mkdirSync(directory+'/assets',{recursive:true});
  fs.writeFileSync(directory+'/index.html',html);
  fs.writeFileSync(directory+'/assets/main.js',chunk);
  return directory;
}
const hash=data=>createHash('sha256').update(data).digest('hex');
"""


def run_protocol(program, tmp_path):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is required for pure renderer protocol tests; no native support is inferred')
    completed = subprocess.run([node, '--input-type=module', '-e', COMMON + program,
        PROTOCOL.as_uri(), str(tmp_path)], capture_output=True, text=True, timeout=30, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout


@pytest.mark.linux_only
def test_renderer_claim_collects_binary_public_assets_and_preserves_fixed_build_scope(tmp_path):
    output = run_protocol(r"""
validateViteBuildConfig(resolved,config);
validateViteBuildConfig(resolved,config,true);
options.plugins[0].configResolved(resolved);
options.plugins[0].buildStart.call({environment:{config:resolved}});
assert.equal(options.configFile,root+'/vite.config.ts');
assert.equal(options.mode,'production');assert.equal(options.envFile,false);
assert.deepEqual(options.envPrefix,[]);assert.equal(options.build.write,true);
const merged={...options,devtools:true,envPrefix:['VITE_','OTHER_'],input:['different.html',options.input],
 build:{...options.build,watch:{},rolldownOptions:{...options.build.rolldownOptions,input:['different.html']}}};
options.plugins[0].config(merged);
assert.deepEqual(merged.envPrefix,[]);assert.equal(merged.build.watch,null);
assert.equal(merged.devtools,false);
assert.equal(merged.input,root+'/index.html');
assert.equal(merged.build.rolldownOptions.input,root+'/index.html');
const badConfigs=[{...resolved,devtools:{enabled:true}},{...resolved,root:'/tmp/other'}, {...resolved,mode:'development'},
 {...resolved,envFile:true}, {...resolved,envDir:root},
 {...resolved,env:{...resolved.env,VITE_SECRET:'unexpected'}}, {...resolved,envPrefix:['VITE_']}, {...resolved,configLoader:'bundle'},
 {...resolved,configFile:root+'/other.ts'}, {...resolved,publicDir:'/etc'},
 {...resolved,publicDir:root+'/public/../../escape'}, {...resolved,input:root+'/other.html'},
 {...resolved,environments:{client:{},ssr:{}}}, {...resolved,cacheDir:root+'/node_modules/.vite'}];
for(const changed of badConfigs) assert.throws(()=>validateViteBuildConfig(changed,config));
for(const changed of [{write:false},{watch:{}},{ssr:true},{lib:{}},{outDir:'/tmp/elsewhere'},
 {emptyOutDir:true},{copyPublicDir:false},{rolldownOptions:{input:root+'/other.html'}},
 {rolldownOptions:{...resolved.build.rolldownOptions,output:{dir:'/tmp/elsewhere'}}},
 {rolldownOptions:{...resolved.build.rolldownOptions,output:[resolved.build.rolldownOptions.output]}}])
 assert.throws(()=>validateViteBuildConfig({...resolved,build:{...resolved.build,...changed}},config));
assert.throws(()=>validateViteBuildConfig({...resolved,consumer:'server'},config,true));
for(const changed of [{tests:['apps/desktop/src/example.test.ts']},
 {selectedSources:['apps/desktop/src/main.ts']},{toolEntry:'node_modules/vite/bin/vite.js'}])
 assert.throws(()=>validateConfig({...config,...changed}));
const directory=outputDir('valid');
fs.writeFileSync(directory+'/assets/public.js',binary);
fs.writeFileSync(directory+'/icon.png',binary);
const metadata=collectBuildOutput(directory,result);
const frame=resultRecord(config,{...metadata,toolExitCode:0,status:'passed'});
assert.equal(validateResult(frame,config),frame);
assert.equal(frame.evidenceOrigin,'workload-claim');assert.equal(frame.outputBodiesRetained,false);
assert.deepEqual(frame.outputManifest.map(item=>[item.path,item.kind]),[
 ['assets/main.js','chunk'],['assets/public.js','asset'],['icon.png','asset'],['index.html','asset']]);
const icon=frame.outputManifest.find(item=>item.path==='icon.png');
assert.equal(icon.bytes,binary.length);assert.equal(icon.sha256,hash(binary));
assert.deepEqual(Object.keys(icon).sort(),['bytes','kind','path','sha256']);
assert.ok(Buffer.byteLength(JSON.stringify(frame))<CONTROL_BYTES);
assert.equal(frame.outputManifestSha256,manifestDigest(frame.outputManifest));
const failure=resultRecord(config);
assert.equal(validateResult(failure,config),failure);
assert.deepEqual(failure.outputManifest,[]);
for(const change of [{outputBodiesRetained:true},{outputManifestVersion:true},{outputManifestSha256:'0'.repeat(64)},
 {outputManifest:[]},{sourceCount:1},{selectedFiles:[root+'/src/main.ts']},{outputBodies:['bytes']},
 {toolExitCode:1},{evidenceOrigin:'trusted-collector-proof'}])
 assert.throws(()=>validateResult({...frame,...change},config));
console.log(JSON.stringify(frame));
""", tmp_path)
    frame = json.loads(output)
    assert frame['outputManifestSha256'] == digest(frame['outputManifest'])
    step, source = build_context()
    assert runtime._outcome(frame, step, source, 'apps/desktop')


@pytest.mark.linux_only
def test_renderer_collection_rejects_unsafe_partial_and_unbounded_outputs(tmp_path):
    run_protocol(r"""
for(const [name,modify,report] of [
 ['empty',directory=>fs.writeFileSync(directory+'/index.html',''),result],
 ['missing',directory=>fs.unlinkSync(directory+'/assets/main.js'),result],
 ['symlink',directory=>fs.symlinkSync(directory+'/index.html',directory+'/linked'),result],
 ['directory-link',directory=>fs.symlinkSync(directory+'/assets',directory+'/linked'),result],
 ['hardlink',directory=>fs.linkSync(directory+'/index.html',directory+'/linked'),result],
 ['case-alias',directory=>{fs.mkdirSync(directory+'/Assets');fs.writeFileSync(directory+'/Assets/other.js','x');},result],
 ['noncanonical',directory=>fs.writeFileSync(directory+'/e\u0301.txt','x'),result],
 ['large-file',directory=>fs.truncateSync(directory+'/index.html',BUILD_OUTPUT_LIMITS.fileBytes+1),result],
 ['count',directory=>{for(let i=0;i<BUILD_OUTPUT_LIMITS.files;i++) fs.writeFileSync(directory+'/extra'+i,'');},result],
 ['inode-count',directory=>{for(let i=0;i<4097;i++) fs.mkdirSync(directory+'/dir'+i);},result],
 ['depth',directory=>fs.mkdirSync(directory+'/'+Array(33).fill('x').join('/'),{recursive:true}),result],
 ['no-entry',()=>{}, {output:result.output.map(item=>({...item,isEntry:false}))}],
 ['extra-emitted',()=>{}, {output:[...result.output,{fileName:'absent.bin',type:'asset'}]}],
 ['escape',()=>{}, {output:[...result.output,{fileName:'../escaped',type:'asset'}]}],
 ['duplicate',()=>{}, {output:[...result.output,result.output[0]]}],
 ['multiple-builds',()=>{}, [result,result]],
]) {
 const directory=outputDir(name);modify(directory);
 assert.throws(()=>collectBuildOutput(directory,report),undefined,name);
}
const good=collectBuildOutput(outputDir('metadata'),result).outputManifest;
for(const bad of [ [...good].reverse(),[...good,good[0]],
 good.map((item,i)=>i?item:{...item,bytes:true}),
 good.map((item,i)=>i?item:{...item,kind:'file'}),
 good.map((item,i)=>i?item:{...item,sha256:'bad'}),
 good.map((item,i)=>i?item:{...item,path:'/absolute'}),
 good.map((item,i)=>i?item:{...item,path:'bad\\name'}),
 good.map((item,i)=>i?item:{...item,body:'leaked'}),
 Array.from({length:5},(_,index)=>({path:'large'+index,bytes:BUILD_OUTPUT_LIMITS.fileBytes,
  sha256:'a'.repeat(64),kind:'asset'})),
 Array.from({length:1024},(_,index)=>({path:String(index).padStart(4,'0')+'x'.repeat(400),
  bytes:0,sha256:'a'.repeat(64),kind:'asset'})),
 ]) assert.throws(()=>validateOutputManifest(bad,manifestDigest(bad),true));
assert.equal(BUILD_OUTPUT_LIMITS.manifestBytes,256*1024);
""", tmp_path)


@pytest.mark.linux_only
def test_renderer_collection_rejects_fifo_without_opening_it(tmp_path):
    run_protocol(r"""
const {execFileSync}=await import('node:child_process');
const directory=outputDir('fifo');
execFileSync('/usr/bin/mkfifo',[directory+'/fifo']);
assert.throws(()=>collectBuildOutput(directory,result),/special/);
""", tmp_path)


def build_context():
    step = {'id': 'renderer-build', 'recipe': 'workspace_vite_build', 'checkKind': 'project_build',
            'config': 'apps/desktop/vite.config.ts', 'testFiles': [], 'sourceDirectories': []}
    files = {'apps/desktop/index.html': b'<script type="module" src="./src/main.ts"></script>',
             'apps/desktop/src/main.ts': b'console.log("renderer")',
             'apps/desktop/vite.config.ts': b'export default {}'}
    source = ProjectSnapshotBundle('root0', (1, 2), tuple(ProjectSnapshotFile(path, data)
        for path, data in sorted(files.items())), ProjectSnapshotLimits(10, 1024, 4096))
    return step, source


def build_frame():
    step, _ = build_context()
    manifest = [{'path': path, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'kind': kind}
                for path, data, kind in [('assets/main.js', b'console.log("renderer")', 'chunk'),
                                         ('index.html', b'<html></html>', 'asset')]]
    return {'kind': 'result', 'status': 'passed', 'recipe': step['recipe'], 'configuration': step['config'],
            'evidenceOrigin': 'workload-claim', 'toolExitCode': 0, 'testCount': 0, 'skippedCount': 0,
            'failedCount': 0, 'sourceCount': 0, 'selectedFiles': [], 'reportedFiles': [], 'modules': [],
            'toolEntry': 'node_modules/vite/dist/node/index.js', 'outputManifestVersion': 1,
            'outputManifest': manifest, 'outputManifestSha256': digest(manifest), 'outputBodiesRetained': False}


def test_host_build_outcome_requires_exact_bounded_metadata_and_prepared_api():
    step, source = build_context()
    frame = build_frame()
    assert runtime._outcome(frame, step, source, 'apps/desktop')
    for changes in [{'outputManifestVersion': True}, {'outputBodiesRetained': True}, {'testCount': 1},
                    {'outputManifestSha256': '0' * 64}, {'outputManifest': []}, {'toolExitCode': True},
                    {'selectedFiles': ['apps/desktop/index.html']}, {'outputBodies': ['leaked']},
                    {'toolEntry': 'node_modules/vite/bin/vite.js'}, {'evidenceOrigin': 'trusted'}]:
        assert not runtime._outcome({**frame, **changes}, step, source, 'apps/desktop')
    for changes in [{'bytes': True}, {'bytes': runtime.BUILD_OUTPUT_LIMITS['fileBytes'] + 1},
                    {'path': '../outside'}, {'path': '/absolute'}, {'path': '\ud800'},
                    {'kind': 'file'}, {'sha256': 'bad'}, {'content': 'leaked'}]:
        changed = deepcopy(frame)
        changed['outputManifest'][0].update(changes)
        if changes.get('path') != '\ud800':
            changed['outputManifestSha256'] = digest(changed['outputManifest'])
        assert not runtime._outcome(changed, step, source, 'apps/desktop')
    checked = {'manifest': {'files': [{'path': frame['toolEntry']}], 'toolchain': {'tools': {'vite': '8.2.0'}}}}
    assert runtime._tool_entry(step, checked) == frame['toolEntry']
    for bad in [{'manifest': {'files': [], 'toolchain': {'tools': {'vite': '8.2.0'}}}},
                {'manifest': {'files': [{'path': frame['toolEntry']}], 'toolchain': {'tools': {}}}}]:
        with pytest.raises(ValueError, match='entry point'):
            runtime._tool_entry(step, bad)


def build_receipt_fixture():
    """Synthetic v2 host receipt for boundary tests, never native provenance."""
    from tests.eidolon_cli.test_organization_project_workspace_receipt import fixture

    receipt, context = fixture()
    step, build_source = build_context()
    profile = deepcopy(context['profile'])
    profile['version'] = 2
    profile['steps'].append(step)
    profile['steps'].sort(key=lambda item: item['id'])
    files = {item.path: item for item in context['source'].files}
    files.update({item.path: item for item in build_source.files})
    source = ProjectSnapshotBundle('root0', (1, 2), tuple(files[path] for path in sorted(files)),
                                   ProjectSnapshotLimits(10, 1024, 4096))
    receipt.update(recipe=step['recipe'], checkKind=step['checkKind'], stepId=step['id'],
                   stepDefinitionSha256=digest(step), profileSha256=digest(profile),
                   snapshotSha256=source.snapshot_sha256, outcome=build_frame(),
                   limitations=runtime.BUILD_LIMITATIONS)
    context.update(source=source, profile=profile, step_id=step['id'], trusted_receipt_sha256=digest(receipt))
    return receipt, context


def test_build_receipt_requires_independent_cleanup_and_preserves_v1_shape():
    from eidolon_cli import organization_project_workspace_receipt as receipts
    from tests.eidolon_cli.test_organization_project_workspace_receipt import fixture, events_for

    candidate, context = build_receipt_fixture()
    validated = receipts.validate_prepared_workspace_receipt(candidate, **context)
    assert validated['workloadEvidenceOrigin'] == 'workload-claim'
    assert validated['requiresIndependentReview'] is True
    assert validated['acceptanceAuthorized'] is False
    history = receipts.inspect_prepared_runtime_history(events_for(candidate),
        resource_identity=context['resource_identity'], receipt_context=context)
    assert history['state'] == 'finish_only'
    assert history['sourceDeliveryAuthorized'] is False
    for changes in [{'cleanupConfirmed': False}, {'stagingCleanupConfirmed': False},
                    {'terminalState': {'Running': True, 'ExitCode': 0, 'OOMKilled': False}},
                    {'limitations': runtime.LIMITATIONS}]:
        altered = {**deepcopy(candidate), **changes}
        with pytest.raises(ValueError):
            receipts.validate_prepared_workspace_receipt(altered,
                **{**context, 'trusted_receipt_sha256': digest(altered)})
    for changes in [{'outputBodiesRetained': True}, {'outputManifestSha256': '0' * 64},
                    {'outputManifest': []}, {'outputManifestVersion': 2}, {'outputBodies': ['not metadata']}]:
        altered = deepcopy(candidate)
        altered['outcome'].update(changes)
        with pytest.raises(ValueError):
            receipts.validate_prepared_workspace_receipt(altered,
                **{**context, 'trusted_receipt_sha256': digest(altered)})
    for recipe in ('workspace_typescript', 'workspace_vitest_ui'):
        legacy, legacy_context = fixture(recipe)
        assert legacy['limitations'] == runtime.LIMITATIONS
        assert not runtime.BUILD_OUTPUT_FIELDS.intersection(legacy['outcome'])
        assert receipts.validate_prepared_workspace_receipt(legacy, **legacy_context)['hostReceiptConsistent']

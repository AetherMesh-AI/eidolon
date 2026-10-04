// Import complete production modules. Only Git transport and installer payload
// are local fixtures; no sliced functions, Electron startup or live installer.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const {execFileSync, spawnSync} = require('node:child_process')
const ts = require(process.env.TYPESCRIPT_JS)
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop:true}
}).outputText, filename)
const repo = process.argv[2], base = process.argv[3]
const source = require(path.join(repo,'apps/desktop/electron/archive-update-source.ts'))
const {createArchiveUpdateCaller, selectSourceBackend} = require(path.join(repo,'apps/desktop/electron/archive-update-caller.ts'))
const {runBootstrap} = require(path.join(repo,'apps/desktop/electron/bootstrap-runner.ts'))
const origin='https://github.com/AetherMesh-AI/Eidolon.git'
const remote=path.join(base,'remote'), home=path.join(base,'home'), archive=path.join(base,'archive')
for(const p of [remote,home,archive]) fs.mkdirSync(p,{recursive:true})
process.env.HOME=home; process.env.HERMES_HOME=path.join(home,'.eidolon')
process.env.GIT_CONFIG_GLOBAL=path.join(base,'gitconfig'); process.env.GIT_CONFIG_NOSYSTEM='1'
fs.writeFileSync(process.env.GIT_CONFIG_GLOBAL,`[url "file://${remote}"]\n insteadOf = ${origin}\n`)
const git=(...args)=>execFileSync('git',args,{cwd:remote,encoding:'utf8',stdio:['ignore','pipe','pipe']}).trim()
git('init','-b','main'); git('config','user.name','Fixture'); git('config','user.email','fixture@example.invalid')
fs.mkdirSync(path.join(remote,'scripts')); fs.mkdirSync(path.join(remote,'eidolon_cli'))
fs.writeFileSync(path.join(remote,'eidolon_cli/main.py'),'fixture source\n')
fs.writeFileSync(path.join(remote,'scripts/install.sh'),`#!/bin/bash
set -eu
if [ "$1" = --manifest ]; then
 printf '%s\\n' '{"protocol_version":1,"stages":[{"name":"prerequisites"},{"name":"venv"},{"name":"python-deps"},{"name":"repository"}]}'
 exit 0
fi
stage="$2"; shift 2
[ ! -f "$HOME/fail-bootstrap" ] || exit 19
root=''
while [ "$#" -gt 0 ]; do
 if [ "$1" = --dir ]; then root="$2"; shift 2; else shift; fi
done
[ -n "$root" ]
printf '%s\\n' "$stage" >> "$root/stages.log"
if [ "$stage" = venv ]; then
 mkdir -p "$root/venv/bin"
 printf '#!/bin/sh\\nprintf "prepared-backend\\\\n"\\n' > "$root/venv/bin/python"
 chmod +x "$root/venv/bin/python"
fi
printf '{"ok":true,"stage":"%s"}\\n' "$stage"
`)
git('add','.');git('commit','-m','archive'); const old=git('rev-parse','HEAD')
git('commit','--allow-empty','-m','update'); const head=git('rev-parse','HEAD')
fs.writeFileSync(path.join(archive,'keep'),'archive bytes');fs.writeFileSync(path.join(home,'config.yaml'),'keep: true\n')
const calls=[]
const runGit=async(args,opts={})=>{calls.push(args);const r=spawnSync('git',args,{cwd:opts.cwd||archive,encoding:'utf8',env:process.env});return {code:r.status??1,stdout:r.stdout||'',stderr:r.stderr||''}}
const prepare=root=>runBootstrap({installStamp:null,activeRoot:root,sourceRepoRoot:root,hermesHome:process.env.HERMES_HOME,stageNames:['prerequisites','venv','python-deps'],onEvent:()=>{},writeMarker:()=>{}})
async function main(){
 let response=0; const dialogs=[]
 const caller=createArchiveUpdateCaller({hermesHome:process.env.HERMES_HOME,runGit,
   showMessageBox:async options=>{dialogs.push(options);return {response}},emitProgress:()=>{}})
 const selected=()=>selectSourceBackend({hermesHome:process.env.HERMES_HOME,packaged:true,sourceRepoRoot:archive,
   backendArgs:['--fixture'],findSystemPython:()=>null})
 assert.equal(selected(),null)

 const check=await caller.check(archive,old)
 assert.equal(check.targetSha,head); assert.equal(check.migrationRequired,true)
 const same=await caller.check(archive,head)
 assert.equal(same.updateAvailable,true)
 const options={hermesHome:process.env.HERMES_HOME,runGit,prepare,confirm:async()=>false}
 const before=calls.length
 assert.equal((await caller.prepare()).ok,false)
 assert.equal(dialogs.at(-1).defaultId,0);assert.equal(dialogs.at(-1).cancelId,0)
 assert.equal(calls.length,before);assert.equal(source.readPreparedSource(options.hermesHome),null)
 const failed=await source.prepareArchiveUpdateWithConsent({...options,confirm:async()=>true,prepare:async()=>({ok:false,error:'fixture failure'})})
 assert.equal(failed.ok,false);assert.equal(source.readPreparedSource(options.hermesHome),null)
 assert.deepEqual(fs.readdirSync(path.join(options.hermesHome,'desktop-source')), [])
 response=1
 fs.writeFileSync(path.join(home,'fail-bootstrap'),'')
 assert.equal((await caller.prepare()).ok,false)
 assert.equal(selected(),null)
 assert.deepEqual(fs.readdirSync(path.join(options.hermesHome,'desktop-source')), [])
 fs.unlinkSync(path.join(home,'fail-bootstrap'))
 const result=await caller.prepare()
 assert.equal(result.ok,true,JSON.stringify(result));const root=result.root
 assert.equal(source.readPreparedSource(options.hermesHome),root)
 const drift=await runBootstrap({installStamp:null,activeRoot:root,sourceRepoRoot:root,hermesHome:options.hermesHome,stageNames:['missing-stage'],onEvent:()=>{},writeMarker:()=>{}})
 assert.equal(drift.ok,false)
 assert.equal(execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim(),head)
 assert.equal(execFileSync('git',['rev-list','--count','HEAD'],{cwd:root,encoding:'utf8'}).trim(),'2')
 assert.equal(execFileSync('git',['config','--get','remote.origin.url'],{cwd:root,encoding:'utf8'}).trim(),origin)
 assert.equal(fs.readFileSync(path.join(root,'stages.log'),'utf8'),'prerequisites\nvenv\npython-deps\n')
 assert.equal(selected().root,root)
 const backend=selected()
 assert.deepEqual(backend.args,['-m','eidolon_cli.main','--fixture'])
 assert.equal(backend.command,path.join(root,'venv/bin/python'))
 assert.equal(backend.bootstrap,false);assert.equal(backend.shell,false)
 assert.equal(execFileSync(backend.command,backend.args,{cwd:backend.root,env:backend.env,encoding:'utf8'}).trim(),'prepared-backend')
 assert.equal((await source.prepareArchiveUpdateWithConsent({...options,confirm:async()=>true,prepare:async()=>({ok:false})})).ok,false)
 assert.equal(source.readPreparedSource(options.hermesHome),root)
 // Exercise failed preparation through the real caller with an existing runtime.
 fs.writeFileSync(path.join(home,'fail-bootstrap'),'yes')
 assert.equal((await caller.prepare()).ok,false)
 assert.deepEqual(selected(),backend)
 assert.equal(execFileSync(selected().command,selected().args,{cwd:selected().root,env:selected().env,encoding:'utf8'}).trim(),'prepared-backend')
 fs.unlinkSync(path.join(home,'fail-bootstrap'))
 const failedGitCaller=createArchiveUpdateCaller({hermesHome:process.env.HERMES_HOME,
   runGit:async()=>({code:1,stdout:'',stderr:'fixture transport failure'}),
   showMessageBox:async()=>({response:1}),emitProgress:()=>{}})
 assert.equal((await failedGitCaller.check(archive,old)).error,'fetch-failed')
 assert.equal((await failedGitCaller.prepare()).ok,false)
 assert.deepEqual(selected(),backend)
 response=0
 assert.equal((await caller.prepare()).ok,false)
 assert.deepEqual(selected(),backend)
 assert.equal(selected().root,root)
 assert.equal(source.readPreparedSource(options.hermesHome),root)
 assert.equal(fs.readFileSync(path.join(archive,'keep'),'utf8'),'archive bytes')
 assert.equal(fs.readFileSync(path.join(home,'config.yaml'),'utf8'),'keep: true\n')
 console.log('PASS: archive check, same-SHA migration, consent, failed preparation, full-history origin, actual bootstrap stages, active backend execution, data preservation')
}
main().catch(e=>{console.error(e);process.exitCode=1})

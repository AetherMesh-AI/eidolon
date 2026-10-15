// Fixed compiler host: project files are parsed as data and never imported as code.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import readline from 'node:readline';
const config=JSON.parse(fs.readFileSync('/runner/config.json','utf8'));
const inventory=JSON.parse(fs.readFileSync('/runner/compiler-inventory.json','utf8'));
const sha=text=>crypto.createHash('sha256').update(text).digest('hex');
const canonical=value=>JSON.stringify(value,(_,v)=>v && !Array.isArray(v) && typeof v==='object' ? Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])) : v);
const status=Object.fromEntries(fs.readFileSync('/proc/self/status','utf8').split('\n').filter(x=>x.includes(':')).map(x=>{const at=x.indexOf(':');return [x.slice(0,at),x.slice(at+1).trim()];}));
const ids=key=>status[key].split(/\s+/).every(x=>x==='65534');
if(!ids('Uid') || !ids('Gid') || status.Groups!=='' || status.NoNewPrivs!=='1' || status.Seccomp!=='2' ||
 ['CapEff','CapPrm','CapInh','CapAmb'].some(k=>!/^0+$/.test(status[k]))) process.exit(202);
const proof=Object.fromEntries(['Uid','Gid','Groups','NoNewPrivs','Seccomp','CapEff','CapPrm','CapInh','CapAmb'].map(k=>[k,status[k]]));
process.stdout.write(JSON.stringify({kind:'ready',status:proof})+'\n');
const lines=readline.createInterface({input:process.stdin});
await new Promise((resolve,reject)=>lines.once('line',line=>line==='GO'?resolve():reject(Error('Invalid start'))));
lines.close();
const {default:ts}=await import('/runner/compiler/lib/typescript.js');
const selected=new Map(config.files.map(item=>['/source/'+item.path,{...item,kind:'source'}]));
const libraries=new Map(inventory.files.filter(item=>item.path.startsWith('lib/') && item.path.endsWith('.d.ts')).map(item=>['/runner/compiler/'+item.path,{...item,kind:'compiler_library'}]));
const allowed=new Map([...selected,...libraries]);
const reads=new Map();
const root='/source/'+config.root;
const normalize=name=>path.posix.resolve(root,name);
const read=name=>{
 name=normalize(name);const descriptor=allowed.get(name);
 if(!descriptor) return undefined;
 const text=fs.readFileSync(name,'utf8');
 if(sha(text)!==descriptor.sha256) throw Error('Exact compiler input identity changed');
 reads.set(name,{...descriptor,path:name});return text;
};
// Verify the entire declared input closure, including unused selected data.
// Every descriptor in the receipt corresponds to a real bounded read/hash.
for(const name of allowed.keys()) read(name);
const configPath='/source/'+config.configuration;
if(!selected.has(configPath)) throw Error('Typecheck configuration is not selected');
const document=JSON.parse(read(configPath));
if(!document || typeof document!=='object' || Array.isArray(document) || Object.keys(document).some(k=>k!=='compilerOptions')) throw Error('Unsupported TypeScript configuration fields');
const supplied=document.compilerOptions??{};
const booleans=new Set(['strict','noUnusedLocals','noUnusedParameters','exactOptionalPropertyTypes','noUncheckedIndexedAccess']);
if(!supplied || typeof supplied!=='object' || Array.isArray(supplied) || Object.keys(supplied).some(k=>!booleans.has(k)) || Object.values(supplied).some(v=>typeof v!=='boolean')) throw Error('Unsupported TypeScript compiler option');
const options={strict:true,...supplied,noEmit:false,noCheck:false,incremental:false,composite:false,
 skipLibCheck:false,skipDefaultLibCheck:false,libReplacement:false,types:[],
 target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,moduleResolution:ts.ModuleResolutionKind.Bundler,
 allowImportingTsExtensions:false,declaration:false,sourceMap:false,rootDir:root,outDir:'/emitted',noEmitOnError:true,emitBOM:false,newLine:ts.NewLineKind.LineFeed};
const names=[...selected.keys()].filter(name=>name.endsWith('.ts')).sort();
if(!names.some(name=>!name.endsWith('.d.ts'))) throw Error('Typecheck requires nonempty selected TypeScript source');
const directories=new Set();
for(const filename of allowed.keys()) {let parent=path.posix.dirname(filename);while(parent!=='/') {directories.add(parent);parent=path.posix.dirname(parent);}}
const expected=new Map(names.filter(name=>!name.endsWith('.d.ts')).map(name=>{
 const relative=name.slice(root.length+1);
 if(relative.split('/').includes('node_modules')) throw Error('Dependency source emission is unsupported');
 return ['/emitted/'+relative.slice(0,-3)+'.js',[name]];
}));
if(expected.size<1 || expected.size>64) throw Error('Invalid build output count');
const outputs=new Map();let outputBytes=0,frameBudget=131072,invalidOutput=false;
const write=(name,text,bom,onError,sources)=>{
 try {
  const origins=(sources??[]).map(source=>source.fileName).sort();
  if(typeof name!=='string' || !expected.has(name) || outputs.has(name) || bom ||
    canonical(origins)!==canonical(expected.get(name)) || typeof text!=='string') throw Error('Unexpected build output');
  const bytes=Buffer.byteLength(text,'utf8');
  if(bytes>262144 || outputBytes+bytes>524288 || outputs.size>=64) throw Error('Build output byte limit exceeded');
  const relative=name.slice('/emitted/'.length);
  if(relative.split('/').some(part=>!part || part==='.' || part==='..') || relative.includes('\\')) throw Error('Invalid build output path');
  // Raw bytes and metadata are bounded before allocating encoded transport.
  const metadataBytes=Buffer.byteLength(canonical({path:relative,sources:origins}),'utf8');
  if(metadataBytes>8192) throw Error('Build output metadata limit exceeded');
  const framedBytes=metadataBytes+4*Math.ceil(bytes/3)+160;
  if(frameBudget+framedBytes>1048576) throw Error('Build serialized frame limit exceeded');
  frameBudget+=framedBytes;
  const data=Buffer.from(text,'utf8');outputBytes+=bytes;
  outputs.set(name,{path:relative,sources:origins,bytes,sha256:sha(data),base64:data.toString('base64')});
 } catch(error) {invalidOutput=true;throw error;}
};
const host={
 getSourceFile(name,languageVersion){const text=read(name);if(text===undefined) return undefined;const source=ts.createSourceFile(normalize(name),text,languageVersion);if(source.checkJsDirective?.enabled===false) throw Error('Source-level typecheck suppression is unsupported');return source;},
 getDefaultLibFileName:()=>'/runner/compiler/lib/'+ts.getDefaultLibFileName(options),
 writeFile:write,
 getCurrentDirectory:()=>root,getCanonicalFileName:name=>normalize(name),useCaseSensitiveFileNames:()=>true,
 getNewLine:()=> '\n',fileExists:name=>allowed.has(normalize(name)),readFile:read,
 directoryExists:name=>directories.has(normalize(name)),realpath:normalize,
 getDirectories:name=>[...directories].filter(item=>path.posix.dirname(item)===normalize(name)).map(item=>path.posix.basename(item)),
};
const program=ts.createProgram({rootNames:[...names,'/runner/compiler/lib/lib.es2022.full.d.ts'],options,host});
const diagnostics=ts.getPreEmitDiagnostics(program);
let emitSkipped=true;
if(!diagnostics.some(item=>item.category===ts.DiagnosticCategory.Error)) {
 const emitted=program.emit();emitSkipped=emitted.emitSkipped;diagnostics.push(...emitted.diagnostics);
}
for(const diagnostic of diagnostics) process.stderr.write(ts.formatDiagnostics([diagnostic],{getCanonicalFileName:x=>x,getCurrentDirectory:()=>root,getNewLine:()=> '\n'}));
if(invalidOutput || emitSkipped || outputs.size!==expected.size) throw Error('Build did not produce the exact complete output set');
const actualInputs=[...reads.values()].sort((a,b)=>Buffer.compare(Buffer.from(a.path,'utf8'),Buffer.from(b.path,'utf8')));
const errors=diagnostics.filter(item=>item.category===ts.DiagnosticCategory.Error).length;
const emittedOutputs=[...outputs.values()].sort((a,b)=>Buffer.compare(Buffer.from(a.path,'utf8'),Buffer.from(b.path,'utf8')));
const outputManifest=emittedOutputs.map(({path,sources,bytes,sha256})=>({path,sources,bytes,sha256}));
const frame=JSON.stringify({kind:'build_result',success:errors===0,inputCount:names.filter(name=>!name.endsWith('.d.ts')).length,
 diagnosticCount:errors,effectiveConfiguration:options,effectiveConfigurationSha256:sha(canonical(options)),
 actualInputs,actualInputsSha256:sha(canonical(actualInputs)),outputs:emittedOutputs,outputManifestSha256:sha(canonical(outputManifest))});
if(Buffer.byteLength(frame,'utf8')+1>1048576) throw Error('Build frame limit exceeded');
process.stdout.write(frame+'\n',()=>process.exit(errors===0?0:1));

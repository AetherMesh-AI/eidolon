// PID 1 imports only fixed built-ins. Project code runs in a different UID.
import fs from 'node:fs';
import {StringDecoder} from 'node:string_decoder';
import { spawn } from 'node:child_process';
const config=JSON.parse(fs.readFileSync('/runner/config.json','utf8'));
const emit=value=>process.stdout.write(JSON.stringify(value)+'\n');
const status=Object.fromEntries(fs.readFileSync('/proc/self/status','utf8').split('\n').filter(x=>x.includes(':')).map(x=>{const at=x.indexOf(':');return [x.slice(0,at),x.slice(at+1).trim()];}));
const fail=reason=>{emit({kind:'blocked',reason});process.exit(202);};
if(process.pid!==1 || process.getuid()!==0 || status.NoNewPrivs!=='1' || status.Seccomp!=='2' ||
   !/^0*c0$/.test(status.CapEff) || !/^0*c0$/.test(status.CapPrm) || !/^0+$/.test(status.CapInh) || !/^0+$/.test(status.CapAmb)) fail('Supervisor identity unavailable');
const read=name=>fs.readFileSync('/sys/fs/cgroup/'+name,'utf8').trim();
if(read('memory.max')!==String(config.memoryBytes) || read('memory.swap.max')!=='0' || read('pids.max')!=='64' || read('cpu.max')!=='100000 100000') fail('Cgroup bounds unavailable');
const mounts=fs.readFileSync('/proc/self/mountinfo','utf8').split('\n').map(line=>line.split(' '));
for(const target of ['/','/source','/runner','/sys/fs/cgroup']) {
  const entry=mounts.find(x=>x[4]===target);
  if(!entry || !entry[5].split(',').includes('ro')) fail('Read-only mount unavailable: '+target);
}
const temporary=mounts.find(x=>x[4]==='/tmp');
if(!temporary || !['nosuid','nodev','noexec'].every(x=>temporary[5].split(',').includes(x))) fail('Scratch mount unavailable');
const stat=fs.statfsSync('/tmp');
if(stat.bsize*stat.blocks>config.scratchBytes || stat.files>4096) fail('Scratch bounds unavailable');
const namespaces=Object.fromEntries(['mnt','pid','net','ipc','uts','cgroup'].map(name=>[name,fs.readlinkSync('/proc/self/ns/'+name)]));
// Do not use child.kill(): UID 0 deliberately has no CAP_KILL. Exiting PID 1
// makes the kernel terminate every descendant, including detached processes.
setTimeout(()=>{emit({kind:'timeout'});process.exit(124);},config.timeoutMs);
process.on('SIGTERM',()=>process.exit(125));
const child=spawn('/usr/local/bin/node',['--disable-sigusr1','/runner/harness.mjs'],{
  uid:65534,gid:65534,cwd:'/source/'+config.root,
  env:{PATH:'/usr/local/bin:/usr/bin:/bin',LANG:'C.UTF-8',HOME:'/nonexistent',TMPDIR:'/tmp'},
  stdio:['pipe','pipe','pipe'],
});
let bytes=0,controlBytes=0,buffer='',ready=false,result=null;
const decoder=new StringDecoder('utf8');
const bound=chunk=>{bytes+=chunk.length;if(bytes>config.outputBytes) {emit({kind:'output_limit'});process.exit(126);}};
child.stderr.on('data',chunk=>{bound(chunk);process.stderr.write(chunk);});
child.stdout.on('data',chunk=>{
  controlBytes+=chunk.length;if(controlBytes>1048576) fail('Child control frame limit exceeded');
  buffer+=decoder.write(chunk);
  while(buffer.includes('\n')) {
    const at=buffer.indexOf('\n'),line=buffer.slice(0,at);buffer=buffer.slice(at+1);
    let record;try{record=JSON.parse(line);}catch{fail('Invalid child framing');}
    if(!ready && record.kind==='ready') {
      const s=record.status;
      if(!s || !['Uid','Gid'].every(k=>s[k]?.split(/\s+/).every(x=>x==='65534')) || s.Groups!=='' ||
        ['CapEff','CapPrm','CapInh','CapAmb'].some(k=>!/^0+$/.test(s[k])) || s.NoNewPrivs!=='1' || s.Seccomp!=='2') fail('Child isolation unavailable');
      ready=true;emit({kind:'isolation',namespaces,supervisorUid:0,workloadUid:65534,supervisorCapabilities:['SETUID','SETGID'],workloadCapabilities:[],pid:1});
      child.stdin.end('GO\n');
    } else if(ready && !result && record.kind==='build_result' && typeof record.success==='boolean' &&
      Number.isSafeInteger(record.inputCount) && record.inputCount>0 && Number.isSafeInteger(record.diagnosticCount) && record.diagnosticCount>=0 &&
      Array.isArray(record.outputs) && record.outputs.length>0 && record.outputs.length<=64 && Array.isArray(record.actualInputs) && record.actualInputs.length<=204) result=record;
    else fail('Unexpected child framing');
  }
});
child.on('error',()=>fail('Child launch failed'));
child.on('close',(code,signal)=>{
  buffer+=decoder.end();
  if(!ready || !result || buffer || signal || code!==0 || !result.success || result.diagnosticCount!==0) {emit({kind:'result',status:'failed'});process.exit(1);}
  process.stdout.write(JSON.stringify({...result,kind:'result',status:'passed'})+'\n',()=>process.exit(0));
});

// Trusted fixed harness. Only test results are claims by untrusted project code.
import fs from 'node:fs';
import readline from 'node:readline';
import { run } from 'node:test';
const config = JSON.parse(fs.readFileSync('/runner/config.json', 'utf8'));
const status = Object.fromEntries(fs.readFileSync('/proc/self/status', 'utf8').split('\n').filter(x => x.includes(':')).map(x => {const at=x.indexOf(':');return [x.slice(0,at),x.slice(at+1).trim()];}));
const ids = key => status[key].split(/\s+/).every(x => x === '65534');
if (!ids('Uid') || !ids('Gid') || status.Groups !== '' || status.NoNewPrivs !== '1' || status.Seccomp !== '2' ||
    ['CapEff','CapPrm','CapInh','CapAmb'].some(k => !/^0+$/.test(status[k]))) process.exit(202);
const proof=Object.fromEntries(['Uid','Gid','Groups','NoNewPrivs','Seccomp','CapEff','CapPrm','CapInh','CapAmb'].map(k=>[k,status[k]]));
process.stdout.write(JSON.stringify({kind:'ready',status:proof})+'\n');
const lines=readline.createInterface({input:process.stdin});
await new Promise((resolve,reject) => {lines.once('line',line=>line==='GO'?resolve():reject(Error('Invalid start')));});
lines.close();
let final=null;
const summaries=[];
for await (const event of run({files:config.tests,concurrency:1,isolation:'process',execArgv:['--disable-sigusr1']})) {
  if (event.type==='test:summary') {
    if (event.data.file) summaries.push(event.data);
    else {if(final) process.exit(203);final=event.data;}
  }
  if (event.type==='test:fail') process.stderr.write('Test failure: '+String(event.data.name)+'\n'+String(event.data.details?.error?.stack??'')+'\n'+String(event.data.details?.error?.cause?.stack??'')+'\n');
  if (event.type==='test:stdout' || event.type==='test:stderr') process.stderr.write(String(event.data.message));
}
const complete=final?.success===true && summaries.length===config.tests.length &&
  new Set(summaries.map(s=>s.file)).size===config.tests.length &&
  summaries.every(s=>config.tests.includes(s.file) && s.success===true);
const passed=summaries.reduce((sum,s)=>sum+s.counts.passed,0);
const skipped=summaries.reduce((sum,s)=>sum+s.counts.skipped+s.counts.todo,0);
process.stdout.write(JSON.stringify({kind:'result',success:complete && passed>0,passed,skipped})+'\n');
process.exit(complete && passed>0?0:1);

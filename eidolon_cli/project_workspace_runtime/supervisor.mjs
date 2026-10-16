// PID 1 never imports project code or prepared dependencies. Only this UID owns
// the outer receipt stream; child control frames remain untrusted claims.
import fs from 'node:fs';
import { spawn } from 'node:child_process';
import { performance } from 'node:perf_hooks';
import { CONTROL_BYTES, ControlFrames, validateConfig, absolutePath, verifyRuntimeToolchain,
  workloadEnvironment, writeControlFrameSync } from './protocol.mjs';

let outputBytes = 0;
let controlDeadline = Infinity, controlFailed = false;
function emit(value) {
  if (controlFailed) throw new Error('Supervisor control channel already failed');
  const frame = Buffer.from(JSON.stringify(value) + '\n');
  outputBytes += frame.length;
  if (outputBytes > CONTROL_BYTES) process.exit(202);
  try { writeControlFrameSync(1, frame, controlDeadline); }
  catch (error) { controlFailed = true; throw error; }
}
function fail(reason) {
  try { emit({ kind: 'blocked', reason }); }
  finally { process.exit(202); }
}

try {
  if (fs.statSync('/runner/config.json').size > CONTROL_BYTES) fail('Configuration size limit exceeded');
  const config = validateConfig(JSON.parse(fs.readFileSync('/runner/config.json', 'utf8')));
  const status = Object.fromEntries(fs.readFileSync('/proc/self/status', 'utf8').split('\n')
    .filter(line => line.includes(':')).map(line => {
      const at = line.indexOf(':'); return [line.slice(0, at), line.slice(at + 1).trim()];
    }));
  if (process.pid !== 1 || process.getuid() !== 0 || process.getgid() !== 0
    || status.NoNewPrivs !== '1' || status.Seccomp !== '2'
    || !['CapEff', 'CapPrm', 'CapBnd'].every(key => /^0*c0$/.test(status[key]))
    || !['CapInh', 'CapAmb'].every(key => /^0+$/.test(status[key]))
    || process.stdin.isTTY || process.stdout.isTTY || process.stderr.isTTY) fail('Supervisor identity unavailable');
  const glibcVersion = process.report.getReport().header.glibcVersionRuntime;
  const runtimeToolchain = verifyRuntimeToolchain(config.preparedToolchain, {
    platform: process.platform, arch: process.arch, libc: glibcVersion ? 'glibc' : null,
    glibcVersion, nodeVersion: process.versions.node, nodeAbi: process.versions.modules,
  });
  const read = name => fs.readFileSync('/sys/fs/cgroup/' + name, 'utf8').trim();
  if (read('memory.max') !== String(config.memoryBytes) || read('memory.swap.max') !== '0'
    || read('pids.max') !== '64' || read('cpu.max') !== '100000 100000') fail('Cgroup bounds unavailable');
  const mounts = fs.readFileSync('/proc/self/mountinfo', 'utf8').split('\n').map(line => line.split(' '));
  for (const target of ['/', '/source', '/runner', '/sys/fs/cgroup']) {
    const entry = mounts.find(parts => parts[4] === target);
    if (!entry || !entry[5].split(',').includes('ro')) fail('Read-only mount unavailable: ' + target);
  }
  if (mounts.some(parts => parts[4]?.startsWith('/source/') && !parts[5].split(',').includes('ro'))) {
    fail('Writable source submount');
  }
  const temporary = mounts.find(parts => parts[4] === '/tmp');
  if (!temporary || !['nosuid', 'nodev', 'noexec'].every(flag => temporary[5].split(',').includes(flag))) {
    fail('Scratch mount unavailable');
  }
  const stat = fs.statfsSync('/tmp');
  if (stat.bsize * stat.blocks > config.scratchBytes || stat.files > 4096) fail('Scratch bounds unavailable');
  const namespaces = Object.fromEntries(['mnt', 'pid', 'net', 'ipc', 'uts', 'cgroup']
    .map(name => [name, fs.readlinkSync('/proc/self/ns/' + name)]));
  process.setgroups([]);
  // UID 0 deliberately lacks CAP_KILL. Exiting namespace PID 1 lets the kernel
  // terminate the workload and every descendant, including detached children.
  controlDeadline = performance.now() + config.timeoutMs;
  setTimeout(() => {
    try { emit({ kind: 'timeout' }); }
    finally { process.exit(124); }
  }, config.timeoutMs);
  process.on('SIGTERM', () => process.exit(125));
  const child = spawn('/usr/local/bin/node', ['--disable-sigusr1', '/runner/harness.mjs'], {
    uid: 65534, gid: 65534, cwd: absolutePath(config, config.workspace),
    env: workloadEnvironment(),
    stdio: ['pipe', 'pipe', 'pipe', 'pipe'],
  });
  const frames = new ControlFrames(config, () => {
    emit({ kind: 'isolation', namespaces, supervisorUid: 0, workloadUid: 65534,
      supervisorCapabilities: ['SETUID', 'SETGID'], workloadCapabilities: [], pid: 1, runtimeToolchain });
    child.stdin.end('GO\n');
  });
  let logBytes = 0;
  const log = chunk => {
    logBytes += chunk.length;
    if (logBytes > config.outputBytes) { emit({ kind: 'output_limit' }); process.exit(126); }
    process.stderr.write(chunk);
  };
  child.stdout.on('data', log);
  child.stderr.on('data', log);
  child.stdio[3].on('data', chunk => {
    try { frames.push(chunk); } catch { fail('Invalid, extra or excessive child framing'); }
  });
  child.stdin.on('error', () => fail('Child start handshake failed'));
  child.stdio[3].on('error', () => fail('Child control channel failed'));
  child.on('error', () => fail('Child launch failed'));
  child.on('close', (code, signal) => {
    let result, exitCode;
    try {
      result = frames.finish(code, signal);
      exitCode = 0;
    } catch {
      // Preserve a structurally valid failure report, but never promote an
      // otherwise-passed claim after an abnormal exit or missing protocol tail.
      result = frames.result && frames.ready ? { ...frames.result, status: 'failed' }
        : { kind: 'blocked', reason: 'Incomplete child result or exit' };
      exitCode = 1;
    }
    try { emit(result); }
    catch { process.exit(202); }
    process.exit(exitCode);
  });
} catch {
  fail('Workspace supervisor prerequisites unavailable');
}

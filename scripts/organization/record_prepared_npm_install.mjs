// Disposable CI preparation only. Never invoked by an organization workload.
// Run in the exact official image, with a clean manifest-only /work and no credentials.
import fs from 'node:fs';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const IMAGE = 'node@sha256:a5f4366e834695d5f363b920c104953a535d3a9864d1bdf74566a0ebe01daee5';
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
export const canonical = value => JSON.stringify(Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))));
export function validateRegistryLock(rawLock) {
const lock = JSON.parse(rawLock);
if (!lock || lock.lockfileVersion !== 3 || !lock.packages
    || typeof lock.packages !== 'object' || Array.isArray(lock.packages)) {
  throw new Error('Expected exact version-three lock');
}
for (const [name, item] of Object.entries(lock.packages)) {
  if (!item || typeof item !== 'object' || Array.isArray(item)) throw new Error('Invalid package record');
  if (item.link === true) {
    const target = item.resolved;
    if (!name.split('/').includes('node_modules') || typeof target !== 'string'
        || !target || target.startsWith('/') || target.includes('\\')
        || target.split('/').some(part => !part || part === '.' || part === '..' || part === 'node_modules')
        || !Object.hasOwn(lock.packages, target) || lock.packages[target]?.link === true) {
      throw new Error('Workspace link must map to an exact contained lock workspace');
    }
    continue;
  }
  if (!name.split('/').includes('node_modules')) continue;
  const url = new URL(item.resolved);
  if (url.protocol !== 'https:' || url.host !== 'registry.npmjs.org' || url.username || url.password
      || url.search || url.hash || !url.pathname.endsWith('.tgz')
      || typeof item.integrity !== 'string' || !/^sha512-[A-Za-z0-9+/]{86}==$/.test(item.integrity)
      || Buffer.from(item.integrity.slice(7), 'base64').toString('base64') !== item.integrity.slice(7)) {
    throw new Error('Every installed dependency must have an integrity-pinned official registry archive');
  }
}
return lock;
}

export function prepare() {
if (process.cwd() !== '/work' || process.platform !== 'linux' || process.arch !== 'x64') {
  throw new Error('Preparation requires the pinned Linux x64 /work container');
}
const rawLock = fs.readFileSync('package-lock.json');
validateRegistryLock(rawLock);
const glibcVersion = process.report.getReport().header.glibcVersionRuntime;
if (typeof glibcVersion !== 'string') throw new Error('Prepared lane requires positively identified glibc');
const npmVersion = JSON.parse(fs.readFileSync('/usr/local/lib/node_modules/npm/package.json', 'utf8')).version;
const runtime = { schemaVersion: 1, image: IMAGE, platform: process.platform, arch: process.arch,
  libc: 'glibc', glibcVersion, nodeVersion: process.versions.node, nodeAbi: process.versions.modules, npmVersion };
const command = ['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund', '--bin-links=false'];
fs.writeFileSync('/tmp/empty-user-npmrc', '', { flag: 'wx', mode: 0o600 });
fs.writeFileSync('/tmp/empty-global-npmrc', '', { flag: 'wx', mode: 0o600 });
const installed = spawnSync(process.execPath, ['/usr/local/lib/node_modules/npm/bin/npm-cli.js', ...command.slice(1)], {
  cwd: '/work', stdio: 'inherit', timeout: 600000,
  env: { PATH: '/usr/local/bin:/usr/bin:/bin', HOME: '/tmp', TMPDIR: '/tmp',
    npm_config_cache: '/tmp/npm-cache', npm_config_registry: 'https://registry.npmjs.org',
    npm_config_userconfig: '/tmp/empty-user-npmrc', npm_config_globalconfig: '/tmp/empty-global-npmrc',
    npm_config_ignore_scripts: 'true', npm_config_audit: 'false', npm_config_fund: 'false', CI: '1' },
});
if (installed.error || installed.signal || installed.status !== 0) throw new Error('Reviewed npm preparation did not succeed');
if (!fs.readFileSync('package-lock.json').equals(rawLock)) throw new Error('Preparation changed its bound lockfile');
const record = { schemaVersion: 1, command, exitCode: 0, lockfileSha256: hash(rawLock),
  runtimeMetadataSha256: hash(canonical(runtime)), registry: 'https://registry.npmjs.org', lifecycleScripts: false };
fs.writeFileSync('runtime-metadata.json', JSON.stringify(runtime), { flag: 'wx', mode: 0o600 });
fs.writeFileSync('installation-record.json', JSON.stringify(record), { flag: 'wx', mode: 0o600 });

}

if (process.argv[1] === fileURLToPath(import.meta.url)) prepare();

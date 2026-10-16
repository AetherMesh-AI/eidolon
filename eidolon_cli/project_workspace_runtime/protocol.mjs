// Fixed, dependency-free transport and scope checks. Workload results are claims.
import { StringDecoder } from 'node:string_decoder';
import { writeSync } from 'node:fs';
import { performance } from 'node:perf_hooks';
import { Socket } from 'node:net';
import { types } from 'node:util';

export const CONTROL_BYTES = 1048576;
const controlClock = performance.now.bind(performance);
const controlWait = Atomics.wait.bind(Atomics);
const controlWaitArray = new Int32Array(new SharedArrayBuffer(4));
let childControlChannel;

export function writeControlFrameSync(descriptor, data, deadline = Infinity) {
  requireValue([1, 3].includes(descriptor) && Buffer.isBuffer(data)
    && data.length > 0 && data.length <= CONTROL_BYTES && data[data.length - 1] === 10
    && typeof deadline === 'number' && !Number.isNaN(deadline), 'Invalid control frame write');
  // Node inherits fd3 as a blocking socket. Opening its existing handle through
  // libuv makes it nonblocking; this never creates a network connection. Retain
  // the wrapper, and use only the checked byte writes below (no stream queue).
  if (descriptor === 3) childControlChannel ??= new Socket({ fd: 3, readable: false, writable: true });
  else void process.stdout.isTTY;
  const stop = Math.min(deadline, controlClock() + 5000);
  let offset = 0;
  while (offset < data.length) {
    requireValue(controlClock() < stop, 'Control frame write deadline exceeded');
    let retry = false;
    try {
      const written = writeSync(descriptor, data, offset, data.length - offset);
      requireValue(Number.isInteger(written) && written > 0 && written <= data.length - offset,
        'Control frame write made invalid progress');
      offset += written;
    } catch (error) {
      if (!['EAGAIN', 'EWOULDBLOCK', 'EINTR'].includes(error?.code)) throw error;
      retry = true;
    }
    const remaining = stop - controlClock();
    requireValue(remaining > 0, 'Control frame write deadline exceeded');
    if (offset === data.length) return;
    // stdout is nonblocking once Node initializes process.stdout. A single
    // write can therefore succeed with only a prefix, or report EAGAIN. Never
    // exit successfully before all bytes reach the pipe. Bound backpressure by
    // the supervisor's existing deadline as well as a fixed per-write ceiling.
    if (retry) controlWait(controlWaitArray, 0, 0, Math.min(1, remaining));
  }
}

export const TOOL_ENTRIES = Object.freeze({
  workspace_typescript: 'node_modules/typescript/lib/typescript.js',
  workspace_vitest_ui: 'node_modules/vitest/dist/node.js',
});
export function workloadEnvironment() {
  return { PATH: '/usr/local/bin:/usr/bin:/bin', LANG: 'C.UTF-8', HOME: '/nonexistent',
    TMPDIR: '/tmp', XDG_CACHE_HOME: '/tmp/cache', CI: '1', NO_COLOR: '1',
    RAYON_NUM_THREADS: '1', UV_THREADPOOL_SIZE: '2' };
}
const requireValue = (condition, reason) => { if (!condition) throw new Error(reason); };
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join(',') === [...keys].sort().join(',');
const integer = (value, low, high = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(value)
  && value >= low && value <= high;

export function formatWorkloadError(error) {
  // Diagnostics are workload claims, and host redaction still applies. Read
  // only native Error data fields: never serialize objects or invoke their
  // getters/toString (including Node's lazily computed stack accessor).
  const own = (value, key) => Object.getOwnPropertyDescriptor(value, key)?.value;
  const clipped = (value, bytes) => new StringDecoder('utf8')
    .write(Buffer.from(value.slice(0, bytes)).subarray(0, bytes));
  let remaining = 8191, output = '';
  const append = value => {
    const text = clipped(value, remaining);
    output += text;
    remaining -= Buffer.byteLength(text);
  };
  append('Workspace recipe failed (workload diagnostic):\n');
  const queue = [{ value: error, label: 'error', depth: 0 }], seen = new Set();
  for (let index = 0; index < queue.length && index < 16 && remaining > 0; index++) {
    const { value, label, depth } = queue[index];
    if (!types.isNativeError(value)) {
      append(label + ': ' + (typeof value === 'string' ? clipped(value, 512)
        : '[non-Error value omitted]') + '\n');
      continue;
    }
    if (seen.has(value)) { append(label + ': [cycle omitted]\n'); continue; }
    seen.add(value);
    const prototype = Object.getPrototypeOf(value);
    const name = own(value, 'name') ?? (prototype && !types.isProxy(prototype) ? own(prototype, 'name') : null);
    const message = own(value, 'message'), stack = own(value, 'stack');
    append(label + ': ' + (typeof name === 'string' ? clipped(name, 64) : 'Error') + ': '
      + (typeof message === 'string' ? clipped(message, 512) : '[message unavailable]') + '\n');
    if (typeof stack === 'string') append(clipped(stack, 512) + '\n');
    if (depth >= 4) { append('[nested error depth limit]\n'); continue; }
    const add = (nested, nestedLabel) => {
      if (queue.length < 16) queue.push({ value: nested, label: nestedLabel, depth: depth + 1 });
      else append('[nested error entry limit]\n');
    };
    const cause = own(value, 'cause');
    if (cause !== undefined) add(cause, label + '.cause');
    const errors = own(value, 'errors');
    if (Array.isArray(errors) && !types.isProxy(errors)) {
      const length = own(errors, 'length');
      for (let at = 0; at < Math.min(length, 8); at++) add(own(errors, String(at)), label + '.errors[' + at + ']');
      if (length > 8) append('[aggregate error entry limit]\n');
    }
  }
  return output + '\n';
}

export function relativePath(value) {
  requireValue(typeof value === 'string' && value.length > 0 && Buffer.byteLength(value) <= 512
    && !/[\\\x00-\x1f\x7f:*?\[\]{}]/.test(value)
    && value.split('/').length <= 32 && value.split('/').every(part => part && part !== '.' && part !== '..'),
  'Expected an exact bounded relative path');
  return value;
}

export function isSource(value) {
  return /\.tsx?$/.test(value) && !/\.d\.ts$/.test(value) && !value.split('/').includes('node_modules');
}

function paths(values, limit) {
  requireValue(Array.isArray(values) && values.length <= limit, 'Selection exceeds file bound');
  values.forEach(relativePath);
  requireValue(new Set(values.map(value => value.toLowerCase())).size === values.length,
    'Selection contains duplicate or ambiguous paths');
  return [...values].sort();
}

export function validateConfig(input) {
  const required = ['root', 'recipe', 'workspace', 'configuration', 'toolEntry', 'preparedToolchain', 'timeoutMs',
    'memoryBytes', 'scratchBytes', 'outputBytes', 'tests', 'selectedSources'];
  requireValue(exact(input, required), 'Configuration requires exact fixed fields');
  requireValue(/^root(?:0|[1-9][0-9]*)$/.test(input.root), 'Invalid source root');
  requireValue(Object.hasOwn(TOOL_ENTRIES, input.recipe) && input.toolEntry === TOOL_ENTRIES[input.recipe],
    'Unsupported recipe or tool entry');
  requireValue(exact(input.preparedToolchain, ['platform', 'arch', 'libc', 'nodeVersion',
    'npmVersion', 'nodeAbi', 'tools']), 'Prepared toolchain fields are incomplete');
  const workspace = relativePath(input.workspace), configuration = relativePath(input.configuration);
  requireValue(configuration.startsWith(workspace + '/'), 'Configuration is outside workspace');
  const bounds = { timeoutMs: [1, 1800000], memoryBytes: [256 * 1048576, 8192 * 1048576],
    scratchBytes: [16 * 1048576, 2048 * 1048576], outputBytes: [1024, 1048576] };
  for (const [key, [low, high]] of Object.entries(bounds)) {
    requireValue(integer(input[key], low, high), 'Resource bound is invalid: ' + key);
  }
  const tests = paths(input.tests, 512), selectedSources = paths(input.selectedSources, 10000);
  if (input.recipe === 'workspace_typescript') {
    requireValue(configuration.endsWith('.json') && tests.length === 0 && selectedSources.length > 0
      && selectedSources.every(isSource), 'Typecheck requires an actual configuration and source selection');
  } else {
    requireValue(selectedSources.length === 0 && tests.length > 0
      && tests.every(value => value.startsWith(workspace + '/src/') && /\.test\.tsx?$/.test(value)),
    'UI tests require exact workspace test files');
  }
  return Object.freeze({ ...input, tests: Object.freeze(tests), selectedSources: Object.freeze(selectedSources) });
}

export const absolutePath = (config, value) => '/source/' + config.root + '/' + value;
export const selectedFiles = config => (config.recipe === 'workspace_typescript'
  ? config.selectedSources : config.tests).map(value => absolutePath(config, value));

export function formatMissingSelectedSources(config, reportedFiles) {
  const missing = selectedFiles(config).filter(path => !reportedFiles.has(path));
  if (missing.length === 0) return '';
  let output = 'TypeScript reported source coverage incomplete (workload claim): '
    + missing.length + ' selected paths absent.\n', shown = 0;
  for (const path of missing.slice(0, 16)) {
    const line = '  ' + path + '\n';
    // Keep paths complete and reserve space for the bounded omission count.
    if (Buffer.byteLength(output) + Buffer.byteLength(line) + 128 > 4096) break;
    output += line;
    shown++;
  }
  return output + 'Listed ' + shown + ' of ' + missing.length
    + ' missing selected paths (maximum 16 paths / 4096 bytes).\n';
}

export function createWorkspaceTypecheckProgram(compiler, configuration, selectedRoots) {
  const enforced = { noEmit: true, noCheck: false, incremental: false }, fatal = [];
  // Parse at the original configuration path so extends, paths, ambient roots,
  // and configFilePath retain TypeScript's own resolution semantics.
  const parsed = compiler.getParsedCommandLineOfConfigFile(configuration, enforced, {
    ...compiler.sys, onUnRecoverableConfigFileDiagnostic: diagnostic => fatal.push(diagnostic),
  });
  const parsingDiagnostics = [...fatal, ...(parsed?.options.configFile?.parseDiagnostics ?? []),
    ...(parsed?.errors ?? [])];
  if (!parsed || parsingDiagnostics.length) return { program: null, diagnostics: parsingDiagnostics };
  requireValue(!parsed.projectReferences?.length,
    'TypeScript project references are unsupported by this fixed workspace recipe');
  const options = { ...parsed.options, ...enforced };
  // TypeScript keeps its original configuration AST on a non-enumerable option.
  // Retain that metadata for config-origin diagnostics and resolution semantics.
  const configFile = Object.getOwnPropertyDescriptor(parsed.options, 'configFile');
  if (configFile) Object.defineProperty(options, 'configFile', configFile);
  // Config globs can suppress a same-stem .tsx sibling. Preserve every parsed
  // root and explicitly add the exact authorized selection rather than losing
  // either the original config scope or the host's source-coverage contract.
  const program = compiler.createProgram({
    rootNames: [...new Set([...parsed.fileNames, ...selectedRoots])],
    options,
    configFileParsingDiagnostics: parsingDiagnostics,
  });
  return { program, diagnostics: compiler.getPreEmitDiagnostics(program) };
}

export function resultRecord(config, details = {}) {
  return { kind: 'result', status: 'failed', recipe: config.recipe,
    configuration: config.configuration, testCount: 0, skippedCount: 0, failedCount: 0,
    sourceCount: 0, selectedFiles: selectedFiles(config), reportedFiles: [], modules: [],
    evidenceOrigin: 'workload-claim', toolEntry: config.toolEntry, toolExitCode: null, ...details };
}

function absoluteFiles(values, config) {
  requireValue(Array.isArray(values) && values.length <= 10000, 'Invalid reported file list');
  const prefix = '/source/' + config.root + '/';
  for (const value of values) {
    requireValue(typeof value === 'string' && value.startsWith(prefix), 'Reported file escaped source');
    relativePath(value.slice(prefix.length));
  }
  requireValue(new Set(values).size === values.length, 'Duplicate reported file');
  return values;
}

const sameSet = (left, right) => left.length === right.length
  && (() => {
    const expected = [...right].sort();
    return [...left].sort().every((value, index) => value === expected[index]);
  })();

export function verifyRuntimeToolchain(prepared, observed) {
  requireValue(observed.platform === 'linux' && observed.arch === 'x64'
    && observed.libc === 'glibc' && typeof observed.glibcVersion === 'string'
    && /^\d+\.\d+(?:\.\d+)?$/.test(observed.glibcVersion)
    && ['platform', 'arch', 'libc', 'nodeVersion', 'nodeAbi']
      .every(key => typeof prepared[key] === 'string' && prepared[key] === observed[key]),
  'Prepared toolchain does not match the actual pinned runtime');
  return observed;
}

export function validateResult(record, config) {
  requireValue(exact(record, Object.keys(resultRecord(config))) && record.kind === 'result'
    && ['passed', 'failed'].includes(record.status) && record.recipe === config.recipe
    && record.configuration === config.configuration && record.toolEntry === config.toolEntry
    && record.evidenceOrigin === 'workload-claim'
    && (record.toolExitCode === null || integer(record.toolExitCode, 0, 255)), 'Invalid result identity');
  for (const key of ['testCount', 'skippedCount', 'failedCount', 'sourceCount']) {
    requireValue(integer(record[key], 0, 1000000), 'Invalid result count');
  }
  absoluteFiles(record.selectedFiles, config);
  absoluteFiles(record.reportedFiles, config);
  requireValue(sameSet(record.selectedFiles, selectedFiles(config)), 'Result selection changed');
  requireValue(Array.isArray(record.modules), 'Invalid module results');
  if (config.recipe === 'workspace_typescript') {
    requireValue(record.modules.length === 0 && record.testCount === 0 && record.skippedCount === 0
      && record.failedCount === 0 && record.sourceCount === record.reportedFiles.length
      && record.reportedFiles.every(isSource), 'Invalid typecheck source closure');
    if (record.status === 'passed') requireValue(record.sourceCount > 0 && record.toolExitCode === 0
      && record.selectedFiles.every(path => record.reportedFiles.includes(path)), 'Incomplete typecheck');
  } else {
    requireValue(record.sourceCount === 0 && record.modules.length === record.reportedFiles.length,
      'Incomplete UI module results');
    for (const module of record.modules) {
      requireValue(exact(module, ['path', 'passed', 'skipped', 'failed', 'state'])
        && ['passed', 'failed', 'skipped', 'pending', 'queued'].includes(module.state)
        && ['passed', 'skipped', 'failed'].every(key => integer(module[key], 0, 1000000)),
      'Invalid UI module result');
    }
    requireValue(sameSet(record.modules.map(module => module.path), record.reportedFiles)
      && new Set(record.modules.map(module => module.path)).size === record.modules.length
      && record.testCount === record.modules.reduce((sum, module) => sum + module.passed, 0)
      && record.skippedCount === record.modules.reduce((sum, module) => sum + module.skipped, 0)
      && record.failedCount === record.modules.reduce((sum, module) => sum + module.failed, 0),
    'UI counts or module identities disagree');
    if (record.status === 'passed') requireValue(record.testCount > 0 && record.failedCount === 0
      && record.toolExitCode === 0 && sameSet(record.reportedFiles, record.selectedFiles)
      && record.modules.every(module => module.state === 'passed' && module.passed > 0),
    'UI test selection did not complete meaningfully');
  }
  return record;
}

export function validateWorkloadIdentity(status) {
  requireValue(exact(status, ['Uid', 'Gid', 'Groups', 'NoNewPrivs', 'Seccomp',
    'CapEff', 'CapPrm', 'CapInh', 'CapAmb'])
    && ['Uid', 'Gid'].every(key => /^65534\s+65534\s+65534\s+65534$/.test(status[key]))
    && status.Groups === '' && status.NoNewPrivs === '1' && status.Seccomp === '2'
    && ['CapEff', 'CapPrm', 'CapInh', 'CapAmb'].every(key => typeof status[key] === 'string'
      && /^0+$/.test(status[key])), 'Child isolation unavailable');
}

export class ControlFrames {
  constructor(config, onReady) {
    this.config = config;
    this.onReady = onReady;
    this.decoder = new StringDecoder('utf8');
    this.buffer = '';
    this.bytes = 0;
    this.ready = false;
    this.result = null;
  }

  push(chunk) {
    this.bytes += chunk.length;
    requireValue(this.bytes <= CONTROL_BYTES, 'Child control frame limit exceeded');
    this.buffer += this.decoder.write(chunk);
    while (this.buffer.includes('\n')) {
      const index = this.buffer.indexOf('\n'), line = this.buffer.slice(0, index);
      this.buffer = this.buffer.slice(index + 1);
      const record = JSON.parse(line);
      if (!this.ready && exact(record, ['kind', 'status']) && record.kind === 'ready') {
        validateWorkloadIdentity(record.status);
        this.ready = true;
        this.onReady();
      } else {
        requireValue(this.ready && !this.result, 'Unexpected child framing');
        this.result = validateResult(record, this.config);
      }
    }
  }

  finish(code, signal) {
    requireValue(!this.buffer && !this.decoder.end() && this.ready && this.result
      && !signal && code === 0 && this.result.status === 'passed', 'Incomplete child result or exit');
    return this.result;
  }
}

export function summarizeVitestModules(modules) {
  requireValue(Array.isArray(modules) && modules.length <= 512, 'Invalid test module report');
  return modules.map(module => {
    requireValue(module.project?.name === 'ui', 'Unexpected test project');
    const summary = { path: module.moduleId, passed: 0, skipped: 0, failed: 0, state: module.state() };
    for (const test of module.children.allTests()) {
      const state = test.result().state;
      if (state === 'passed') summary.passed += 1;
      else if (state === 'skipped') summary.skipped += 1;
      else summary.failed += 1;
      requireValue(summary.passed + summary.skipped + summary.failed <= 1000000, 'Test count exceeded');
    }
    if (!module.ok() || module.errors().length > 0) summary.state = 'failed';
    return summary;
  }).sort((left, right) => left.path.localeCompare(right.path));
}

export function boundUiProject(projects) {
  requireValue(Array.isArray(projects), 'UI recipe requires explicit inline projects');
  const selected = projects.filter(project => project && typeof project === 'object'
    && !Array.isArray(project) && project.test?.name === 'ui');
  requireValue(selected.length === 1, 'UI recipe requires exactly one inline ui project');
  const project = selected[0];
  return [{ ...project, cacheDir: '/tmp/vite-cache/ui', test: { ...project.test,
    browser: { enabled: false }, api: false, coverage: { enabled: false },
    cache: { dir: '/tmp/vite-cache/ui' }, pool: 'forks', maxWorkers: 1,
    fileParallelism: false, maxConcurrency: 1, isolate: true, allowOnly: false,
    passWithNoTests: false, retry: 0, update: false } }];
}

export function vitestOptions(config, reporter) {
  return { root: absolutePath(config, config.workspace), config: absolutePath(config, config.configuration),
    configLoader: 'runner', project: ['ui'], run: true, watch: false, standalone: false,
    pool: 'forks', maxWorkers: 1, fileParallelism: false, maxConcurrency: 1, isolate: true,
    coverage: { enabled: false }, browser: { enabled: false }, ui: false, open: false, api: false,
    cache: { dir: '/tmp/vite-cache' }, passWithNoTests: false, allowOnly: false, retry: 0,
    update: false, reporters: [reporter], dangerouslyIgnoreUnhandledErrors: false,
    color: false, clearScreen: false, execArgv: ['--disable-sigusr1'] };
}

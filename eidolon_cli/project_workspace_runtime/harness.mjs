// This fixed bootstrap validates its identity before importing any workload.
// Everything observed after GO, including compiler/reporter data, is a claim.
import fs from 'node:fs';
import readline from 'node:readline';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { CONTROL_BYTES, validateConfig, validateWorkloadIdentity, validateResult, absolutePath,
  selectedFiles, isSource, resultRecord, createWorkspaceTypecheckProgram, summarizeVitestModules,
  boundUiProject, vitestOptions, writeControlFrameSync, formatWorkloadError,
  formatMissingSelectedSources } from './protocol.mjs';

const config = validateConfig(JSON.parse(fs.readFileSync('/runner/config.json', 'utf8')));
const status = Object.fromEntries(fs.readFileSync('/proc/self/status', 'utf8').split('\n')
  .filter(line => line.includes(':')).map(line => {
    const at = line.indexOf(':'); return [line.slice(0, at), line.slice(at + 1).trim()];
  }));
const proof = Object.fromEntries(['Uid', 'Gid', 'Groups', 'NoNewPrivs', 'Seccomp',
  'CapEff', 'CapPrm', 'CapInh', 'CapAmb'].map(key => [key, status[key]]));
validateWorkloadIdentity(proof);
let controlBytes = 0;
let controlFailed = false;
const emit = value => {
  if (controlFailed) throw new Error('Child control channel already failed');
  const data = Buffer.from(JSON.stringify(value) + '\n');
  controlBytes += data.length;
  if (controlBytes > CONTROL_BYTES) process.exit(203);
  try { writeControlFrameSync(3, data); }
  catch (error) { controlFailed = true; throw error; }
};
emit({ kind: 'ready', status: proof });
const lines = readline.createInterface({ input: process.stdin });
await new Promise((resolve, reject) => {
  lines.once('line', line => line === 'GO' ? resolve() : reject(Error('Invalid start')));
  lines.once('close', () => reject(Error('Missing start')));
});
lines.close();

function requirePreparedFiles() {
  const base = '/source/' + config.root + '/';
  for (const value of [config.configuration, config.toolEntry, ...config.tests, ...config.selectedSources]) {
    const filename = absolutePath(config, value);
    if (!fs.statSync(filename).isFile() || !fs.realpathSync(filename).startsWith(base)) {
      throw new Error('Selected input is absent or escaped the prepared source');
    }
  }
}

async function typecheck() {
  const imported = await import(pathToFileURL(absolutePath(config, config.toolEntry)).href);
  const compiler = imported.default ?? imported;
  const { program, diagnostics } = createWorkspaceTypecheckProgram(compiler,
    absolutePath(config, config.configuration), selectedFiles(config));
  const formatHost = { getCanonicalFileName: path => path,
    getCurrentDirectory: () => absolutePath(config, config.workspace), getNewLine: () => '\n' };
  let logBytes = 0;
  for (const diagnostic of diagnostics) {
    const data = Buffer.from(compiler.formatDiagnostics([diagnostic], formatHost));
    if (logBytes + data.length > config.outputBytes) process.exit(204);
    logBytes += data.length;
    process.stderr.write(data);
  }
  const prefix = '/source/' + config.root + '/';
  const sources = new Set((program?.getSourceFiles() ?? []).filter(source => !source.isDeclarationFile
    && source.fileName.startsWith(prefix) && isSource(source.fileName)).map(source => source.fileName));
  const reportedFiles = [...sources].sort();
  const missing = formatMissingSelectedSources(config, sources);
  if (missing) process.stderr.write(missing);
  const toolExitCode = program && diagnostics.length === 0 ? 0 : 1;
  return resultRecord(config, { toolExitCode, sourceCount: sources.size,
    reportedFiles, status: toolExitCode === 0 && sources.size > 0
      && selectedFiles(config).every(path => sources.has(path)) ? 'passed' : 'failed' });
}

async function runUiTests() {
  const { startVitest } = await import(pathToFileURL(absolutePath(config, config.toolEntry)).href);
  const requireFromWorkspace = createRequire(absolutePath(config, config.workspace + '/package.json'));
  const packageInstaller = {
    isPackageExists(name, options = {}) {
      try {
        const resolved = requireFromWorkspace.resolve(name, { paths: options.paths
          ?? [absolutePath(config, config.workspace), '/source/' + config.root] });
        return fs.realpathSync(resolved).startsWith('/source/' + config.root + '/');
      } catch { return false; }
    },
    async ensureInstalled(name, root) {
      if (!this.isPackageExists(name, { paths: [root, '/source/' + config.root] })) {
        throw new Error('Prepared dependency missing; installation is disabled: ' + name);
      }
      return true;
    },
  };
  let started = false, completed = false, modules = [], successful = false;
  const expected = selectedFiles(config).sort();
  const exactSelection = values => values.length === expected.length
    && new Set(values).size === values.length && [...values].sort().every((path, index) => path === expected[index]);
  const reporter = {
    onInit(ctx) {
      if (ctx.config.watch || ctx.config.ui || ctx.config.api?.port || ctx.config.coverage.enabled
        || ctx.projects.length !== 1 || ctx.projects[0].name !== 'ui'
        || ctx.projects[0].config.browser.enabled || ctx.projects[0].config.pool !== 'forks'
        || ctx.projects[0].config.fileParallelism || ctx.projects[0].config.maxWorkers !== 1
        || !ctx.projects[0].vite.config.cacheDir.startsWith('/tmp/')) {
        throw new Error('Resolved UI execution exceeded fixed recipe');
      }
    },
    onTestRunStart(specifications) {
      if (started || !exactSelection(specifications.map(spec => spec.moduleId))
        || specifications.some(spec => spec.project.name !== 'ui')) {
        throw new Error('Vitest selected an unexpected or repeated test set');
      }
      started = true;
    },
    onTestRunEnd(testModules, errors, reason) {
      if (!started || completed) throw new Error('Unexpected repeated or missing test run');
      completed = true;
      modules = summarizeVitestModules(testModules);
      successful = reason === 'passed' && errors.length === 0
        && exactSelection(modules.map(module => module.path))
        && modules.every(module => module.state === 'passed' && module.passed > 0 && module.failed === 0);
    },
    onUserConsoleLog(log) { process.stderr.write(String(log.content)); },
  };
  const ctx = await startVitest('test', expected, vitestOptions(config, reporter), {
    cacheDir: '/tmp/vite-cache', envFile: false, clearScreen: false,
    server: { watch: null, hmr: false, middlewareMode: true },
    plugins: [{ name: 'eidolon:fixed-ui-bounds', enforce: 'post', config(value) {
      // Returning another array would concatenate projects during Vite merging.
      value.test.projects = boundUiProject(value.test?.projects);
    } }],
  }, { packageInstaller, stdin: process.stdin, stdout: process.stderr, stderr: process.stderr });
  await ctx.close();
  const toolExitCode = process.exitCode === undefined ? 0 : Number(process.exitCode);
  return resultRecord(config, { modules, reportedFiles: modules.map(module => module.path),
    testCount: modules.reduce((sum, module) => sum + module.passed, 0),
    skippedCount: modules.reduce((sum, module) => sum + module.skipped, 0),
    failedCount: modules.reduce((sum, module) => sum + module.failed, 0), toolExitCode,
    status: started && completed && successful && toolExitCode === 0 ? 'passed' : 'failed' });
}

try {
  requirePreparedFiles();
  const recipes = { workspace_typescript: typecheck, workspace_vitest_ui: runUiTests };
  const result = validateResult(await recipes[config.recipe](), config);
  emit(result);
  process.exit(result.status === 'passed' ? 0 : 1);
} catch (error) {
  process.stderr.write(formatWorkloadError(error));
  if (!controlFailed) emit(resultRecord(config));
  process.exit(1);
}

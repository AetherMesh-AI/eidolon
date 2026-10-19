/** Observation-only receipt; Python remains the explicitly pinned checkout runtime. */
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { readFileSync, realpathSync } from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import type { ElectronApplication, Page } from '@playwright/test'
import type * as NodePty from 'node-pty'

import type { Sandbox } from './fixtures'
import { requirePackagedBinary } from './packaged-binary'

const { validateReceipt } = createRequire(import.meta.url)('../scripts/check-packaged-macos-proof.cjs') as {
  validateReceipt: (receipt: unknown, expected: { commit: string; tree: string; root: string; electron: string; arch: string; mainSha256: string; rendererSha256: string }) => unknown
}

const root = realpathSync(path.resolve(import.meta.dirname, '../../..'))
const desktop = path.join(root, 'apps/desktop')
const hash = (file: string) => createHash('sha256').update(readFileSync(file)).digest('hex')

export async function packagedMacosProof(app: ElectronApplication, page: Page, sandbox: Sandbox) {
  const git = (...args: string[]) => execFileSync('git', args, { cwd: root, encoding: 'utf8' }).trim()
  const commit = git('rev-parse', 'HEAD')
  const tree = git('rev-parse', 'HEAD^{tree}')
  assert.equal(commit, process.env.EIDOLON_PACKAGED_PROOF_SHA, 'Packaged acceptance requires the exact CI source')
  assert.equal(git('diff', '--name-only'), '')
  assert.equal(git('diff', '--cached', '--name-only'), '')
  assert.equal(git('write-tree'), tree)
  const executable = requirePackagedBinary(path.join(desktop, 'release'))

  const renderer = fileURLToPath(page.url())

  const runtime = await app.evaluate(async ({ app, BrowserWindow }, renderer) => {
    const fs = process.getBuiltinModule('fs')
    const path = process.getBuiltinModule('path')
    const crypto = process.getBuiltinModule('crypto')
    const module = process.getBuiltinModule('module')
    const appPath = app.getAppPath()
    const resources = fs.realpathSync(process.resourcesPath)

    if (fs.realpathSync(appPath) !== appPath) { throw new Error('Packaged app archive is redirected') }
    const metadata = JSON.parse(fs.readFileSync(path.join(appPath, 'package.json'), 'utf8'))
    const main = path.resolve(appPath, metadata.main)
    const require = module.createRequire(main)
    const physical = (file: string) => fs.realpathSync(file.replace('/app.asar/', '/app.asar.unpacked/'))
    const hash = (file: string) => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')
    const mainPhysical = physical(main)
    const rendererPhysical = fs.realpathSync(renderer)

    if (rendererPhysical !== renderer) { throw new Error('Loaded renderer is redirected') }

    const ptyModule = physical(require.resolve('node-pty'))
    const ptyRoot = resources + '/app.asar.unpacked/dist/node_modules/node-pty/'

    if (!ptyModule.startsWith(ptyRoot)) {
      throw new Error('node-pty resolved outside the packaged bundle')
    }

    const pty = require('node-pty') as typeof NodePty

    const terminal = pty.spawn('/bin/sh', ['-c', 'printf EIDOLON_PACKAGED_PTY'], {
      name: 'xterm', cols: 80, rows: 24,
      cwd: process.env.HOME,
      env: { HOME: process.env.HOME!, PATH: '/usr/bin:/bin', TERM: 'xterm' },
    })

    const nativePty = await new Promise<{ exitCode: number; sentinel: boolean }>((resolve, reject) => {
      let output = ''

      const timer = setTimeout(() => {
        terminal.kill()
        reject(new Error('Packaged native PTY timed out'))
      }, 10_000)

      terminal.onData(data => { output = (output + data).slice(0, 1024) })
      terminal.onExit(({ exitCode }) => {
        clearTimeout(timer)
        resolve({ exitCode, sentinel: output === 'EIDOLON_PACKAGED_PTY' })
      })
    })

    const nativeModules = Object.keys(require.cache)
      .filter(file => file.endsWith('.node') && file.includes('node-pty'))
      .map(file => ({ path: physical(file), sha256: hash(file) }))

    const stamp = JSON.parse(fs.readFileSync(path.join(resources, 'install-stamp.json'), 'utf8'))

    return {
      host: { platform: process.platform, arch: process.arch, electron: process.versions.electron,
        node: process.versions.node, modules: process.versions.modules },
      bundle: {
        executable: fs.realpathSync(process.execPath), resources, app: appPath, main, mainPhysical, renderer: rendererPhysical,
        isPackaged: app.isPackaged, productName: metadata.productName,
        ptyModule, nativeModules, nativePty,
        executableSha256: hash(process.execPath), asarSha256: hash(appPath), mainSha256: hash(main),
        rendererSha256: hash(rendererPhysical),
        stampCommit: stamp.commit, stampDirty: stamp.dirty,
        visibleWindows: BrowserWindow.getAllWindows().filter(window => window.isVisible()).length,
      },
      isolation: {
        home: fs.realpathSync(process.env.HOME!), profile: fs.realpathSync(process.env.HERMES_HOME!),
        userData: fs.realpathSync(app.getPath('userData')),
        guardRoot: fs.realpathSync(process.env.EIDOLON_FRESH_TEAM_GUARD_DIR!),
        bootFake: Boolean(process.env.HERMES_DESKTOP_BOOT_FAKE || process.env.HERMES_DESKTOP_BOOT_FAKE_ERROR),
        devServer: Boolean(process.env.HERMES_DESKTOP_DEV_SERVER),
        sourceAppArgument: process.argv.some(argument => argument === process.env.HERMES_DESKTOP_HERMES_ROOT ||
          argument === path.join(process.env.HERMES_DESKTOP_HERMES_ROOT!, 'apps/desktop')),
      },
    }
  }, renderer)

  assert.equal(runtime.bundle.executable, executable)
  assert.equal(realpathSync(runtime.bundle.resources), runtime.bundle.resources)
  assert.equal(runtime.bundle.mainSha256, hash(path.join(desktop, 'dist/electron-main.mjs')))
  assert.equal(runtime.bundle.rendererSha256, hash(path.join(desktop, 'dist/index.html')))

  const executableName = execFileSync('/usr/bin/plutil', ['-extract', 'CFBundleExecutable', 'raw', '-o', '-',
    path.join(runtime.bundle.resources, '../Info.plist')], { encoding: 'utf8' }).trim()

  assert.equal(runtime.bundle.renderer, path.join(runtime.bundle.resources, 'app.asar.unpacked/dist/index.html'))
  assert.equal(runtime.isolation.profile, sandbox.hermesHome)
  assert.equal(runtime.isolation.userData, sandbox.userDataDir)
  // Verify the actual live gateway process, not just the test runner's Python.
  // Only fixed path comparisons and the existing synthetic guard marker are exported.
  const python = path.join(root, '.venv/bin/python')

  const liveGateways = JSON.parse(execFileSync(python, ['-c', `
import json,os,sys
from pathlib import Path
import psutil
pid,root,home,evidence=int(sys.argv[1]),sys.argv[2],sys.argv[3],Path(sys.argv[4])
rows=[]
for child in psutil.Process(pid).children(recursive=True):
 try:
  args=child.cmdline()
  if not ('-m' in args and 'eidolon_cli.main' in args and 'serve' in args): continue
  env=child.environ()
  marker=json.loads((evidence/('installed-'+str(child.pid)+'.json')).read_text())
  rows.append({'pid':child.pid,'birth':child.create_time(),
   'pythonMatches':args[0]==root+'/.venv/bin/python' and child.exe()==str(Path(root+'/.venv/bin/python').resolve()),
   'rootMatches':env.get('PYTHONPATH','').split(os.pathsep)[0]==root,
   'guardInstalled':marker['pid']==child.pid and abs(marker['create_time']-child.create_time())<0.001 and all(marker[k] is True for k in ('sync','async','socket','dns','updateAvailability')),
   'homeMatches':env.get('HOME')==str(Path(home).parent/'user-home') and env.get('HERMES_HOME')==home and marker['bootstrapHome']==home})
 except psutil.NoSuchProcess: continue
print(json.dumps(rows))
`, String(app.process().pid), root, sandbox.hermesHome, path.join(sandbox.root, 'evidence')], {
    cwd: root, encoding: 'utf8',
    env: { PATH: process.env.PATH, HOME: path.join(sandbox.root, 'user-home'), HERMES_HOME: sandbox.hermesHome,
      PYTHONDONTWRITEBYTECODE: '1' },
  })) as unknown

  const receipt = {
    version: 1, scope: 'packaged-shell-exact-checkout-backend', source: { commit, tree, clean: true },
    ...runtime,
    bundle: { ...runtime.bundle, executableName },
    backend: { kind: 'controlled-checkout', root, python, liveGateways,
      cliSha256: hash(path.join(root, 'eidolon_cli/main.py')), gatewaySha256: hash(path.join(root, 'tui_gateway/server.py')) },
  }

  const metadata = JSON.parse(readFileSync(path.join(desktop, 'package.json'), 'utf8'))
  validateReceipt(receipt, { commit, tree, root, electron: metadata.build.electronVersion, arch: process.arch,
    mainSha256: hash(path.join(desktop, 'dist/electron-main.mjs')), rendererSha256: hash(path.join(desktop, 'dist/index.html')) })

  return receipt
}

import fs from 'node:fs'
import path from 'node:path'

import { checkArchiveUpdate, prepareArchiveUpdateWithConsent, preparedSourceBackend } from './archive-update-source'
import { buildDesktopBackendEnv } from './backend-env'
import { runBootstrap } from './bootstrap-runner'
import { hasRuntimeSource, runtimeCliModule } from './eidolon-runtime'
import { getVenvSitePackagesEntries } from './windows-eidolon-path'

/** Electron supplies only the dialog and progress boundaries. */
export function createArchiveUpdateCaller(options: {
  hermesHome: string
  runGit: Parameters<typeof checkArchiveUpdate>[0]['runGit']
  showMessageBox: (options: { type: 'question'; buttons: string[]; defaultId: number; cancelId: number; title: string; message: string; detail: string }) => Promise<{ response: number }>
  emitProgress: (event: { stage: string; message: string }) => void
}) {
  return {
    check: (archiveRoot: string, currentSha?: string) => checkArchiveUpdate({ runGit: options.runGit, archiveRoot, currentSha }),
    prepare: () => prepareArchiveUpdateWithConsent({
      hermesHome: options.hermesHome, runGit: options.runGit,
      confirm: async () => (await options.showMessageBox({
        type: 'question', buttons: ['Cancel', 'Prepare source update'], defaultId: 0, cancelId: 0,
        title: 'Prepare Eidolon Git updates',
        message: 'Convert this release installation to an updatable Eidolon source runtime?',
        detail: 'This downloads a complete Eidolon Git checkout and runs the existing installer prerequisite, Python environment and dependency stages. Missing system tools may be installed. Your conversations, settings and current source remain unchanged if preparation fails. Native desktop replacement still uses the platform update helper.'
      })).response === 1,
      prepare: root => runBootstrap({
        installStamp: null, activeRoot: root, sourceRepoRoot: root, hermesHome: options.hermesHome,
        stageNames: process.platform === 'win32' ? ['uv', 'python', 'venv', 'dependencies'] : ['prerequisites', 'venv', 'python-deps'],
        onEvent: event => options.emitProgress({ stage: 'preparing', message: event.line || event.error || event.type }),
        writeMarker: () => {}
      })
    })
  }
}

const IS_WINDOWS = process.platform === 'win32'
const fileExists = (file: string) => { try { return fs.statSync(file).isFile() } catch { return false } }
function getVenvPython(root: string) {
  return path.join(root, IS_WINDOWS ? 'Scripts/python.exe' : 'bin/python')
}
function venvRootForPython(python: string, root: string) {
  const parent = path.dirname(python)
  const binName = path.basename(parent).toLowerCase()

  if (binName !== 'bin' && binName !== 'scripts') {
    return null
  }

  const candidate = path.dirname(parent)
  const relative = path.relative(root, candidate)

  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) {
    return null
  }

  return candidate
}

function findPythonForRoot(root: string, findSystemPython: () => string | null) {
  const override = process.env.HERMES_DESKTOP_PYTHON

  if (override && fileExists(override)) {
    return override
  }

  const relativePaths = IS_WINDOWS
    ? [path.join('.venv', 'Scripts', 'python.exe'), path.join('venv', 'Scripts', 'python.exe')]
    : [path.join('.venv', 'bin', 'python'), path.join('venv', 'bin', 'python')]

  for (const relativePath of relativePaths) {
    const candidate = path.join(root, relativePath)

    if (fileExists(candidate)) {
      return candidate
    }
  }

  return findSystemPython()
}

export function createSourcePythonBackend(root: string, label: string, backendArgs: string[], context: { hermesHome: string; findSystemPython: () => string | null }, options: { bootstrap?: boolean } = {}) {
  const python = findPythonForRoot(root, context.findSystemPython)

  if (!python) {
    return null
  }

  // The venv whose interpreter we selected is the venv whose site-packages
  // belong on PYTHONPATH — findPythonForRoot may have picked `.venv` over
  // `venv`, and mixing the two crashes the backend on its first native
  // import (see venvRootForPython). Fall back to root/venv only for a
  // system python, where the historical layout is the best guess.
  const venvRoot = venvRootForPython(python, root) ?? path.join(root, 'venv')
  const venvPython = getVenvPython(venvRoot)
  const command = IS_WINDOWS && fileExists(venvPython) ? venvPython : python

  return {
    kind: 'python',
    label,
    command,
    args: ['-m', runtimeCliModule(root), ...backendArgs],
    env: buildDesktopBackendEnv({
      hermesHome: context.hermesHome,
      pythonPathEntries: [root, ...getVenvSitePackagesEntries(venvRoot)],
      venvRoot
    }),
    root,
    bootstrap: Boolean(options.bootstrap),
    shell: false
  }
}

/** Real source precedence and backend descriptor consumer, before legacy fallbacks. */
export function selectSourceBackend(options: {
  overrideRoot?: string; packaged: boolean; sourceRepoRoot: string; hermesHome: string
  backendArgs: string[]; findSystemPython: () => string | null
}) {
  const create = (root: string, name: string) => hasRuntimeSource(root)
    ? createSourcePythonBackend(root, `${name} source at ${root}`, options.backendArgs, options) : null
  if (options.overrideRoot) {
    const backend = create(path.resolve(options.overrideRoot), 'Eidolon')
    if (backend) {return backend}
  }
  if (!options.packaged) {return create(options.sourceRepoRoot, 'Eidolon')}
  return preparedSourceBackend(options.hermesHome, root => create(root, 'Eidolon'))
}

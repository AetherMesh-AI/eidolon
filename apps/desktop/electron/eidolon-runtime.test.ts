import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { expect, test } from 'vitest'

import { createSourcePythonBackend } from './archive-update-caller'
import { canImportHermesCli } from './backend-probes'
import { hasRuntimeSource, resolveManagedRuntimeRoot, runtimeCliModule, runtimeShimPath } from './eidolon-runtime'

function checkout(root: string, repository: string, namespace = 'hermes_cli') {
  fs.mkdirSync(path.join(root, namespace), { recursive: true })
  fs.writeFileSync(path.join(root, 'package.json'), JSON.stringify({ repository: { url: repository } }))
  fs.writeFileSync(path.join(root, namespace, 'main.py'), '')
  fs.writeFileSync(path.join(root, namespace, 'config.py'), '')
}

test('managed discovery preserves only an identified fork, and the new layout takes precedence', () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'eidolon-layout-'))
  const legacy = path.join(home, 'hermes-agent')
  const current = path.join(home, 'eidolon-agent')

  try {
    checkout(legacy, 'https://github.com/NousResearch/hermes-agent.git')
    expect(resolveManagedRuntimeRoot(home)).toBe(current)
    expect(hasRuntimeSource(legacy)).toBe(false)
    checkout(legacy, 'git+https://github.com/AetherMesh-AI/Eidolon.git')
    expect(resolveManagedRuntimeRoot(home)).toBe(legacy)
    expect(runtimeCliModule(legacy)).toBe('hermes_cli.main')
    expect(hasRuntimeSource(legacy)).toBe(true)
    checkout(current, 'https://github.com/AetherMesh-AI/Eidolon.git', 'eidolon_cli')
    expect(resolveManagedRuntimeRoot(home)).toBe(current)
    expect(runtimeCliModule(current)).toBe('eidolon_cli.main')
  } finally {
    fs.rmSync(home, { recursive: true, force: true })
  }
})

test('selected legacy fork uses the same namespace in its source descriptor and real import probe', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'eidolon-legacy-runtime-'))

  try {
    checkout(root, 'git@github.com:AetherMesh-AI/Eidolon.git')
    // Disposable dependency modules keep this import-path test independent of a user's Python environment.
    fs.writeFileSync(path.join(root, 'yaml.py'), '')
    fs.writeFileSync(path.join(root, 'dotenv.py'), '')
    const python = execFileSync(process.platform === 'win32' ? 'python' : 'python3', ['-c', 'import sys; print(sys.executable)'], { encoding: 'utf8' }).trim()
    const backend = createSourcePythonBackend(root, 'legacy fork', ['serve'], { hermesHome: root, findSystemPython: () => python })
    expect(backend?.args).toEqual(['-m', runtimeCliModule(root), 'serve'])
    expect(canImportHermesCli(python, { runtimeRoot: root, env: { PYTHONPATH: root } })).toBe(true)
    const bin = path.join(root, 'venv', process.platform === 'win32' ? 'Scripts' : 'bin')
    fs.mkdirSync(bin, { recursive: true })
    const oldShim = path.join(bin, process.platform === 'win32' ? 'hermes.exe' : 'hermes')
    fs.writeFileSync(oldShim, '')
    expect(runtimeShimPath(root)).toBe(oldShim)
    const newShim = path.join(bin, process.platform === 'win32' ? 'eidolon.exe' : 'eidolon')
    fs.writeFileSync(newShim, '')
    expect(runtimeShimPath(root)).toBe(newShim)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

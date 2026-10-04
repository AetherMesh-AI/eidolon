import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { afterEach, expect, test, vi } from 'vitest'

import { createSourcePythonBackend } from './archive-update-caller'

const roots: string[] = []
afterEach(() => {
  vi.unstubAllEnvs()

  for (const root of roots.splice(0)) {fs.rmSync(root, { recursive: true, force: true })}
})

function sourceFixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'eidolon-python-coherence-'))
  roots.push(root)
  vi.stubEnv('HERMES_DESKTOP_PYTHON', '')
  vi.stubEnv('PYTHONPATH', '')

  const venvs = ['.venv', 'venv'].map((name, index) => {
    const directory = path.join(root, name)
    const python = path.join(directory, process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
    const sitePackages = path.join(directory, process.platform === 'win32' ? 'Lib/site-packages' : `lib/python3.${12 - index}/site-packages`)
    fs.mkdirSync(path.dirname(python), { recursive: true })
    fs.mkdirSync(sitePackages, { recursive: true })
    fs.writeFileSync(python, '')
    fs.writeFileSync(path.join(directory, 'pyvenv.cfg'), `version_info = 3.${12 - index}.0\n`)

    return { python, sitePackages }
  })

  return { root, venvs }
}

test('a dual-venv checkout uses the selected interpreter and its own site-packages', () => {
  const { root, venvs: [preferred, sibling] } = sourceFixture()
  const systemPython = vi.fn(() => null)

  const backend = createSourcePythonBackend(root, 'Eidolon source', ['serve'], {
    hermesHome: path.join(root, 'home'), findSystemPython: systemPython
  })

  expect(backend?.command).toBe(preferred.python)
  expect(backend?.env.PYTHONPATH.split(path.delimiter)).toEqual([root, preferred.sitePackages])
  expect(backend?.env.PYTHONPATH).not.toContain(sibling.sitePackages)
  expect(backend?.args).toEqual(['-m', 'hermes_cli.main', 'serve'])
  expect(systemPython).not.toHaveBeenCalled()
})

test('an explicit in-checkout interpreter keeps its matching site-packages', () => {
  const { root, venvs: [preferred, selected] } = sourceFixture()
  vi.stubEnv('HERMES_DESKTOP_PYTHON', selected.python)

  const backend = createSourcePythonBackend(root, 'Eidolon source', ['serve'], {
    hermesHome: path.join(root, 'home'), findSystemPython: () => null
  })

  expect(backend?.command).toBe(selected.python)
  expect(backend?.env.PYTHONPATH.split(path.delimiter)).toEqual([root, selected.sitePackages])
  expect(backend?.env.PYTHONPATH).not.toContain(preferred.sitePackages)
})

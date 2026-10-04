import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { nativeImage } from 'electron'
import { afterEach, test, vi } from 'vitest'

import { appIconCandidates, decodingFileProbe, resolveAppIcon } from './app-icon'

// Mock only the native decoder boundary; filesystem checks and resolution stay real.
vi.mock('electron', () => ({
  nativeImage: { createFromPath: vi.fn(() => ({ isEmpty: () => true })) }
}))

afterEach(() => {
  vi.mocked(nativeImage.createFromPath).mockReset()
  vi.mocked(nativeImage.createFromPath).mockReturnValue({ isEmpty: () => true } as ReturnType<
    typeof nativeImage.createFromPath
  >)
})

// Regression: a packaged app.asar can contain a TRUNCATED apple-touch-icon.png
// (interrupted electron-builder run, partial copy). Electron's
// BrowserWindow({ icon }) / app.dock.setIcon() decode synchronously and THROW
// on undecodable bytes, which killed the main process inside createWindow()
// and took the app down mid-session. Icon resolution must fail soft: skip a
// candidate that exists but does not decode, exactly like a missing one.

test('resolveAppIcon skips an existing but undecodable candidate', () => {
  // First candidate "exists" (probe says true) but does not decode; second
  // decodes. The resolver must return the second, not the first.
  const probeCalls: string[] = []

  const probe = (p: string) => {
    probeCalls.push(p)

    return p !== '/packaged/app.asar/public/apple-touch-icon.png'
  }

  const picked = resolveAppIcon(
    ['/packaged/app.asar/public/apple-touch-icon.png', '/packaged/app.asar/dist/apple-touch-icon.png'],
    probe
  )

  assert.equal(picked, '/packaged/app.asar/dist/apple-touch-icon.png')
  assert.deepEqual(probeCalls, [
    '/packaged/app.asar/public/apple-touch-icon.png',
    '/packaged/app.asar/dist/apple-touch-icon.png'
  ])
})

test('resolveAppIcon returns undefined when every candidate fails the probe', () => {
  const picked = resolveAppIcon(['/a.png', '/b.ico'], () => false)
  assert.equal(picked, undefined)
})

test('resolveAppIcon returns the first candidate that passes the probe', () => {
  const picked = resolveAppIcon(['/a.png', '/b.png'], () => true)
  assert.equal(picked, '/a.png')
})

test('decodingFileProbe rejects a missing file', () => {
  const missing = path.join(os.tmpdir(), `hermes-icon-missing-${process.pid}.png`)
  assert.equal(decodingFileProbe(missing), false)
})

test('decodingFileProbe rejects an existing but empty (0-byte) file', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-icon-'))
  const empty = path.join(dir, 'apple-touch-icon.png')
  fs.writeFileSync(empty, Buffer.alloc(0))

  try {
    // The decoder double returns an empty image; existence alone is insufficient.
    assert.equal(decodingFileProbe(empty), false)
    assert.deepEqual(vi.mocked(nativeImage.createFromPath).mock.calls, [[empty]])
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('decodingFileProbe rejects a directory', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-icon-dir-'))

  try {
    assert.equal(decodingFileProbe(dir), false)
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('appIconCandidates keeps the documented precedence ladder', () => {
  const mac = appIconCandidates({
    isWindows: false,
    appRoot: '/Applications/Hermes.app/Contents/Resources',
    unpackedPathFor: p => `${p}.unpacked`
  })

  assert.deepEqual(mac, [
    path.join('/Applications/Hermes.app/Contents/Resources', 'public', 'icon.png'),
    path.join('/Applications/Hermes.app/Contents/Resources', 'dist', 'icon.png'),
    path.join('/Applications/Hermes.app/Contents/Resources.unpacked', 'dist', 'icon.png'),
    path.join('/Applications/Hermes.app/Contents/Resources', 'public', 'apple-touch-icon.png'),
    path.join('/Applications/Hermes.app/Contents/Resources', 'dist', 'apple-touch-icon.png'),
    path.join('/Applications/Hermes.app/Contents/Resources.unpacked', 'dist', 'apple-touch-icon.png')
  ])

  // Windows prepends the two full-bleed .ico rungs ahead of the PNG ladder.
  const win = appIconCandidates({
    isWindows: true,
    appRoot: 'C:\\app',
    resourcesPath: 'C:\\resources',
    unpackedPathFor: p => `${p}\\unpacked`
  })

  assert.deepEqual(win, [
    path.join('C:\\resources', 'icon.ico'),
    path.join('C:\\app', 'assets', 'icon.ico'),
    path.join('C:\\app', 'public', 'icon.png'),
    path.join('C:\\app', 'dist', 'icon.png'),
    path.join('C:\\app\\unpacked', 'dist', 'icon.png'),
    path.join('C:\\app', 'public', 'apple-touch-icon.png'),
    path.join('C:\\app', 'dist', 'apple-touch-icon.png'),
    path.join('C:\\app\\unpacked', 'dist', 'apple-touch-icon.png')
  ])
  assert.equal(win.length, 8)
  assert.equal(win.filter(c => c.endsWith('.ico')).length, 2)
  assert.equal(
    win[0],
    path.join('C:\\resources', 'icon.ico'),
    'resources/ icon.ico is the highest-precedence Windows rung'
  )
  assert.equal(
    win.filter(c => c.endsWith('apple-touch-icon.png')).length,
    3,
    'all three legacy PNG rungs remain after the preferred artwork'
  )
})

for (const isWindows of [false, true]) {
  test(`resolveAppIcon traverses every artwork and legacy fallback (Windows=${isWindows})`, () => {
    const root = '/fixture/app.asar'
    const expected = [
      ...(isWindows ? [path.join('/fixture/resources', 'icon.ico'), path.join(root, 'assets', 'icon.ico')] : []),
      path.join(root, 'public', 'icon.png'),
      path.join(root, 'dist', 'icon.png'),
      path.join(`${root}.unpacked`, 'dist', 'icon.png'),
      path.join(root, 'public', 'apple-touch-icon.png'),
      path.join(root, 'dist', 'apple-touch-icon.png'),
      path.join(`${root}.unpacked`, 'dist', 'apple-touch-icon.png')
    ]
    const candidates = appIconCandidates({
      isWindows,
      appRoot: root,
      resourcesPath: '/fixture/resources',
      unpackedPathFor: p => `${p}.unpacked`
    })

    // Earlier candidates fail; this rung and every later rung are usable.
    // Assert visits too: a missing rung or continuing after success must fail.
    for (let i = 0; i < expected.length; i++) {
      const calls: string[] = []
      const picked = resolveAppIcon(candidates, p => {
        calls.push(p)
        return expected.slice(i).includes(p)
      })
      assert.equal(picked, expected[i])
      assert.deepEqual(calls, expected.slice(0, i + 1))
    }
    const calls: string[] = []
    assert.equal(
      resolveAppIcon(candidates, p => {
        calls.push(p)
        return false
      }),
      undefined
    )
    assert.deepEqual(calls, expected)
  })
}

test('Windows without resourcesPath retains relative ICO then app assets priority', () => {
  const candidates = appIconCandidates({
    isWindows: true,
    appRoot: '/fixture/app',
    unpackedPathFor: p => `${p}.unpacked`
  })
  assert.deepEqual(candidates.slice(0, 2), ['icon.ico', path.join('/fixture/app', 'assets', 'icon.ico')])
  assert.equal(resolveAppIcon(candidates, () => true), 'icon.ico')
  assert.equal(resolveAppIcon(candidates, p => p !== 'icon.ico'), path.join('/fixture/app', 'assets', 'icon.ico'))
})

test('default probe skips missing, directory, throwing and empty images before legacy success', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-icon-fallback-'))
  const candidates = appIconCandidates({
    isWindows: true,
    appRoot: path.join(dir, 'app'),
    resourcesPath: path.join(dir, 'resources'),
    unpackedPathFor: p => `${p}.unpacked`
  })
  const missing = path.join(dir, 'resources', 'icon.ico')
  const directory = path.join(dir, 'app', 'assets', 'icon.ico')
  const throwing = path.join(dir, 'app', 'public', 'icon.png')
  const empty = path.join(dir, 'app', 'dist', 'icon.png')
  const legacy = path.join(dir, 'app', 'public', 'apple-touch-icon.png')
  fs.mkdirSync(directory, { recursive: true })
  for (const file of [throwing, empty, legacy]) {
    fs.mkdirSync(path.dirname(file), { recursive: true })
    fs.writeFileSync(file, 'decoder-boundary fixture, not native image bytes')
  }
  const decoder = vi.mocked(nativeImage.createFromPath)
  decoder.mockImplementation(p => {
    if (p === throwing) {throw new Error('decode failed')}
    return { isEmpty: () => p !== legacy } as ReturnType<typeof nativeImage.createFromPath>
  })
  try {
    assert.equal(decodingFileProbe(missing), false)
    assert.equal(decodingFileProbe(directory), false)
    assert.equal(decoder.mock.calls.length, 0)
    assert.equal(resolveAppIcon(candidates), legacy)
    assert.deepEqual(decoder.mock.calls, [[throwing], [empty], [legacy]])
    decoder.mockClear()
    fs.rmSync(legacy)
    assert.equal(resolveAppIcon(candidates), undefined)
    assert.deepEqual(decoder.mock.calls, [[throwing], [empty]])
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('macOS runtime Dock override prefers the same rounded artwork as the ICNS generator', () => {
  const root = '/fixture/app.asar'
  const candidates = appIconCandidates({
    isWindows: false,
    isMac: true,
    appRoot: root,
    unpackedPathFor: p => `${p}.unpacked`
  })
  const masked = path.join(root, 'assets', 'icon-macos.png')
  assert.equal(resolveAppIcon(candidates, () => true), masked)
  assert.equal(
    resolveAppIcon(candidates, candidate => candidate !== masked),
    path.join(`${root}.unpacked`, 'assets', 'icon-macos.png')
  )
  assert.ok(candidates.indexOf(masked) < candidates.indexOf(path.join(root, 'public', 'icon.png')))
})

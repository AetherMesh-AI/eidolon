import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { test } from 'node:test'

import config from '../docusaurus.config.ts'

test('documentation navigation belongs to Eidolon without upstream hosted search', () => {
  assert.equal(config.title, 'Eidolon')
  assert.equal(config.organizationName, 'AetherMesh-AI')
  assert.equal(config.themeConfig.navbar.title, config.title)
  assert.equal(config.themeConfig.algolia, undefined)
  assert.equal(config.url, process.env.EIDOLON_DOCS_URL || 'http://localhost:3000')
  const links = [...config.themeConfig.navbar.items, ...config.themeConfig.footer.links.flatMap(group => group.items)]
  assert.ok(links.some(link => link.href === 'https://github.com/AetherMesh-AI/eidolon/releases'))
  assert.ok(links.every(link => !link.href?.includes('nousresearch.com') && !link.href?.includes('NousResearch')))
  assert.match(config.themeConfig.footer.copyright, /Derived from Hermes Agent by Nous Research/)
})

test('a clean documentation prebuild uses local catalogs without any fetch', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'eidolon-docs-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  fs.mkdirSync(path.join(root, 'scripts'))
  fs.copyFileSync(new URL('./prebuild.mjs', import.meta.url), path.join(root, 'scripts/prebuild.mjs'))
  const guard = `let calls = 0; globalThis.fetch = async () => { calls++; throw new Error('No network in docs fixture'); }; process.on('beforeExit', () => { if (calls) process.exitCode = 89; });`
  const child = spawnSync(process.execPath, ['--import', `data:text/javascript,${encodeURIComponent(guard)}`, path.join(root, 'scripts/prebuild.mjs')], { encoding: 'utf8' })
  assert.ifError(child.error)
  assert.equal(child.status, 0, child.stdout + child.stderr)
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(root, 'static/api/skills.json'), 'utf8')), [])
})

/** First background tree warm-up preserves sessions without provisioning projects storage. */
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync } from 'node:fs'
import path from 'node:path'

import { buildAppEnv, type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

const root = path.resolve(import.meta.dirname, '../../..')
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
let fixture: MockBackendFixture | undefined

test.afterEach(async () => {
  if (fixture) {
    try {
      await fixture.page.screenshot({ path: test.info().outputPath('project-sidebar.png') })
    } finally {
      await fixture.cleanup()
      fixture = undefined
    }
  }
})

for (const existing of [false, true]) {
  test(`${existing ? 'existing project' : 'absent projects database'} retains the real session sidebar`, async () => {
    fixture = await setupMockBackend({
      isolateModelCatalog: true,
      extraConfig: 'updates:\n  check: false\nauxiliary:\n  title_generation:\n    enabled: false\n',
      beforeLaunch: async sandbox => {
        const repo = path.join(sandbox.root, 'session-repository')
        mkdirSync(repo)
        execFileSync('git', ['init', '--quiet', repo])
        const env = buildAppEnv(sandbox)
        delete env.EIDOLON_HOME
        execFileSync(python, ['-c', `
import sys
from pathlib import Path
from eidolon_state import SessionDB
home=Path(sys.argv[1]); repo=sys.argv[2]
db=SessionDB(db_path=home/'state.db')
try:
 db.create_session('sidebar-fixture',source='cli',cwd=repo)
 db.append_message(session_id='sidebar-fixture',role='user',content='Preserved synthetic conversation')
 db.set_session_title('sidebar-fixture','Retained sidebar conversation')
finally: db.close()
if sys.argv[3]=='true':
 from eidolon_cli import projects_db as pdb
 with pdb.connect_closing() as c:
  pdb.create_project(c,name='Existing review project',folders=[repo])
`, sandbox.hermesHome, repo, String(existing)], { cwd: root, env })
      }
    })
    const f = fixture
    const cdp = await f.page.context().newCDPSession(f.page)
    await cdp.send('Network.enable')
    const pending = new Set<unknown>()
    const results: Record<string, unknown>[] = []
    cdp.on('Network.webSocketFrameSent', event => {
      const frame = JSON.parse(event.response.payloadData)

      if (frame.method === 'projects.tree') {
        pending.add(frame.id)
      }
    })
    cdp.on('Network.webSocketFrameReceived', event => {
      const frame = JSON.parse(event.response.payloadData)

      if (pending.delete(frame.id)) {
        expect(frame.error).toBeUndefined()
        results.push(frame.result)
      }
    })
    await waitForAppReady(f)
    await f.page.locator('.eid-rail-history > summary').click()
    await expect(f.page.getByText('Retained sidebar conversation', { exact: true }).first()).toBeVisible()
    await expect.poll(() => results.length).toBeGreaterThan(0)
    expect(JSON.stringify(results)).toContain('sidebar-fixture')
    expect(existsSync(path.join(f.sandbox.hermesHome, 'projects.db'))).toBe(existing)

    if (existing) {
      expect(JSON.stringify(results)).toContain('Existing review project')
      await f.page.getByRole('button', { name: 'Filters', exact: true }).click()
      await f.page.getByRole('menuitem', { name: /^Grouping/ }).hover()
      await f.page.getByRole('menuitemradio', { name: 'Project', exact: true }).click()
      await expect(f.page.getByText('Existing review project', { exact: true }).first()).toBeVisible()
    }

    expect(f.mock.receivedPrompts).toEqual([])
    await test.info().attach('background-tree', { body: JSON.stringify(results), contentType: 'application/json' })
    await cdp.detach()
  })
}

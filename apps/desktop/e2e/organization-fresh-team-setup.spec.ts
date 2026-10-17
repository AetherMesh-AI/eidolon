/** Real isolated profile setup → reserved synthetic transport → reviewed durable team. */
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync
} from 'node:fs'
import { isIP } from 'node:net'
import path from 'node:path'

import type { Locator } from '@playwright/test'

import {
  buildAppEnv,
  createSandbox,
  launchDesktop,
  type MockBackendFixture,
  waitForAppReady,
  writeEnvFile,
  writeMockProviderConfig
} from './fixtures'
import { verifyKeyboardFocus } from './focus-visibility'
import { seedIsolatedModelCatalog } from './isolated-model-catalog'
import { startMockServer } from './mock-server'
import { switchPython } from './organization-switch-fixture'
import { expect, test } from './test'

const root = path.resolve(import.meta.dirname, '../../..')
type Receipt = { profile: string; organizationId: string; ownerId: string }
type Descriptor = { open: boolean; host: string; port: number; descriptor: Record<string, unknown> }
type Guard = Descriptor & Receipt & { home: string; pid: number; create_time: number }
type Inputs = { jobs: string; goals: string; namingStyle: string }
const models = ['synthetic-studio-one', 'synthetic-studio-two']
const keys = ['synthetic-fresh-team-key-one-never-real', 'synthetic-fresh-team-key-two-never-real']
const names = ['Isolated Studio One', 'Isolated Studio Two']

// launchDesktop loads this sandbox file with -r before the real main module.
// No first-window/page.route gap: each session is guarded before it can load
// renderer resources. The one source-declared cosmetic CSS URL is fulfilled
// locally; screenshots use its declared system-font fallbacks, without downloads.
const electronNetworkGuard = String.raw`
const { app, net } = require('electron')
const { appendFileSync } = require('node:fs')
const { join } = require('node:path')
const evidence = process.env.EIDOLON_FRESH_TEAM_GUARD_DIR
const fontUrl = 'https://fonts.googleapis.com/css2?family=Courier+Prime:wght@400;700&display=swap'
const installed = new WeakSet()
const record = (name, value) => appendFileSync(join(evidence, name + '-main.jsonl'), JSON.stringify(value) + '\n')
const local = url => ['127.0.0.1', '[::1]'].includes(new URL(url).hostname)
const deny = url => record('browser-attempts', { url, physicalNetworkSend: false })
function install(session) {
  if (installed.has(session)) return
  session.protocol.handle('https', request => {
    if (request.method === 'GET' && request.url === fontUrl) {
      record('browser-fonts', { url: request.url, physicalNetworkSend: false,
        fulfillment: 'empty-local-css', typography: 'source-declared system-font fallbacks' })
      return new Response('/* Synthetic fixture: use declared system-font fallbacks. */', {
        headers: { 'content-type': 'text/css' }
      })
    }
    if (local(request.url)) return net.fetch(request, { bypassCustomProtocolHandlers: true, redirect: 'error' })
    deny(request.url)
    return new Response('Fixture denied external request', { status: 403 })
  })
  session.webRequest.onBeforeRequest({ urls: ['http://*/*', 'https://*/*', 'ws://*/*', 'wss://*/*'] }, (details, callback) => {
    if (local(details.url) || (details.method === 'GET' && details.url === fontUrl)) {
      callback({})
      return
    }
    deny(details.url)
    callback({ cancel: true })
  })
  installed.add(session)
  record('browser-installed', { stage: 'session-created', httpsHandler: true, externalRequestGuard: true })
}
app.on('session-created', install)
app.on('web-contents-created', (_event, contents) => {
  record('browser-windows', { id: contents.id, guardedBeforeCreation: installed.has(contents.session) })
  if (!installed.has(contents.session)) throw new Error('Renderer created before external-request guard')
})
`

const teams = names.map((_, index) => ({
  executive: {
    name: `Ash ${index + 1}`,
    purpose: 'Coordinate synthetic studio work',
    responsibilities: ['Review outcomes']
  },
  members: [
    {
      id: `studio-lead-${index + 1}`,
      name: `Ember ${index + 1}`,
      role: 'Manager',
      manager_id: 'executive',
      team: 'studio',
      capabilities: ['request.plan', 'request.integrate'],
      purpose: 'Plan synthetic drafts',
      responsibilities: ['Coordinate drafts']
    },
    {
      id: `studio-writer-${index + 1}`,
      name: `Flint ${index + 1}`,
      role: 'Worker',
      manager_id: `studio-lead-${index + 1}`,
      team: 'studio',
      capabilities: ['work.draft'],
      purpose: 'Write synthetic drafts',
      responsibilities: ['Produce reviewed drafts']
    }
  ]
}))

type Snapshot = {
  metadata: { organization_id: string; owner_id: string; display_name: string }
  model: { provider: string; default: string }
  generations: Array<{
    idempotency_key: string
    organization_id: string
    status: string
    dispatched: number
    reserved_tokens: number
    result_json: string
  }>
  teams: Array<{ organization_id: string; result_json: string }>
  agents: Array<{ id: string; name: string }>
  counts: Record<string, number>
  budget: unknown
  operationTable: boolean
}

function snapshot(home: string): Snapshot {
  return JSON.parse(
    execFileSync(
      switchPython,
      [
        '-c',
        String.raw`
import json,sqlite3,sys,yaml
from contextlib import closing
from pathlib import Path
home=Path(sys.argv[1])
with closing(sqlite3.connect((home/'organization'/'state.db').as_uri()+'?mode=ro',uri=True)) as c:
 c.row_factory=sqlite3.Row
 tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
 budget=[dict(r) for r in c.execute('SELECT * FROM organization_owner_budget_policy')] if 'organization_owner_budget_policy' in tables else []
 print(json.dumps({'metadata':dict(c.execute('SELECT * FROM organization_metadata').fetchone()),
  'model':yaml.safe_load((home/'config.yaml').read_text()).get('model',{}),
  'generations':[dict(r) for r in c.execute('SELECT * FROM organization_team_generations')],
  'teams':[dict(r) for r in c.execute('SELECT * FROM organization_team_setup')],
  'agents':[dict(r) for r in c.execute('SELECT id,name FROM agents ORDER BY id')],
  'counts':{t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['objectives','requests','tasks','organization_model_calls','owner_chat_threads']},
  'budget':budget,'operationTable':'organization_team_generation_operations' in tables}))
`,
        home
      ],
      { cwd: root, encoding: 'utf8' }
    )
  )
}

async function capture(fixture: MockBackendFixture, dialog: Locator, target: Locator, label: string) {
  const geometry: object[] = []

  for (const [width, height, percent] of [
    [1280, 900, 100],
    [1280, 900, 200],
    [400, 820, 100]
  ]) {
    await fixture.app.evaluate(
      ({ BrowserWindow }, size) => {
        const window = BrowserWindow.getAllWindows()[0]
        window.unmaximize()
        window.setMinimumSize(320, 400)
        window.setContentSize(size.width, size.height)
      },
      { width, height }
    )
    await fixture.page.evaluate(
      value =>
        (
          window as unknown as { hermesDesktop: { zoom: { setPercent(value: number): Promise<unknown> } } }
        ).hermesDesktop.zoom.setPercent(value),
      percent
    )
    await expect
      .poll(() =>
        fixture.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].webContents.getZoomFactor())
      )
      .toBe(percent / 100)
    await dialog.locator('footer').getByRole('button').first().focus()
    let steps = 0

    do {
      await fixture.page.keyboard.press('Tab')
      steps++
    } while (!(await target.evaluate(element => element === document.activeElement)) && steps < 60)

    await expect(target).toBeFocused()
    await expect(target).toBeInViewport({ ratio: 1 })
    const focus = await verifyKeyboardFocus(fixture.page, target, `${label}-${width}-${percent}`)
    expect(await dialog.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
    await fixture.page.evaluate(async () => {
      await document.fonts.ready
      await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
    })

    const bytes = Buffer.from(
      await fixture.app.evaluate(async ({ BrowserWindow }) =>
        (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64')
      ),
      'base64'
    )

    await test.info().attach(`${label}-${width}-${percent}`, { contentType: 'image/png', body: bytes })
    geometry.push({
      label,
      width,
      height,
      percent,
      steps,
      focus,
      sha256: createHash('sha256').update(bytes).digest('hex'),
      captureMethod: 'electron.capturePage'
    })
  }

  await test.info().attach(`${label}-geometry`, { contentType: 'application/json', body: JSON.stringify(geometry) })
  await fixture.page.evaluate(() =>
    (
      window as unknown as { hermesDesktop: { zoom: { setPercent(value: number): Promise<unknown> } } }
    ).hermesDesktop.zoom.setPercent(100)
  )
  await fixture.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setContentSize(1280, 900))
}

test('two fresh isolated organizations configure their own models and explicitly review and create synthetic teams', async () => {
  test.setTimeout(300_000)
  const sandbox = createSandbox('fresh-team')
  let mock: Awaited<ReturnType<typeof startMockServer>> | undefined
  const observer = path.join(sandbox.root, 'python-guard')
  const evidence = path.join(sandbox.root, 'provider-evidence')

  const env = buildAppEnv(sandbox, {
    PYTHONPATH: observer,
    EIDOLON_FRESH_TEAM_GUARD_DIR: evidence,
    EIDOLON_FRESH_TEAM_ROOT_HOME: sandbox.hermesHome,
    PYTHONDONTWRITEBYTECODE: '1'
  })

  const permit = path.join(evidence, 'permit.json')
  let launch: Awaited<ReturnType<typeof launchDesktop>> | undefined
  const descriptors = new Map<string, Descriptor>()
  const receipts: Receipt[] = []
  const rpc: Array<{ method: string; params: Record<string, unknown>; profile?: string; result?: unknown }> = []
  const verified: object[] = []
  const snapshots: Snapshot[] = []
  let armed: { guard: Guard; observed: Descriptor; inputs: Inputs; index: number } | undefined

  const records = (prefix: string): Array<Record<string, unknown>> =>
    (existsSync(evidence) ? readdirSync(evidence) : [])
      .filter(name => name.startsWith(prefix))
      .flatMap(name =>
        readFileSync(path.join(evidence, name), 'utf8')
          .trim()
          .split('\n')
          .filter(Boolean)
          .map(line => JSON.parse(line))
      )

  const sends = () => records('sends-')
  const browserAttempts = () => records('browser-attempts-')

  const assertQuiet = (count: number) => {
    expect(sends()).toHaveLength(count)
    expect(records('attempts-')).toEqual([])
    expect(browserAttempts()).toEqual([])
    expect(mock?.receivedPrompts).toEqual([])
  }

  const revoke = (observed?: Descriptor) => {
    if (observed) {
      observed.open = false
    }

    if (armed?.observed === observed) {
      armed = undefined
      rmSync(permit, { force: true })
    }
  }

  const requireGuard = async (home: string, receipt?: Receipt, checkpoint = 'initial'): Promise<Guard> => {
    const profile = receipt?.profile ?? 'default'
    let accepted: Guard | undefined
    await expect
      .poll(() => {
        const observed = descriptors.get(profile)

        if (!observed?.open) {
          return false
        }

        const expected = {
          ...observed,
          home: realpathSync(home),
          profile,
          organizationId: receipt?.organizationId,
          ownerId: receipt?.ownerId
        }

        const matches = JSON.parse(
          execFileSync(
            switchPython,
            [
              '-c',
              String.raw`
import json,sys
from pathlib import Path
import sitecustomize as guard
expected=json.loads(sys.argv[1]);matches=[]
for entry in json.loads((guard.ROOT_HOME/'spawn-ledger.json').read_text()):
 if not isinstance(entry,dict) or type(entry.get('pid')) is not int or entry.get('home') != expected['home']:continue
 path=guard.GUARD_ROOT/('installed-'+str(entry['pid'])+'.json')
 if not path.exists():continue
 marker=json.loads(path.read_text())
 if marker.get('socket') is not True or marker.get('dns') is not True or marker.get('updateAvailability') is not True:continue
 if guard._fixture_identity_matches(marker,entry,guard._fixture_live_identity(entry['pid']),expected):
  matches.append({**expected,'pid':entry['pid'],'create_time':entry['create_time']})
print(json.dumps(matches))
`,
              JSON.stringify(expected)
            ],
            { cwd: root, env, encoding: 'utf8', timeout: 5_000 }
          )
        ) as Guard[]

        if (matches.length !== 1 || !observed.open || descriptors.get(profile) !== observed) {
          return false
        }

        accepted = matches[0]

        return true
      })
      .toBe(true)
    verified.push({ checkpoint, ...accepted })
    expect(records('attempts-')).toEqual([])

    return accepted!
  }

  try {
    mock = await startMockServer()
    mkdirSync(observer)
    mkdirSync(evidence)
    copyFileSync(
      path.join(import.meta.dirname, 'fresh-team-provider-guard.py'),
      path.join(observer, 'sitecustomize.py')
    )
    writeMockProviderConfig(sandbox.hermesHome, mock.url, undefined, seedIsolatedModelCatalog(sandbox.hermesHome))
    writeEnvFile(sandbox.hermesHome)
    const bootstrap = path.join(sandbox.root, 'electron-network-guard.cjs')
    writeFileSync(bootstrap, electronNetworkGuard)
    launch = await launchDesktop(env, path.relative(import.meta.dirname, bootstrap))
    const fixture: MockBackendFixture = { ...launch, sandbox, mock, mockUrl: mock.url, cleanup: async () => {} }
    const { page } = launch
    expect(records('browser-installed-').length).toBeGreaterThan(0)
    expect(records('browser-windows-').length).toBeGreaterThan(0)
    expect(records('browser-windows-').every(row => row.guardedBeforeCreation === true)).toBe(true)
    let descriptorSequence = 0
    const lastDescriptor = new Map<string, number>()
    await page.routeWebSocket(/.*/, socket => {
      const endpoint = new URL(socket.url())
      const host = endpoint.hostname.replace(/^\[|\]$/g, '')

      if (!isIP(host) || (host !== '127.0.0.1' && host !== '::1')) {
        throw new Error('Non-loopback fixture WebSocket')
      }

      const server = socket.connectToServer()
      let open = true
      let observed: Descriptor | undefined

      const pending = new Map<
        string | number,
        { method: string; params: Record<string, unknown>; sequence: number; record?: (typeof rpc)[number] }
      >()

      socket.onClose((code, reason) => {
        open = false
        revoke(observed)

        return server.close({ code, reason })
      })
      server.onClose((code, reason) => {
        open = false
        revoke(observed)

        return socket.close({ code, reason })
      })
      socket.onMessage(message => {
        const request = JSON.parse(message.toString()) as {
          id?: string | number
          method?: string
          params?: Record<string, unknown>
        }

        if (request.method && request.id !== undefined) {
          const record = request.method.startsWith('organization.')
            ? {
                method: request.method,
                params: request.params ?? {},
                profile: [...descriptors].find(([, value]) => value === observed)?.[0]
              }
            : undefined

          if (record) {
            rpc.push(record)
          }

          pending.set(request.id, {
            method: request.method,
            params: request.params ?? {},
            sequence: ++descriptorSequence,
            record
          })

          if (request.method === 'organization.generateTeam') {
            expect(armed, 'explicitly configured and live-verified before Generate').toBeDefined()
            expect(
              open &&
                armed!.observed === observed &&
                observed?.open &&
                descriptors.get(armed!.guard.profile) === observed
            ).toBe(true)
            expect(request.params).toMatchObject({
              expectedOrganizationId: armed!.guard.organizationId,
              ...armed!.inputs
            })
            writeFileSync(
              permit,
              JSON.stringify({
                ...armed!.guard,
                model: models[armed!.index],
                key: keys[armed!.index],
                inputs: armed!.inputs,
                team: teams[armed!.index],
                request: request.params
              })
            )
          }
        }

        server.send(message)
      })
      server.onMessage(message => {
        const response = JSON.parse(message.toString()) as { id?: string | number; result?: Record<string, unknown> }
        const request = response.id === undefined ? undefined : pending.get(response.id)

        if (response.id !== undefined) {
          pending.delete(response.id)
        }

        if (request?.record) {
          request.record.result = response.result
        }

        if (request?.method === 'organization.createWorkspace' && response.result) {
          receipts.push(response.result as unknown as Receipt)
        }

        if (request?.method === 'organization.descriptor') {
          const scope = response.result?.scope as { kind?: string; profile?: string } | undefined

          if (
            open &&
            scope?.kind === 'served-profile' &&
            scope.profile &&
            request.sequence > (lastDescriptor.get(scope.profile) ?? 0)
          ) {
            revoke(descriptors.get(scope.profile))
            observed = { open: true, host, port: Number(endpoint.port), descriptor: response.result! }
            descriptors.set(scope.profile, observed)
            lastDescriptor.set(scope.profile, request.sequence)
          }
        }

        socket.send(message)
      })
    })
    await page.reload()
    await waitForAppReady(fixture)
    await requireGuard(sandbox.hermesHome)
    const navigation = page.getByRole('complementary', { name: 'Eidolon navigation', exact: true })
    await navigation.getByRole('link', { name: 'Home', exact: true }).click()
    await page.getByRole('button', { name: 'Edit organization and owner names', exact: true }).click()
    const identity = page.getByRole('dialog', { name: 'Organization and owner', exact: true })
    await identity.getByLabel('Organization name', { exact: true }).fill('Original Synthetic Studio')
    await identity.getByLabel('Your display name', { exact: true }).fill('Original Synthetic Owner')
    await identity.getByRole('button', { name: 'Save names', exact: true }).click()
    await expect(identity).toHaveCount(0)
    const rootConfig = readFileSync(path.join(sandbox.hermesHome, 'config.yaml'))
    const rootEnv = readFileSync(path.join(sandbox.hermesHome, '.env'))
    const trigger = page.locator('.eid-workspace-header').getByRole('button', { name: / · Organizations$/ })
    const directory = page.getByRole('dialog', { name: 'Organizations', exact: true })

    for (let index = 0; index < names.length; index++) {
      armed = undefined
      rmSync(permit, { force: true })
      await trigger.click()
      await directory.getByRole('button', { name: 'Create organization', exact: true }).click()
      const creation = page.getByRole('dialog', { name: 'Create organization', exact: true })
      await creation.getByLabel('Organization name', { exact: true }).fill(names[index])
      await creation.getByLabel('Your display name', { exact: true }).fill(`Isolated Owner ${index + 1}`)
      await creation.getByRole('button', { name: 'Create organization', exact: true }).click()
      await expect(creation.getByRole('button', { name: 'Switch to this organization', exact: true })).toBeEnabled()
      expect(receipts).toHaveLength(index + 1)
      await creation.getByRole('button', { name: 'Switch to this organization', exact: true }).click()
      // Matches the product cold-boot budget in src/lib/with-timeout.ts. This
      // still requires the exact verified status, followed by the live guard.
      await expect(creation.getByText('Selected organization verified.', { exact: true })).toBeVisible({
        timeout: 45_000
      })
      const receipt = receipts[index]
      const home = path.join(sandbox.hermesHome, 'profiles', receipt.profile)
      const fresh = snapshot(home)
      expect(fresh.generations).toEqual([])
      expect(fresh.teams).toEqual([])
      expect(fresh.model.default).toBe('')
      expect(readFileSync(path.join(home, '.env'), 'utf8')).toBe('')
      expect(fresh.operationTable).toBe(false)
      assertQuiet(index)
      await creation.getByRole('button', { name: 'Set up OpenAI API key', exact: true }).click()
      await expect(page).toHaveURL(/tab=providers.*pview=keys/)
      await expect(trigger).toHaveAccessibleName(`${names[index]} · Organizations`)
      const input = page.getByPlaceholder(/^Paste OpenAI(?: API)? key$/)
      await expect(input).toBeVisible()
      await requireGuard(home, receipt, 'before-key-entry')
      await input.fill(keys[index])
      await requireGuard(home, receipt, 'before-key-save')
      await input.locator('..').getByRole('button', { name: 'Save', exact: true }).click()
      await expect.poll(() => readFileSync(path.join(home, '.env'), 'utf8')).toContain(keys[index])
      assertQuiet(index)
      await page.goBack()
      await navigation.getByRole('link', { name: 'Home', exact: true }).click()
      await page.locator('.eid-team-entry').getByRole('button', { name: 'Set up organization', exact: true }).click()
      const unconfigured = page.getByRole('dialog', { name: 'Build your starter team', exact: true })
      await unconfigured.getByLabel('Work to delegate', { exact: true }).fill('A valid synthetic work description')
      await unconfigured.getByLabel('What success looks like', { exact: true }).fill('A reviewed synthetic result')
      await expect(
        unconfigured.getByRole('button', { name: 'Generate team · Recommended', exact: true })
      ).toBeDisabled()
      expect(snapshot(home).generations).toEqual([])
      assertQuiet(index)
      await unconfigured.getByRole('button', { name: 'Cancel', exact: true }).click()
      await trigger.click()
      await directory.getByRole('button', { name: 'Set up OpenAI API model', exact: true }).click()
      const model = page.getByRole('dialog', { name: 'Set up OpenAI API model', exact: true })
      await model.getByLabel('OpenAI API model ID', { exact: true }).fill(models[index])
      await model.getByRole('button', { name: 'Save model', exact: true }).click()
      await expect(model.getByRole('status')).toHaveText('Model saved for this organization.')
      await model.getByRole('button', { name: 'Close', exact: true }).click()
      expect(snapshot(home).model).toMatchObject({ provider: 'openai-api', default: models[index] })
      assertQuiet(index)
      await navigation.getByRole('link', { name: 'Home', exact: true }).click()
      await page.locator('.eid-team-entry').getByRole('button', { name: 'Set up organization', exact: true }).click()
      const dialog = page.getByRole('dialog', { name: 'Build your starter team', exact: true })

      const inputs = {
        jobs: `Review café drafts for studio ${index + 1}. Keep source wording.`,
        goals: `Deliver reviewed drafts for studio ${index + 1}.`,
        namingStyle: `Use Ash ${index + 1}, Ember ${index + 1} and Flint ${index + 1}`
      }

      await dialog.getByLabel('Work to delegate', { exact: true }).fill(inputs.jobs)
      await dialog.getByLabel('What success looks like', { exact: true }).fill(inputs.goals)
      await dialog.getByLabel('Team naming style (optional)', { exact: true }).fill(inputs.namingStyle)
      await expect(dialog).toContainText('Setup model calls remaining: 3.')
      const generate = dialog.getByRole('button', { name: 'Generate team · Recommended', exact: true })
      await expect(generate).toBeEnabled()

      if (index === 0) {
        await capture(fixture, dialog, generate, 'fresh-team-original-inputs')
      }

      const guard = await requireGuard(home, receipt, 'before-generate')
      armed = { guard, observed: descriptors.get(receipt.profile)!, inputs, index }
      const before = snapshot(home)
      assertQuiet(index)
      await generate.click()
      await expect(dialog.getByLabel('Name', { exact: true }).first()).toHaveValue(teams[index].executive.name, {
        timeout: 60_000
      })
      assertQuiet(index + 1)
      expect(snapshot(home).teams).toEqual([])
      expect(snapshot(home).agents).toEqual(before.agents)
      // Back retains original text; repeating the same intent replays its
      // durable receipt, without another provider send or another charge.
      await dialog.getByRole('button', { name: 'Back', exact: true }).click()
      await expect(dialog.getByLabel('Work to delegate', { exact: true })).toHaveValue(inputs.jobs)
      await expect(dialog.getByLabel('What success looks like', { exact: true })).toHaveValue(inputs.goals)
      await expect(dialog.getByLabel('Team naming style (optional)', { exact: true })).toHaveValue(inputs.namingStyle)
      await generate.click()
      await expect(dialog.getByLabel('Name', { exact: true }).first()).toHaveValue(teams[index].executive.name)
      assertQuiet(index + 1)
      const editedName = `Reviewed Ash Studio ${index + 1}`
      await dialog.getByLabel('Name', { exact: true }).first().fill(editedName)
      await expect(dialog.getByRole('button', { name: 'Create team', exact: true })).toHaveCount(0)
      expect(snapshot(home).agents).toEqual(before.agents)
      expect(snapshot(home).teams).toEqual([])
      const review = dialog.getByRole('button', { name: 'Review changes', exact: true })

      if (index === 0) {
        await capture(fixture, dialog, review, 'fresh-team-edited-proposal')
      }

      await review.click()
      const create = dialog.getByRole('button', { name: 'Create team', exact: true })
      await expect(create).toBeEnabled()
      await expect(dialog).toContainText('Setup model calls remaining: 2.')
      expect(snapshot(home).teams).toEqual([])
      expect(snapshot(home).agents).toEqual(before.agents)

      if (index === 0) {
        await capture(fixture, dialog, create, 'fresh-team-reviewed-create')
      }

      await create.click()
      const saved = page.getByRole('dialog', { name: 'Team saved', exact: true })
      await expect(saved).toBeVisible()
      await expect(saved).toContainText('No work has started.')
      armed = undefined
      rmSync(permit, { force: true })
      const after = snapshot(home)
      expect(after.generations).toHaveLength(1)
      expect(after.generations[0]).toMatchObject({
        organization_id: receipt.organizationId,
        status: 'completed',
        dispatched: 1
      })
      expect(JSON.parse(after.generations[0].result_json).team.executive.name).toBe(teams[index].executive.name)
      expect(after.teams).toHaveLength(1)
      expect(after.teams[0].organization_id).toBe(receipt.organizationId)
      expect(JSON.parse(after.teams[0].result_json).team).toEqual({
        ...teams[index],
        executive: { ...teams[index].executive, name: editedName }
      })
      expect(after.agents.find(agent => agent.id === 'executive')?.name).toBe(editedName)
      expect(Object.values(after.counts)).toEqual([0, 0, 0, 0, 0])
      expect(after.budget).toEqual(before.budget)
      expect(after.operationTable).toBe(false)
      const sent = sends()[index]

      const accounting = sent.wireAccounting as {
        wireInputBound: number
        contextReserve: number
        outputLimit: number
        reservedTokens: number
      }

      expect(accounting.outputLimit).toBe(8000)
      expect(accounting.contextReserve).toBe(2048)
      expect(accounting.reservedTokens).toBe(
        accounting.wireInputBound + accounting.contextReserve + accounting.outputLimit
      )
      expect(accounting.reservedTokens).toBeLessThanOrEqual(24000)
      expect(after.generations[0].reserved_tokens).toBe(accounting.reservedTokens)
      expect(sent).toMatchObject({
        home: realpathSync(home),
        profile: receipt.profile,
        organizationId: receipt.organizationId,
        ownerId: receipt.ownerId,
        model: models[index],
        keySha256: createHash('sha256').update(keys[index]).digest('hex'),
        inputs,
        physicalNetworkSend: false
      })
      expect((sent.reservation as Record<string, unknown>).idempotency_key).toBe(after.generations[0].idempotency_key)

      const generated = rpc.filter(
        item =>
          item.method === 'organization.generateTeam' && item.params.expectedOrganizationId === receipt.organizationId
      )

      expect(generated).toHaveLength(2)
      expect(generated[0].params).toEqual(generated[1].params)

      const created = rpc.filter(
        item =>
          item.method === 'organization.createTeam' && item.params.expectedOrganizationId === receipt.organizationId
      )

      expect(created).toHaveLength(1)
      expect(created[0].params).toMatchObject({ confirmed: true, team: { executive: { name: editedName } } })

      const reviewed = rpc.filter(
        item =>
          item.method === 'organization.previewTeam' && item.params.expectedOrganizationId === receipt.organizationId
      )

      expect(reviewed).toHaveLength(1)
      expect(created[0].params.previewVersion).toBe((reviewed[0].result as Record<string, unknown>).previewVersion)
      expect(JSON.parse(after.teams[0].result_json).receipt).toEqual({
        idempotencyKey: created[0].params.idempotencyKey,
        previewVersion: created[0].params.previewVersion
      })
      snapshots.push(after)
      await saved.getByRole('button', { name: 'Cancel', exact: true }).click()
      assertQuiet(index + 1)
      expect(readFileSync(path.join(sandbox.hermesHome, 'config.yaml'))).toEqual(rootConfig)
      expect(readFileSync(path.join(sandbox.hermesHome, '.env'))).toEqual(rootEnv)

      if (index === 1) {
        expect(snapshot(path.join(sandbox.hermesHome, 'profiles', receipts[0].profile))).toEqual(snapshots[0])
      }
    }

    expect(receipts[0].profile).not.toBe(receipts[1].profile)
    expect(receipts[0].organizationId).not.toBe(receipts[1].organizationId)
    expect(receipts[0].ownerId).not.toBe(receipts[1].ownerId)

    for (const index of [0, 1]) {
      await trigger.click()
      await directory.getByRole('option').filter({ hasText: names[index] }).click()
      await directory.getByRole('button', { name: 'Use selected organization', exact: true }).click()
      await directory.getByRole('button', { name: 'Confirm organization selection', exact: true }).click()
      await expect(directory.getByRole('status')).toHaveText('Selected organization verified.', { timeout: 45_000 })
      await directory.getByRole('button', { name: 'Close', exact: true }).click()
      await page.reload()
      await waitForAppReady(fixture)
      await expect(trigger).toHaveAccessibleName(`${names[index]} · Organizations`)
      await requireGuard(
        path.join(sandbox.hermesHome, 'profiles', receipts[index].profile),
        receipts[index],
        'after-reload'
      )
      await navigation.getByRole('link', { name: 'Organization', exact: true }).click()
      await expect(page.getByText(`Reviewed Ash Studio ${index + 1}`, { exact: true }).first()).toBeVisible()
      await expect(page.getByText(`Reviewed Ash Studio ${2 - index}`, { exact: true })).toHaveCount(0)
      expect(snapshot(path.join(sandbox.hermesHome, 'profiles', receipts[index].profile))).toEqual(snapshots[index])
      assertQuiet(2)
    }

    expect(
      rpc.filter(item =>
        /improveTeamInput|applySetupBudget|previewSetupBudget|createObjective|submitRequest|ownerChat|dispatch/.test(
          item.method
        )
      )
    ).toEqual([])
    await test.info().attach('fresh-team-acceptance', {
      contentType: 'application/json',
      body: JSON.stringify({
        receipts,
        verified,
        sends: sends(),
        snapshots,
        rpc,
        externalAttempts: records('attempts-'),
        browserAttempts: browserAttempts(),
        localFontFulfillments: records('browser-fonts-'),
        backgroundAvailability: records('background-'),
        browserInstallation: records('browser-installed-'),
        browserWindows: records('browser-windows-'),
        realProviderCalls: 0,
        syntheticTransportCompletions: 2,
        chargedSetupSends: 2
      })
    })
  } finally {
    // Preserve attempts and admissions even when an assertion fails.
    try {
      await test.info().attach('fresh-team-provider-guard', {
        contentType: 'application/json',
        body: JSON.stringify({
          verified,
          receipts,
          rpc,
          descriptors: [...descriptors],
          sends: sends(),
          attempts: records('attempts-'),
          browserAttempts: browserAttempts(),
          localFontFulfillments: records('browser-fonts-'),
          backgroundAvailability: records('background-'),
          browserInstallation: records('browser-installed-'),
          browserWindows: records('browser-windows-')
        })
      })
    } finally {
      try {
        rmSync(permit, { force: true })
      } finally {
        try {
          await launch?.app.close()
        } finally {
          try {
            await mock?.close()
          } finally {
            sandbox.cleanup()
          }
        }
      }
    }
  }
})

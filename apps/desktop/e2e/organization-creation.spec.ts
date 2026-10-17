/** Real fresh-profile RPCs, strict markers and verified native switching. No provider calls. */
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdirSync, readdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

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
import { acknowledgeExpectedErrorBanner, expect, test } from './test'

// Keep native acceptance independent of the renderer dependency graph.
const copy = {
  title: 'Create organization',
  organizationName: 'Organization name',
  ownerName: 'Your display name',
  create: 'Create organization',
  switch: 'Switch to this organization',
  selected: 'Selected organization verified.',
  setupKeys: 'Set up OpenAI API key',
  setupModel: 'Set up OpenAI API model',
  modelId: 'OpenAI API model ID',
  saveModel: 'Save model',
  modelStatus: { saved: 'Model saved for this organization.' },
  errors: { unsupported: 'This runtime is not supported.', uncertain: 'The creation result is unknown.' }
}

// Loaded only by this native fixture's Python children, before any SDK client.
const providerSendGuard = String.raw`
import ipaddress,json,math,os
from pathlib import Path
import httpx,psutil
guard_root=Path(os.environ['EIDOLON_NATIVE_PROVIDER_GUARD_DIR'])
bootstrap_home=str(Path(os.environ['HERMES_HOME']).resolve())
attempts=guard_root/('attempts-'+str(os.getpid())+'.jsonl')
def _fixture_check_request(request):
 try:
  allowed=ipaddress.ip_address(request.url.host).is_loopback
 except ValueError:
  allowed=False
 if not allowed:
  with attempts.open('a') as record:
   record.write(json.dumps({'home':str(Path(os.environ['HERMES_HOME']).resolve()),'pid':os.getpid(),'host':request.url.host})+'\n')
  raise AssertionError('Fixture denied non-loopback HTTPX send')
original_sync=httpx.HTTPTransport.handle_request
original_async=httpx.AsyncHTTPTransport.handle_async_request
def guarded_sync(self,request):
 _fixture_check_request(request)
 return original_sync(self,request)
async def guarded_async(self,request):
 _fixture_check_request(request)
 return await original_async(self,request)
httpx.HTTPTransport.handle_request=guarded_sync
httpx.AsyncHTTPTransport.handle_async_request=guarded_async
# This runs before the CLI applies --profile. Serving identity is joined later
# to the post-bind ledger and the descriptor's actual open WebSocket endpoint.
marker=guard_root/('installed-'+str(os.getpid())+'.json')
pending=marker.with_suffix('.pending')
pending.write_text(json.dumps({
 'bootstrapHome':bootstrap_home,'pid':os.getpid(),
 'create_time':psutil.Process().create_time(),'sync':True,'async':True
}))
pending.replace(marker)

def _fixture_identity_matches(marker,entry,live,expected):
 try:
  host=expected['host'];port=expected['port'];profile=expected['profile']
  if expected['open'] is not True or not ipaddress.ip_address(host).is_loopback:
   return False
  if type(port) is not int or not 0 < port < 65536:
   return False
  descriptor=expected['descriptor']
  if descriptor.get('capabilityVersion') != 1 or descriptor.get('scope') != {'kind':'served-profile','profile':profile}:
   return False
  if expected.get('organizationId'):
   identity={'organizationId':expected['organizationId'],'ownerId':expected['ownerId']}
   isolation=descriptor.get('isolation',{})
   if descriptor.get('state') != 'ready' or isolation.get('profile') != profile:
    return False
   if any(isolation.get(key) != value for key,value in identity.items()):
    return False
   if isolation.get('isolationVersion') != 1 or isolation.get('credentialMode') != 'profile-local-openai-api' or isolation.get('executionMode') != 'tool-free-organization' or isolation.get('supportedProviders') != ['openai-api']:
    return False
   metadata=descriptor.get('metadata',{})
   if metadata.get('organizationId') != identity['organizationId'] or metadata.get('owner',{}).get('ownerId') != identity['ownerId']:
    return False
  pid=marker['pid'];started=marker['create_time']
  times=[started,entry.get('create_time'),live.get('create_time')]
  if type(pid) is not int or pid <= 0 or any(type(value) not in (int,float) or not math.isfinite(value) or value <= 0 for value in times):
   return False
  if marker.get('sync') is not True or marker.get('async') is not True:
   return False
  if entry.get('pid') != pid or live.get('pid') != pid or entry.get('create_time') != started or live.get('create_time') != started or live.get('alive') is not True:
   return False
  # ledger.profile is optional browser-route metadata, not --profile identity.
  if entry.get('purpose') != 'serve' or entry.get('home') != expected['home'] or entry.get('profile') not in ('',profile):
   return False
  return entry.get('host') == host and entry.get('port') == port and [host,port] in live.get('listeners',[])
 except (KeyError,TypeError,ValueError,AttributeError):
  return False

def _fixture_live_identity(pid):
 try:
  process=psutil.Process(pid)
  started=process.create_time()
  listeners=[[item.laddr.ip,item.laddr.port] for item in process.net_connections(kind='tcp') if item.status == psutil.CONN_LISTEN]
  alive=process.is_running() and psutil.Process(pid).create_time() == started
  return {'pid':pid,'create_time':started,'alive':alive,'listeners':listeners}
 except (psutil.Error,OSError,ValueError):
  return {}
`

// This regression has an independent socket backstop: a broken guard cannot
// turn the canonical-URL denial check into an actual external request.
const providerGuardRegression = String.raw`
import asyncio,copy,json,os,socket,sys
import httpx,sitecustomize
loop=asyncio.new_event_loop()
physical=[]
def deny_socket(*args,**kwargs):
 physical.append(True)
 raise AssertionError('Unexpected physical socket in guard regression')
socket.socket.connect=deny_socket
socket.socket.connect_ex=deny_socket
socket.getaddrinfo=deny_socket
urls=['https://api.openai.com/v1/chat/completions','https://example.invalid/','http://localhost/']
for url in urls:
 with httpx.HTTPTransport(trust_env=False) as transport:
  try:
   transport.handle_request(httpx.Request('POST',url))
  except AssertionError as error:
   assert str(error)=='Fixture denied non-loopback HTTPX send'
  else:
   raise AssertionError('Sync guard failed open')
async def check_async():
 for url in urls:
  async with httpx.AsyncHTTPTransport(trust_env=False) as transport:
   try:
    await transport.handle_async_request(httpx.Request('POST',url))
   except AssertionError as error:
    assert str(error)=='Fixture denied non-loopback HTTPX send'
   else:
    raise AssertionError('Async guard failed open')
loop.run_until_complete(check_async())
loop.close()
for url in ['http://127.0.0.1/','http://[::1]/']:
 sitecustomize._fixture_check_request(httpx.Request('GET',url))
assert physical==[]
assert len(sitecustomize.attempts.read_text().splitlines())==6
marker=json.loads((sitecustomize.guard_root/('installed-'+str(os.getpid())+'.json')).read_text())
assert marker['bootstrapHome']==sitecustomize.bootstrap_home and marker['pid']==os.getpid() and marker['sync'] and marker['async']
assert marker['create_time']==sitecustomize.psutil.Process().create_time()
expected=json.loads(sys.argv[1])
entry={'pid':marker['pid'],'create_time':marker['create_time'],'purpose':'serve','home':expected['home'],'profile':'','host':expected['host'],'port':expected['port']}
live={'pid':marker['pid'],'create_time':marker['create_time'],'alive':True,'listeners':[[expected['host'],expected['port']]]}
assert sitecustomize._fixture_identity_matches(marker,entry,live,expected)
cases=[('marker','create_time',None),('marker','create_time',float('nan')),('marker','create_time',0),('marker','async',False),('entry','pid',marker['pid']+1),('entry','create_time',marker['create_time']+1),('entry','create_time',None),('entry','home','/fixture'),('entry','profile','foreign'),('entry','host','127.0.0.2'),('entry','port',12346),('entry','purpose','gateway'),('live','create_time',marker['create_time']+1),('live','create_time',None),('live','alive',False),('live','listeners',[]),('expected','open',False),('expected','host','localhost'),('expected','profile','foreign'),('expected','organizationId','foreign'),('expected','ownerId','foreign')]
for target,key,value in cases:
 records=copy.deepcopy({'marker':marker,'entry':entry,'live':live,'expected':expected})
 records[target][key]=value
 assert not sitecustomize._fixture_identity_matches(**records),(target,key)
with socket.socket() as listener:
 listener.bind(('127.0.0.1',0));listener.listen()
 expected['port']=entry['port']=listener.getsockname()[1]
 assert sitecustomize._fixture_identity_matches(marker,entry,sitecustomize._fixture_live_identity(os.getpid()),expected)
assert not sitecustomize._fixture_identity_matches(marker,entry,sitecustomize._fixture_live_identity(os.getpid()),expected)
assert physical==[]
print(json.dumps({'syncDenied':3,'asyncDenied':3,'physicalSocketAttempts':0,'numericLoopbackAllowed':2,'identityRefusals':len(cases),'receiptMetadataCannotReplaceLiveDescriptor':True,'emptyDisplayRouteAcceptedWithExactServedHome':True,'liveListenerVerifiedAndRevoked':True,'installation':marker}))
`

type WorkspaceReceipt = { profile: string; organizationId: string; ownerId: string }
type ObservedDescriptor = { open: boolean; host: string; port: number; descriptor?: unknown }

function guardExpectation(observed: ObservedDescriptor, home: string, receipt?: WorkspaceReceipt) {
  const profile = receipt?.profile ?? 'default'

  return {
    ...observed,
    home,
    profile,
    organizationId: receipt?.organizationId,
    ownerId: receipt?.ownerId
  }
}

function guardRegressionExpected() {
  // The real creation result has a metadata-only descriptor. It must never
  // overwrite the live transport descriptor when projecting receipt identity.
  const receipt = {
    capabilityVersion: 1,
    state: 'created',
    idempotencyKey: 'fixture-operation',
    profile: 'fresh',
    organizationId: 'org',
    ownerId: 'owner',
    isolationVersion: 1,
    credentialMode: 'profile-local-openai-api',
    executionMode: 'tool-free-organization',
    descriptor: { organizationId: 'org', displayName: 'Fresh fixture', owner: { ownerId: 'owner' } },
    activated: false
  }

  return guardExpectation(
    {
      open: true,
      host: '127.0.0.1',
      port: 12345,
      descriptor: {
        capabilityVersion: 1,
        state: 'ready',
        scope: { kind: 'served-profile', profile: receipt.profile },
        metadata: receipt.descriptor,
        isolation: {
          organizationId: receipt.organizationId,
          ownerId: receipt.ownerId,
          profile: receipt.profile,
          isolationVersion: 1,
          credentialMode: 'profile-local-openai-api',
          executionMode: 'tool-free-organization',
          supportedProviders: ['openai-api']
        }
      }
    },
    '/fixture/profiles/fresh',
    receipt
  )
}

async function setupCreationFixture() {
  const sandbox = createSandbox('fresh-organization')
  const mock = await startMockServer()
  const observer = join(sandbox.root, 'python-provider-guard')
  const guardRoot = join(sandbox.root, 'provider-guard-evidence')
  const regressionRoot = join(sandbox.root, 'provider-guard-regression')
  const descriptors = new Map<string, ObservedDescriptor>()
  const verifiedGuards: object[] = []
  let launch: Awaited<ReturnType<typeof launchDesktop>> | undefined

  const cleanup = async () => {
    try {
      await launch?.app.close()
    } finally {
      await mock.close()
      sandbox.cleanup()
    }
  }

  try {
    for (const directory of [observer, guardRoot, regressionRoot]) {
      mkdirSync(directory)
    }

    writeFileSync(join(observer, 'sitecustomize.py'), providerSendGuard)
    writeMockProviderConfig(sandbox.hermesHome, mock.url, undefined, seedIsolatedModelCatalog(sandbox.hermesHome))
    writeEnvFile(sandbox.hermesHome)
    const env = buildAppEnv(sandbox, { PYTHONPATH: observer, EIDOLON_NATIVE_PROVIDER_GUARD_DIR: guardRoot })

    const regression = JSON.parse(
      execFileSync(switchPython, ['-c', providerGuardRegression, JSON.stringify(guardRegressionExpected())], {
        env: { ...env, EIDOLON_NATIVE_PROVIDER_GUARD_DIR: regressionRoot },
        encoding: 'utf8'
      })
    )

    await test
      .info()
      .attach('provider-guard-regression', { contentType: 'application/json', body: JSON.stringify(regression) })
    launch = await launchDesktop(env)
    const fixture: MockBackendFixture = { ...launch, mock, mockUrl: mock.url, sandbox, cleanup }

    const attempts = () =>
      readdirSync(guardRoot)
        .filter(name => name.startsWith('attempts-'))
        .flatMap(name =>
          readFileSync(join(guardRoot, name), 'utf8')
            .trim()
            .split('\n')
            .filter(Boolean)
            .map(line => JSON.parse(line))
        )

    const requireGuard = async (home: string, receipt?: WorkspaceReceipt, checkpoint = 'initial') => {
      const profile = receipt?.profile ?? 'default'
      let verified: object | undefined

      await expect
        .poll(() => {
          const observed = descriptors.get(profile)

          if (!observed?.open || !observed.descriptor) {
            return false
          }

          const expected = guardExpectation(observed, realpathSync(home), receipt)

          const matches = JSON.parse(
            execFileSync(
              switchPython,
              [
                '-c',
                String.raw`
import json,sys
from pathlib import Path
import sitecustomize
root=Path(sys.argv[1]);ledger=Path(sys.argv[2]);expected=json.loads(sys.argv[3]);matches=[]
entries=json.loads(ledger.read_text()) if ledger.exists() else []
for entry in entries:
 if not isinstance(entry,dict) or type(entry.get('pid')) is not int or entry.get('home') != expected['home']:
  continue
 path=root/('installed-'+str(entry['pid'])+'.json')
 if not path.exists():
  continue
 marker=json.loads(path.read_text());live=sitecustomize._fixture_live_identity(entry['pid'])
 if sitecustomize._fixture_identity_matches(marker,entry,live,expected):
  matches.append({'pid':entry['pid'],'create_time':entry['create_time'],'servedHome':entry['home'],'profile':expected['profile'],'host':entry['host'],'port':entry['port'],'descriptor':expected['descriptor'],'sync':marker['sync'],'async':marker['async'],'bootstrapHome':marker['bootstrapHome']})
print(json.dumps(matches))
`,
                guardRoot,
                join(sandbox.hermesHome, 'spawn-ledger.json'),
                JSON.stringify(expected)
              ],
              { env, encoding: 'utf8', timeout: 5_000 }
            )
          ) as object[]

          if (matches.length !== 1 || !observed.open || descriptors.get(profile) !== observed) {
            return false
          }

          verified = { checkpoint, ...matches[0] }

          return true
        })
        .toBe(true)
      verifiedGuards.push(verified!)
      expect(attempts()).toEqual([])
    }

    return { fixture, requireGuard, attempts, guardRoot, descriptors, verifiedGuards }
  } catch (error) {
    await cleanup()
    throw error
  }
}

/** Exercise native Tab scrolling before measuring/capturing the painted focus ring. */
async function captureDialogKeyboard(
  fixture: MockBackendFixture,
  dialog: Locator,
  prefix: string,
  points: Array<{ name: string; target: Locator; context?: Locator }>,
  assertDraft: () => Promise<void>
) {
  const evidence: object[] = []

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
    await fixture.page.evaluate(value => {
      const desktop = window as unknown as { hermesDesktop: { zoom: { setPercent(value: number): Promise<unknown> } } }

      return desktop.hermesDesktop.zoom.setPercent(value)
    }, percent)
    await expect
      .poll(() =>
        fixture.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].webContents.getZoomFactor())
      )
      .toBe(percent / 100)

    for (const { name, target, context } of [
      {
        name: 'close',
        target: dialog.locator('[data-slot=dialog-close-button]'),
        context: dialog.locator('[data-slot=dialog-title]')
      },
      ...points
    ]) {
      let steps = 0

      do {
        await fixture.page.keyboard.press('Tab')
        steps++
      } while (!(await target.evaluate(element => element === document.activeElement)) && steps < 40)

      await expect(target, 'reachable using only Tab from current dialog focus').toBeFocused()
      await expect(target).toBeInViewport({ ratio: 1 })

      const immediate = await target.evaluate(element => {
        const style = getComputedStyle(element)
        const rect = element.getBoundingClientRect()
        const outset = parseFloat(style.outlineWidth) + parseFloat(style.outlineOffset)

        const ring = {
          left: rect.left - outset,
          top: rect.top - outset,
          right: rect.right + outset,
          bottom: rect.bottom + outset
        }

        const clipping: string[] = []

        if (ring.left < 0 || ring.top < 0 || ring.right > innerWidth || ring.bottom > innerHeight) {
          clipping.push('viewport')
        }

        for (let parent = element.parentElement; parent; parent = parent.parentElement) {
          const css = getComputedStyle(parent),
            bounds = parent.getBoundingClientRect()

          const clips = (value: string) => ['auto', 'scroll', 'hidden', 'clip'].includes(value)

          if (
            (clips(css.overflowX) &&
              (ring.left < bounds.left + parent.clientLeft - 0.5 ||
                ring.right > bounds.left + parent.clientLeft + parent.clientWidth + 0.5)) ||
            (clips(css.overflowY) &&
              (ring.top < bounds.top + parent.clientTop - 0.5 ||
                ring.bottom > bounds.top + parent.clientTop + parent.clientHeight + 0.5))
          ) {
            clipping.push(parent.className)
          }
        }

        const shell = element.closest<HTMLElement>('[data-slot="dialog-content"]')!
        const shellStyle = getComputedStyle(shell)
        const shellRect = shell.getBoundingClientRect()

        return {
          shell: {
            transitionProperty: shellStyle.transitionProperty,
            transitionDuration: shellStyle.transitionDuration,
            animationName: shellStyle.animationName,
            animationDuration: shellStyle.animationDuration,
            rect: { left: shellRect.left, top: shellRect.top, right: shellRect.right, bottom: shellRect.bottom },
            transitions: shell
              .getAnimations()
              .filter(animation => animation instanceof CSSTransition)
              .map(animation => ({
                property: (animation as CSSTransition).transitionProperty,
                state: animation.playState
              }))
          },
          viewport: { width: innerWidth, height: innerHeight },
          ring,
          clipping,
          width: parseFloat(style.outlineWidth),
          offset: parseFloat(style.outlineOffset),
          visible: element.matches(':focus-visible')
        }
      })

      await test.info().attach(`${prefix}-${name}-${width}-${percent}-initial-tab`, {
        contentType: 'application/json',
        body: JSON.stringify({ steps, ...immediate })
      })
      expect(immediate.clipping, `${prefix}/${name}: original Tab focus has complete clearance`).toEqual([])
      expect(immediate.visible).toBe(true)
      expect(immediate.width).toBe(3)
      expect(immediate.offset).toBe(3)
      expect(immediate.shell.transitionProperty, 'viewport resizing must not interpolate dialog geometry').toBe(
        'color, background-color, border-color, box-shadow, opacity'
      )
      const focus = await verifyKeyboardFocus(fixture.page, target, `${prefix}-${name}-${width}-${percent}`)
      await assertDraft()

      if (context) {
        await expect(context).toBeInViewport({ ratio: 1 })
      }

      for (const box of [
        dialog,
        dialog.locator('.eid-organization-dialog-body'),
        dialog.locator('.eid-organization-dialog-scroll')
      ]) {
        expect(await box.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
      }

      let titleRects: Array<{ left: number; top: number; right: number; bottom: number }> = []

      if (name === 'close') {
        const box = await target.boundingBox()
        expect(box!.width).toBeGreaterThanOrEqual(36)
        expect(box!.height).toBeGreaterThanOrEqual(36)
        titleRects = await dialog.locator('[data-slot=dialog-title]').evaluate(element => {
          const range = document.createRange()
          range.selectNodeContents(element)

          return Array.from(range.getClientRects(), rect => ({
            left: rect.left,
            top: rect.top,
            right: rect.right,
            bottom: rect.bottom
          }))
        })
        expect(titleRects.length).toBeGreaterThan(0)
        expect(
          titleRects.filter(
            rect =>
              rect.left < focus.ring.right &&
              rect.right > focus.ring.left &&
              rect.top < focus.ring.bottom &&
              rect.bottom > focus.ring.top
          )
        ).toEqual([])
      }

      await fixture.page.evaluate(async () => {
        await document.fonts.ready
        await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
      })

      const native = await fixture.app.evaluate(({ BrowserWindow }) => {
        const window = BrowserWindow.getAllWindows()[0]

        return { ...window.getContentBounds(), factor: window.webContents.getZoomFactor() }
      })

      const renderer = await fixture.page.evaluate(() => ({
        width: innerWidth,
        height: innerHeight,
        ratio: devicePixelRatio
      }))

      expect(Math.abs(renderer.width - native.width / native.factor)).toBeLessThanOrEqual(1)
      expect(Math.abs(renderer.height - native.height / native.factor)).toBeLessThanOrEqual(1)

      const bytes = Buffer.from(
        await fixture.app.evaluate(async ({ BrowserWindow }) =>
          (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64')
        ),
        'base64'
      )

      expect([bytes.readUInt32BE(16), bytes.readUInt32BE(20)]).toEqual([
        Math.round(renderer.width * renderer.ratio),
        Math.round(renderer.height * renderer.ratio)
      ])
      const label = `${prefix}-${name}-${width}-${percent}`
      await test.info().attach(label, { contentType: 'image/png', body: bytes })
      evidence.push({
        label,
        native,
        renderer,
        steps,
        immediate,
        focus,
        titleRects,
        sha256: createHash('sha256').update(bytes).digest('hex'),
        captureMethod: 'electron.capturePage'
      })
    }
  }

  await test
    .info()
    .attach(`${prefix}-keyboard-geometry`, { contentType: 'application/json', body: JSON.stringify(evidence) })
  await fixture.page.evaluate(() => {
    const desktop = window as unknown as { hermesDesktop: { zoom: { setPercent(value: number): Promise<unknown> } } }

    return desktop.hermesDesktop.zoom.setPercent(100)
  })
  await fixture.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setContentSize(1280, 900))
}

test('creates two fresh organizations, reconciles a lost reply and keeps switching, setup and deep links owner-scoped', async () => {
  test.setTimeout(300_000)
  const { fixture, requireGuard, attempts, guardRoot, descriptors, verifiedGuards } = await setupCreationFixture()
  const { page, sandbox, mock } = fixture
  const creates: Record<string, unknown>[] = []
  const receipts: WorkspaceReceipt[] = []
  const methods: string[] = []
  let socketSequence = 0
  let descriptorSequence = 0
  const descriptorSequences = new Map<string, number>()
  const descriptorTransports = new WeakMap<ObservedDescriptor, {
    socketSequence: number
    descriptorSequence: number
    readConfig: () => { received: boolean; model?: unknown; error?: unknown }
  }>()
  let dropNext = false
  let unsupported = false
  const header = page.locator('.eid-workspace-header')
  const trigger = header.getByRole('button', { name: / · Organizations$/ })
  const directory = page.getByRole('dialog', { name: 'Organizations', exact: true })
  const creation = page.getByRole('dialog', { name: copy.title, exact: true })
  const names = ['Fresh Studio One', 'Fresh Studio Two']

  const openCreation = async () => {
    await trigger.click()
    await directory.getByRole('button', { name: copy.title, exact: true }).click()
    await expect(creation).toBeVisible()
  }

  const choose = async (name: string) => {
    await trigger.click()
    await directory.getByRole('option').filter({ hasText: name }).click()
    await directory.getByRole('button', { name: 'Use selected organization', exact: true }).click()
    await directory.getByRole('button', { name: 'Confirm organization selection', exact: true }).click()
    await expect(directory.getByRole('status')).toHaveText('Selected organization verified.')
    await directory.getByRole('button', { name: 'Close', exact: true }).click()
    await expect(trigger).toHaveAccessibleName(`${name} · Organizations`)
  }

  const stored = (profile: string) =>
    JSON.parse(
      execFileSync(
        switchPython,
        [
          '-c',
          `
import json,sqlite3,sys,yaml
from pathlib import Path
home=Path(sys.argv[1]); c=sqlite3.connect((home/'organization'/'state.db').as_uri()+'?mode=ro',uri=True); c.row_factory=sqlite3.Row
print(json.dumps({'metadata':dict(c.execute('select * from organization_metadata').fetchone()), 'config':yaml.safe_load((home/'config.yaml').read_text())}))
`,
          join(sandbox.hermesHome, 'profiles', profile)
        ],
        { encoding: 'utf8' }
      )
    ) as {
      metadata: { organization_id: string; owner_id: string; display_name: string }
      config: { model: { provider: string; default: string } }
    }

  try {
    await page.routeWebSocket(/.*/, socket => {
      const server = socket.connectToServer()
      const connectionSequence = ++socketSequence
      const writes = new Set<string | number>()
      const descriptorReads = new Map<string | number, number>()
      const diagnosticReads = new Map<string, {
        observed: ObservedDescriptor
        profile: string
        evidence: { received: boolean; model?: unknown; error?: unknown }
      }>()
      const endpoint = new URL(socket.url())
      let open = true
      let observed: ObservedDescriptor | undefined

      socket.onClose((code, reason) => {
        open = false

        if (observed) {
          observed.open = false
        }

        return server.close({ code, reason })
      })
      server.onClose((code, reason) => {
        open = false

        if (observed) {
          observed.open = false
        }

        return socket.close({ code, reason })
      })
      let dropped: string | number | undefined
      socket.onMessage(message => {
        const request = JSON.parse(message.toString()) as {
          id?: string | number
          method?: string
          params?: Record<string, unknown>
        }

        if (request.method) {
          methods.push(request.method)
        }

        if (request.method === 'organization.descriptor' && request.id !== undefined) {
          descriptorReads.set(request.id, ++descriptorSequence)
        }

        if (request.method === 'organization.workspaceCreation' && unsupported) {
          socket.send(
            JSON.stringify({
              jsonrpc: '2.0',
              id: request.id,
              error: { code: -32601, message: 'Synthetic older runtime' }
            })
          )

          return
        }

        if (request.method === 'organization.createWorkspace') {
          creates.push(request.params!)
          writes.add(request.id!)

          if (dropNext) {
            dropped = request.id
            dropNext = false
          }
        }

        server.send(message)
      })
      server.onMessage(message => {
        const response = JSON.parse(message.toString()) as {
          id?: string | number
          result?: WorkspaceReceipt & {
            scope?: { kind?: string; profile?: string }
            config?: { model?: { provider?: unknown; default?: unknown } }
          }
          error?: unknown
        }

        const diagnostic = typeof response.id === 'string' ? diagnosticReads.get(response.id) : undefined

        if (diagnostic) {
          diagnosticReads.delete(response.id as string)

          if (open && diagnostic.observed.open && descriptors.get(diagnostic.profile) === diagnostic.observed) {
            diagnostic.evidence.received = true
            diagnostic.evidence.error = response.error
            diagnostic.evidence.model = response.result?.config?.model && {
              provider: response.result.config.model.provider,
              default: response.result.config.model.default
            }
          }

          // This fixture-only read uses the existing socket; it is not app hydration.
          return
        }

        const sequence = response.id === undefined ? undefined : descriptorReads.get(response.id)

        if (response.id !== undefined && sequence !== undefined) {
          descriptorReads.delete(response.id)
          const profile = response.result?.scope?.profile

          if (
            open &&
            response.result?.scope?.kind === 'served-profile' &&
            profile &&
            sequence > (descriptorSequences.get(profile) ?? 0)
          ) {
            const previous = descriptors.get(profile)

            if (previous) {
              previous.open = false
            }

            observed = {
              open: true,
              host: endpoint.hostname.replace(/^\[|\]$/g, ''),
              port: Number(endpoint.port),
              descriptor: response.result
            }
            descriptorSequences.set(profile, sequence)
            descriptors.set(profile, observed)
            const captured = observed
            descriptorTransports.set(captured, {
              socketSequence: connectionSequence,
              descriptorSequence: sequence,
              readConfig: () => {
                expect(open && captured.open && descriptors.get(profile) === captured).toBe(true)
                const id = `fixture-restored-config-${connectionSequence}-${sequence}`
                const evidence: { received: boolean; model?: unknown; error?: unknown } = { received: false }
                diagnosticReads.set(id, { observed: captured, profile, evidence })
                server.send(JSON.stringify({ jsonrpc: '2.0', id, method: 'config.get', params: { key: 'full' } }))

                return evidence
              }
            })
          }
        }

        if (response.id !== undefined && writes.has(response.id) && response.result) {
          receipts.push(response.result)
          writes.delete(response.id)
        }

        if (dropped !== undefined && response.id === dropped) {
          dropped = undefined
          socket.send(
            JSON.stringify({
              jsonrpc: '2.0',
              id: response.id,
              error: { code: -32000, message: 'Synthetic reply loss after publication' }
            })
          )

          return
        }

        socket.send(message)
      })
    })
    await page.reload()
    await waitForAppReady(fixture)
    await requireGuard(sandbox.hermesHome)
    await page
      .getByRole('complementary', { name: 'Eidolon navigation', exact: true })
      .getByRole('link', { name: 'Home', exact: true })
      .click()
    await page.getByRole('button', { name: 'Edit organization and owner names', exact: true }).click()
    const editor = page.getByRole('dialog', { name: 'Organization and owner', exact: true })
    await editor.getByLabel('Organization name', { exact: true }).fill('Original Studio')
    await editor.getByLabel('Your display name', { exact: true }).fill('Original Owner')
    await editor.getByRole('button', { name: 'Save names', exact: true }).click()
    await expect(editor).toHaveCount(0)

    const rootConfig = readFileSync(join(sandbox.hermesHome, 'config.yaml'))
    const rootEnv = readFileSync(join(sandbox.hermesHome, '.env'))

    unsupported = true
    await openCreation()
    await expect(creation.getByRole('alert')).toContainText(copy.errors.unsupported)
    expect(creates).toHaveLength(0)
    const unsupportedMessage = await creation.getByRole('alert').textContent()
    await creation.getByRole('button', { name: 'Close', exact: true }).click()
    await acknowledgeExpectedErrorBanner(page, unsupportedMessage!)
    unsupported = false

    for (let index = 0; index < names.length; index++) {
      await openCreation()
      await creation.getByLabel(copy.organizationName, { exact: true }).fill(names[index])
      await creation.getByLabel(copy.ownerName, { exact: true }).fill(`Fresh Owner ${index + 1}`)

      if (index === 0) {
        await captureDialogKeyboard(
          fixture,
          creation,
          'organization-create',
          [
            {
              name: 'organization-name',
              target: creation.getByLabel(copy.organizationName, { exact: true }),
              context: creation.locator('label').filter({ hasText: copy.organizationName })
            },
            {
              name: 'owner-name',
              target: creation.getByLabel(copy.ownerName, { exact: true }),
              context: creation.locator('label').filter({ hasText: copy.ownerName })
            },
            { name: 'create', target: creation.getByRole('button', { name: copy.create, exact: true }) }
          ],
          async () => {
            await expect(creation.getByLabel(copy.organizationName, { exact: true })).toHaveValue(names[index])
            await expect(creation.getByLabel(copy.ownerName, { exact: true })).toHaveValue(`Fresh Owner ${index + 1}`)
            expect(creates).toHaveLength(0)
          }
        )
      }

      dropNext = index === 0
      await creation.getByRole('button', { name: copy.create, exact: true }).click()

      if (index === 0) {
        await expect(creation.getByRole('alert')).toContainText(copy.errors.uncertain)
        const uncertainMessage = await creation.getByRole('alert').textContent()
        await creation.getByRole('button', { name: 'Close', exact: true }).click()
        await acknowledgeExpectedErrorBanner(page, uncertainMessage!)
        await page.reload()
        await waitForAppReady(fixture)
        await openCreation()
      }

      await expect(creation.getByRole('button', { name: copy.switch, exact: true })).toBeEnabled()
      expect(creates).toHaveLength(index + 1)
      await expect(
        header.getByRole('button', {
          name: `${index === 0 ? 'Original Studio' : names[0]} · Organizations`,
          includeHidden: true
        })
      ).toBeVisible()
      await creation.getByRole('button', { name: copy.switch, exact: true }).click()
      await expect(creation.getByText(copy.selected, { exact: true })).toBeVisible()
      // Deliberately take Keys first; durable model setup must remain reachable afterward.
      await creation.getByRole('button', { name: copy.setupKeys, exact: true }).click()
      await expect(page).toHaveURL(/tab=providers.*pview=keys/)
      await expect(trigger).toHaveAccessibleName(`${names[index]} · Organizations`)
      const home = join(sandbox.hermesHome, 'profiles', receipts[index].profile)
      const ownKey = `synthetic-workspace-key-${index + 1}-never-a-real-credential`
      expect(readFileSync(join(home, '.env'), 'utf8')).toBe('')

      const otherKeys = receipts
        .slice(0, index)
        .map(receipt => readFileSync(join(sandbox.hermesHome, 'profiles', receipt.profile, '.env')))

      const keyInput = page.getByPlaceholder(/^Paste OpenAI(?: API)? key$/)
      await expect(keyInput).toBeVisible()
      await requireGuard(home, receipts[index], 'before-key-entry')
      await keyInput.fill(ownKey)
      await requireGuard(home, receipts[index], 'before-key-save')
      await keyInput.locator('..').getByRole('button', { name: 'Save', exact: true }).click()
      await expect.poll(() => readFileSync(join(home, '.env'), 'utf8')).toContain(ownKey)
      expect(attempts()).toEqual([])
      expect(readFileSync(join(sandbox.hermesHome, '.env'))).toEqual(rootEnv)
      expect(
        receipts
          .slice(0, index)
          .map(receipt => readFileSync(join(sandbox.hermesHome, 'profiles', receipt.profile, '.env')))
      ).toEqual(otherKeys)
      await page.goBack()
      await expect(trigger).toHaveAccessibleName(`${names[index]} · Organizations`)
      await trigger.click()
      await directory.getByRole('button', { name: copy.setupModel, exact: true }).click()
      const model = page.getByRole('dialog', { name: copy.setupModel, exact: true })
      await expect(model.getByLabel(copy.modelId)).toBeEnabled()
      const modelId = `explicit-fixture-model-${index + 1}`
      await model.getByLabel(copy.modelId).fill(modelId)

      if (index === 0) {
        await captureDialogKeyboard(
          fixture,
          model,
          'organization-model',
          [
            {
              name: 'model-id',
              target: model.getByLabel(copy.modelId),
              context: model.locator('label').filter({ hasText: copy.modelId })
            },
            { name: 'save', target: model.getByRole('button', { name: copy.saveModel, exact: true }) }
          ],
          async () => {
            await expect(model.getByLabel(copy.modelId)).toHaveValue(modelId)
            expect(stored(receipts[index].profile).config.model.default).toBe('')
          }
        )
      }

      expect(stored(receipts[index].profile).config.model.default).toBe('')
      await model.getByRole('button', { name: copy.saveModel, exact: true }).click()
      await expect(model.getByRole('status')).toHaveText(copy.modelStatus.saved)
      expect(attempts()).toEqual([])
      await model.getByRole('button', { name: 'Close', exact: true }).click()
      expect(readFileSync(join(home, '.env'), 'utf8')).toContain(ownKey)
      expect(JSON.parse(readFileSync(join(home, '.organization-isolation.json'), 'utf8'))).toMatchObject({
        profile: receipts[index].profile,
        organizationId: receipts[index].organizationId,
        ownerId: receipts[index].ownerId
      })
      expect(stored(receipts[index].profile)).toMatchObject({
        metadata: { display_name: names[index], organization_id: receipts[index].organizationId },
        config: { model: { provider: 'openai-api', default: modelId } }
      })
    }

    expect(receipts[0].profile).not.toBe(receipts[1].profile)
    expect(receipts[0].organizationId).not.toBe(receipts[1].organizationId)
    expect(receipts[0].ownerId).not.toBe(receipts[1].ownerId)
    const before = receipts.map(receipt => stored(receipt.profile))
    await choose(names[0])
    await choose('Original Studio')
    await choose(names[1])
    await page.evaluate(() => {
      location.hash = '/messages'
    })
    await expect(trigger).toHaveAccessibleName(`${names[1]} · Organizations`)
    const beforeReload = { socketSequence, descriptorSequence }
    await page.reload()
    await waitForAppReady(fixture, 60_000, '.eid-messages-view')
    await expect(page).toHaveURL(/#\/messages$/)
    await expect(page.locator('.eid-messages-view').getByRole('heading', { name: 'Messages', exact: true })).toBeVisible()
    await expect(page.locator('.eid-messages-view')).toHaveAttribute('data-member-selected', 'false')
    await expect(trigger).toHaveAccessibleName(`${names[1]} · Organizations`)
    await expect.poll(() => {
      const observed = descriptors.get(receipts[1].profile)
      const transport = observed && descriptorTransports.get(observed)

      return Boolean(observed?.open && transport &&
        transport.socketSequence > beforeReload.socketSequence &&
        transport.descriptorSequence > beforeReload.descriptorSequence)
    }, { timeout: 15_000 }).toBe(true)
    const restored = descriptors.get(receipts[1].profile)!
    const restoredTransport = descriptorTransports.get(restored)!
    expect(restoredTransport.socketSequence).toBeGreaterThan(beforeReload.socketSequence)
    expect(restoredTransport.descriptorSequence).toBeGreaterThan(beforeReload.descriptorSequence)
    expect(restored.descriptor).toMatchObject({
      capabilityVersion: 1,
      state: 'ready',
      scope: { kind: 'served-profile', profile: receipts[1].profile },
      metadata: {
        organizationId: receipts[1].organizationId,
        displayName: names[1],
        owner: { ownerId: receipts[1].ownerId }
      },
      isolation: {
        profile: receipts[1].profile,
        organizationId: receipts[1].organizationId,
        ownerId: receipts[1].ownerId,
        isolationVersion: 1,
        credentialMode: 'profile-local-openai-api',
        executionMode: 'tool-free-organization',
        supportedProviders: ['openai-api']
      }
    })
    // Config normally hydrates over REST. Read the saved model through this exact
    // post-reload socket, without resolving another endpoint or changing the UI.
    const restoredConfig = restoredTransport.readConfig()
    await expect.poll(() => restoredConfig.received, { timeout: 15_000 }).toBe(true)
    expect(restoredConfig.error).toBeUndefined()
    expect(restoredConfig.model).toEqual({ provider: 'openai-api', default: before[1].config.model.default })
    expect(restored.open && descriptors.get(receipts[1].profile) === restored).toBe(true)
    await requireGuard(join(sandbox.hermesHome, 'profiles', receipts[1].profile), receipts[1], 'after-reload')
    expect(restored.open && descriptors.get(receipts[1].profile) === restored).toBe(true)
    await expect(trigger).toHaveAccessibleName(`${names[1]} · Organizations`)
    await test.info().attach('fresh-organization-cold-restore', {
      contentType: 'application/json',
      body: JSON.stringify({
        beforeReload,
        socketSequence: restoredTransport.socketSequence,
        descriptorSequence: restoredTransport.descriptorSequence,
        profile: receipts[1].profile,
        organizationId: receipts[1].organizationId,
        ownerId: receipts[1].ownerId,
        host: restored.host,
        port: restored.port,
        selectedOrganization: await trigger.getAttribute('aria-label'),
        route: new URL(page.url()).hash,
        diagnostic: {
          kind: 'read-only-existing-socket-diagnostic',
          method: 'config.get',
          params: { key: 'full' },
          responseReceived: restoredConfig.received,
          model: restoredConfig.model
        },
        sameObservedSocketStillOpen: restored.open && descriptors.get(receipts[1].profile) === restored
      })
    })
    await page.goBack()
    await expect(trigger).toHaveAccessibleName(`${names[1]} · Organizations`)
    expect(receipts.map(receipt => stored(receipt.profile))).toEqual(before)
    expect(readFileSync(join(sandbox.hermesHome, 'config.yaml'))).toEqual(rootConfig)
    expect(readFileSync(join(sandbox.hermesHome, '.env'))).toEqual(rootEnv)
    expect(methods).not.toContain('profiles.create')
    expect(methods).not.toContain('model.save_key')
    expect(mock.receivedPrompts).toHaveLength(0)
    expect(attempts()).toEqual([])
    await test.info().attach('fresh-organization-isolation', {
      contentType: 'application/json',
      body: JSON.stringify({
        profiles: receipts.map(({ profile, organizationId, ownerId }) => ({ profile, organizationId, ownerId })),
        explicitCreates: creates.length,
        lostReplyRecoveredWithoutResend: true,
        keyFirstModelRecovery: true,
        explicitSyntheticKeySavedOnlyToOwnedProfile: true,
        originalDefaultReturnVerified: true,
        providerCalls: mock.receivedPrompts.length,
        blockedExternalHTTPXAttempts: attempts().length
      })
    })
    await test.info().attach('fresh-organization-switcher', { contentType: 'image/png', body: await page.screenshot() })
  } finally {
    try {
      await test.info().attach('provider-guard-records', {
        contentType: 'application/json',
        body: JSON.stringify({
          installed: readdirSync(guardRoot)
            .filter(name => name.endsWith('.json'))
            .map(name => JSON.parse(readFileSync(join(guardRoot, name), 'utf8'))),
          attempts: attempts(),
          verified: verifiedGuards
        })
      })
    } finally {
      await fixture.cleanup()
    }
  }
})

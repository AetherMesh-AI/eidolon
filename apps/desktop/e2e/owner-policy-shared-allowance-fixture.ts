/** Real transport observation and read-only durable evidence; never synthetic RPC success. */
import { execFileSync } from 'node:child_process'
import path from 'node:path'

import type { Locator, Page } from '@playwright/test'

import { buildAppEnv, type Sandbox } from './fixtures'
import { metadataCopy } from './owner-policy-copy'
import { sharedPolicyCopy, type SharedPolicyLocale } from './owner-policy-shared-allowance-copy'
import { expect } from './test'

export interface AllowanceRpc {
  id?: string | number
  method?: string
  params?: Record<string, unknown>
  result?: Record<string, unknown>
  error?: { code: number; message: string; data?: { reason?: string } }
}
export interface AllowanceCandidate {
  profile: string
  organizationId: string
  ownerId: string
  displayName: string
}
export interface AllowanceDescriptor {
  authority: string
  scope: { profile: string }
  organizationId: string
  ownerId: string
  generation: number
  candidates: AllowanceCandidate[]
  configuration: { periodId: string; allocations: AllowanceCandidate[] } | null
  pool: { reserved: { modelCalls: number }; ordinaryRemaining: { modelCalls: number } } | null
  member: { reserved: { modelCalls: number }; ordinaryRemaining: { modelCalls: number } } | null
  capabilities: { configuration: boolean; observation: boolean }
}

export async function observeAllowanceRpc(page: Page) {
  const requests: Array<AllowanceRpc & { url: string }> = []
  const responses: Array<{ method: string; value: AllowanceRpc; url: string }> = []
  const dropped: AllowanceRpc[] = []
  let dropMethod: string | undefined

  await page.routeWebSocket(/.*/, socket => {
    const server = socket.connectToServer()
    const methods = new Map<string | number, string>()
    let droppedId: string | number | undefined
    socket.onMessage(message => {
      const request = JSON.parse(message.toString()) as AllowanceRpc
      if (request.id !== undefined && request.method) {methods.set(request.id, request.method)}
      if (request.method?.startsWith('organization.')) {requests.push({ ...request, url: socket.url() })}
      if (request.method === dropMethod) {dropMethod = undefined; droppedId = request.id}
      server.send(message)
    })
    server.onMessage(message => {
      const response = JSON.parse(message.toString()) as AllowanceRpc
      const method = response.id !== undefined ? methods.get(response.id) : undefined
      if (method?.startsWith('organization.')) {responses.push({ method, value: response, url: socket.url() })}
      if (response.id !== undefined && response.id === droppedId) {
        expect(response.error, 'lose only an actual successful server acknowledgement').toBeUndefined()
        dropped.push(response)
        return
      }
      socket.send(message)
    })
  })

  return {
    requests, responses, dropped,
    dropNextAcknowledgement: (method: string) => {dropMethod = method},
    descriptor: (profile = 'default') => responses
      .filter(row => row.method === 'organization.sharedAllowance' && (row.value.result?.scope as { profile?: string } | undefined)?.profile === profile)
      .at(-1)?.value.result as unknown as AllowanceDescriptor | undefined
  }
}

export interface SqliteEvidence {
  schema: Array<Record<string, unknown>>
  rows: Record<string, Array<Record<string, unknown>>>
}
export interface AllowanceLedgerEvidence {
  files: string[]
  shared: SqliteEvidence | null
  local: Record<string, SqliteEvidence | null>
}

/** Read-only application rows/schema; SQLite may still maintain its ordinary WAL read-lock bookkeeping. */
export function allowanceLedgers(sandbox: Sandbox, profiles = ['default']): AllowanceLedgerEvidence {
  const root = path.resolve(import.meta.dirname, '../../..')
  const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  return JSON.parse(execFileSync(python, ['-c', `
import json,sqlite3,sys
from pathlib import Path
root=Path(sys.argv[1])
profiles=json.loads(sys.argv[2])
def snapshot(p):
 if not p.exists(): return None
 with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as c:
  c.row_factory=sqlite3.Row
  c.execute('PRAGMA query_only=ON')
  c.execute('BEGIN')
  schema=[dict(r) for r in c.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
  tables=[r['name'] for r in schema if r['type']=='table' and not r['name'].startswith('sqlite_')]
  return {'schema':schema,'rows':{t:[dict(r) for r in c.execute('SELECT * FROM "'+t+'" ORDER BY rowid')] for t in tables}}
homes={profile:root if profile=='default' else root/'profiles'/profile for profile in profiles}
files=sorted(str(p.relative_to(root)) for home in homes.values() for p in (home/'organization').rglob('*') if p.is_file() and not p.name.endswith(('-wal','-shm')))
print(json.dumps({'files':files,'shared':snapshot(root/'organization'/'shared-allowance.db'),'local':{profile:snapshot(home/'organization'/'state.db') for profile,home in homes.items()}}))
`, sandbox.hermesHome, JSON.stringify(profiles)], { cwd: root, env: buildAppEnv(sandbox), encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 })) as AllowanceLedgerEvidence
}

export async function initializeAllowanceOrganization(page: Page, name: string, locale: SharedPolicyLocale = 'en') {
  const copy = metadataCopy[locale]
  await page.getByRole('button', { name: copy.edit, exact: true }).click()
  const names = page.getByRole('dialog', { name: copy.title, exact: true })
  await names.getByLabel(copy.organizationName, { exact: true }).fill(name)
  await names.getByLabel(copy.ownerName, { exact: true }).fill('Fixture Owner')
  await names.getByRole('button', { name: copy.save, exact: true }).click()
  await expect(names).toHaveCount(0)
  // This explicit existing runtime entry initializes execution, never an allowance read/preview.
  await page.evaluate(() => {location.hash = '#/organization'})
  await expect(page.locator('.eid-organization .eid-agent').first()).toBeVisible()
}

export async function openSharedAllowance(page: Page, locale: SharedPolicyLocale = 'en') {
  const copy = sharedPolicyCopy[locale]
  await page.evaluate(() => {location.hash = '#/settings?tab=organization'})
  const section = page.getByRole('region', { name: copy.title, exact: true })
  await expect(section).toBeVisible()
  return section
}

export async function fillAllowanceAmounts(group: Locator, calls: number, tokens: number, locale: SharedPolicyLocale = 'en') {
  const copy = sharedPolicyCopy[locale]
  await group.getByLabel(copy.calls, { exact: true }).fill(String(calls))
  await group.getByLabel(copy.tokens, { exact: true }).fill(String(tokens))
}

export async function fillSharedAllowance(
  dialog: Locator,
  candidates: AllowanceCandidate[],
  options: { periodId: string; poolCalls: number; poolProtected?: number; allocations: Record<string, number>; locale?: SharedPolicyLocale }
) {
  const locale = options.locale ?? 'en'
  const copy = sharedPolicyCopy[locale]
  await dialog.getByLabel(copy.label, { exact: true }).fill(options.periodId)
  await dialog.getByLabel(copy.startsAt, { exact: true }).fill(new Date(Date.now() - 60_000).toISOString())
  await dialog.getByLabel(copy.endsAt, { exact: true }).fill(new Date(Date.now() + 3600_000).toISOString())
  await fillAllowanceAmounts(dialog.getByRole('group', { name: copy.limits, exact: true }), options.poolCalls, 2_000_000, locale)
  await fillAllowanceAmounts(dialog.getByRole('group', { name: copy.protected, exact: true }).first(), options.poolProtected ?? 0, 0, locale)
  for (const candidate of candidates) {
    const calls = options.allocations[candidate.profile]
    const checkbox = dialog.getByRole('checkbox', { name: `${copy.enroll}: ${candidate.displayName} (${candidate.profile})`, exact: true })
    await checkbox.setChecked(calls !== undefined)
    if (calls === undefined) {continue}
    const allocation = dialog.locator('.eid-allowance-allocation').filter({ has: checkbox })
    await fillAllowanceAmounts(allocation.getByRole('group', { name: copy.allocation, exact: true }), calls, 500_000, locale)
    await fillAllowanceAmounts(allocation.getByRole('group', { name: copy.protected, exact: true }), 0, 0, locale)
  }
}

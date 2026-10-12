import { expect, it, vi } from 'vitest'

// This node-only test never launches Electron; avoid registering Playwright hooks.
vi.mock('./test', () => ({ installErrorBannerGuard: vi.fn() }))

import { createSandbox } from './fixtures'
import { startMockServer } from './mock-server'
import { startOrganizationSwitchGateway } from './organization-switch-fixture'

it.each([0, 1])('isolates remote update availability at %i through the real HTTP route', async behind => {
  const sandbox = createSandbox('backend-update-check')
  const mock = await startMockServer()
  let remote: Awaited<ReturnType<typeof startOrganizationSwitchGateway>> | undefined

  try {
    remote = await startOrganizationSwitchGateway(sandbox, mock.url, behind)
    const before = remote.updateChecks()

    const response = await fetch(`${remote.url}/api/eidolon/update/check?force=true`, {
      headers: { 'X-Hermes-Session-Token': remote.token }
    })

    expect(response.status).toBe(200)
    expect(await response.json()).toMatchObject({ behind, update_available: behind > 0, can_apply: true })
    expect(remote.updateChecks()).toBeGreaterThan(before)
    remote.setUpdateBehind(behind === 0 ? 1 : 0)

    const changed = await fetch(`${remote.url}/api/eidolon/update/check?force=true`, {
      headers: { 'X-Hermes-Session-Token': remote.token }
    })

    expect(changed.status).toBe(200)
    expect(await changed.json()).toMatchObject({ behind: behind === 0 ? 1 : 0, update_available: behind === 0, can_apply: true })
    expect(remote.starts()).toBe(0)
    expect(remote.updatePosts()).toBe(0)
    const denied = await fetch(`${remote.url}/api/eidolon/update/check?force=true`)
    expect([401, 403]).toContain(denied.status)
    // An unauthenticated action is refused, and proves the passive counter is live.
    const deniedAction = await fetch(`${remote.url}/api/eidolon/update`, { method: 'POST' })
    expect([401, 403]).toContain(deniedAction.status)
    expect(remote.updatePosts()).toBe(1)
  } finally {
    await remote?.close()
    await mock.close()
    sandbox.cleanup()
  }
}, 120_000)

import { describe, expect, it, vi } from 'vitest'

import { readPreparedValidationScope } from './runtime-prepared-validation-scope'
import { readOrganizationMetadataScope } from './use-organization-metadata'

const state = vi.hoisted(() => ({ request: vi.fn(async (_method: string, _params: Record<string, unknown>, _timeout: number) => ({})), replacement: vi.fn(async (_method: string, _params: Record<string, unknown>, _timeout: number) => ({})), changed: false }))
vi.mock('@/store/connections', () => ({ $pendingConnectionId: { get: () => null } }))
vi.mock('@/store/gateway', () => ({ $gateway: { get: () => ({ request: state.changed ? state.replacement : state.request }) }, $activeGatewayRoute: {}, $localOwnerProofRevision: {} }))
vi.mock('@/store/organization-observation-mode', () => ({ $organizationObservationEpoch: {}, $organizationObservationSwitching: {} }))
vi.mock('@/store/profile', () => ({ $activeGatewayProfile: { get: () => 'default' }, $gatewaySwapTarget: {} }))
vi.mock('@/store/session', () => ({ $connection: { get: () => ({}) }, $gatewayState: {} }))
vi.mock('./runtime-provider', () => ({ createOrganizationScopeReader: () => () => ({ key: 'socket', ownerKey: 'owner-route', connected: true }) }))

describe('prepared validation captured-socket timeout', () => {
  it('preserves the existing default timeout and uses the requested numeric override', async () => {
    state.changed = false; state.request.mockClear()
    await readOrganizationMetadataScope().request('organization.get', {})
    await readOrganizationMetadataScope(180000).request('organization.previewPreparedValidation', {})
    expect(state.request.mock.calls).toEqual([['organization.get', {}, 15000], ['organization.previewPreparedValidation', {}, 180000]])
  })
  it('extends only preview/save while read/revoke stay short and none retarget a replacement socket', async () => {
    state.changed = false; state.request.mockClear(); state.replacement.mockClear()
    const scope = readPreparedValidationScope()
    state.changed = true

    for (const method of ['preparedValidationSetup', 'previewPreparedValidation', 'savePreparedValidation', 'revokePreparedValidation']) {
      await scope.request(`organization.${method}`, { expectedOrganizationId: 'org' })
    }

    expect(state.request.mock.calls.map(call => [call[0], call[2]])).toEqual([
      ['organization.preparedValidationSetup', 15000], ['organization.previewPreparedValidation', 180000],
      ['organization.savePreparedValidation', 180000], ['organization.revokePreparedValidation', 15000]
    ])
    expect(state.replacement).not.toHaveBeenCalled()
  })
})

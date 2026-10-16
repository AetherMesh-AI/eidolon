import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { ownerPolicyCopy } from '@/i18n/organization-owner-policy'
import { $pendingConnectionId } from '@/store/connections'
import { $activeGatewayRoute, $gateway } from '@/store/gateway'
import { $organizationObservationEpoch, $organizationObservationSwitching } from '@/store/organization-observation-mode'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'
import { $connection, $gatewayState } from '@/store/session'
import { stubResizeObserver } from '@/test/jsdom'

import { createPreparedNativeModel, PREPARED_NATIVE_ORGANIZATION_ID, PREPARED_NATIVE_TITLE } from '../../../e2e/organization-prepared-validation-native-model'

import { createRuntimeAdapter } from './runtime-adapter'
import { RuntimeObjectiveDetail } from './runtime-detail'
import { createOrganizationScopeReader } from './runtime-provider'

// Keep the actual detail tree, metadata/policy hooks, validators and clients.
// Only the authenticated transport and its connection facts are synthetic.
afterEach(() => {
  cleanup()
  $gateway.set(null)
  $connection.set(null)
  $gatewayState.set('closed')
  vi.unstubAllGlobals()
})

it('reads configured policy through the real post-create detail path without preview, writes or provider calls', async () => {
  stubResizeObserver()
  const model = createPreparedNativeModel()

  const request = vi.fn(async (method: string, params: Record<string, unknown>, _timeoutMs: number, _signal?: AbortSignal) => {
    const response = model.reply(method, JSON.parse(JSON.stringify(params)))

    if (!response) { throw new Error(`Unmodeled transport method: ${method}`) }

    if ('error' in response) { throw Object.assign(new Error(response.error.message), response.error) }

    return response.result
  })

  $pendingConnectionId.set(null)
  $gatewaySwapTarget.set(null)
  $organizationObservationEpoch.set(0)
  $organizationObservationSwitching.set(false)
  $activeGatewayProfile.set('default')
  $activeGatewayRoute.set('default')
  $connection.set({ mode: 'local', profile: 'default', baseUrl: 'http://127.0.0.1:4321' } as NonNullable<ReturnType<typeof $connection.get>>)
  $gateway.set({ request } as unknown as NonNullable<ReturnType<typeof $gateway.get>>)
  $gatewayState.set('open')

  const adapter = createRuntimeAdapter({
    request<T>(method: string, params: Record<string, unknown>, timeoutMs: number, signal: AbortSignal) {
      return request(method, params, timeoutMs, signal) as Promise<T>
    },
    getScope: createOrganizationScopeReader(), subscribeScope: () => () => {} })

  const references = model.currentReferences()

  const objective = await adapter.createObjective(PREPARED_NATIVE_TITLE, {
    projectIds: ['frontend', 'backend'], executiveId: 'executive', managerId: 'manager',
    deliveryMode: 'managed_artifact', requiredChecks: ['project_typecheck', 'project_tests'], preparedValidations: references
  }, 'synthetic-renderer-create')

  const snapshot = adapter.getSnapshot()
  const readBefore = structuredClone(model.currentReferences())

  await act(async () => {
    render(<MemoryRouter><I18nProvider configClient={null} initialLocale="en">
      <RuntimeObjectiveDetail adapter={adapter} objective={objective} snapshot={snapshot} />
    </I18nProvider></MemoryRouter>)
  })
  expect(screen.getByRole('heading', { name: PREPARED_NATIVE_TITLE })).toBeTruthy()
  // A ready button proves that the real client accepted the entire configured
  // descriptor, including this objective's observation row. Never open it.
  await waitFor(() => expect(screen.getByRole('button', { name: ownerPolicyCopy.en.editObservation })).toHaveProperty('disabled', false))
  expect(request.mock.calls.filter(([method]) => method === 'organization.ownerPolicy')).toEqual([
    ['organization.ownerPolicy', { expectedOrganizationId: PREPARED_NATIVE_ORGANIZATION_ID }, 15000]
  ])
  expect(screen.queryByRole('alert')).toBeNull()
  const activity = within(screen.getByRole('region', { name: 'Recorded prepared validation activity' }))
  expect(activity.getByText(/Read-only scalar metadata/)).toBeTruthy()
  const current = activity.getByRole('list', { name: 'Current recorded generations' })

  for (const summary of current.querySelectorAll('summary')) { fireEvent.click(summary) }
  expect(within(current).getAllByText('Passed claim (unverified)')).toHaveLength(4)
  expect(activity.queryByText('Passed', { exact: true })).toBeNull()
  expect(model.unexpectedMethods).toEqual([])
  expect(new Set(request.mock.calls.map(([method]) => method))).toEqual(new Set([
    'organization.create', 'organization.descriptor', 'organization.ownerPolicy'
  ]))
  expect(model.creates).toHaveLength(1)
  expect(model.admittedCount).toBe(1)
  expect(model.currentReferences()).toEqual(readBefore)
  expect(adapter.getSnapshot()).toBe(snapshot)
  expect(snapshot.objectives[0].preparedValidations).toEqual(references)
})

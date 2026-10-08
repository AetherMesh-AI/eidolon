import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { Link, MemoryRouter, useLocation } from 'react-router'
import { expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { $activeGatewayProfile } from '@/store/profile'
import { $settingsScopeOverride, $settingsScopeProfile, setSettingsScope } from '@/store/settings-scope'

import { Organization } from './organization'
import { createRuntimeAdapter, type OrganizationGateway } from './runtime-adapter'
import { RuntimeSetup } from './runtime-setup'
import type { OrganizationAgent, OrganizationSnapshot } from './types'
import { OrganizationWorkspace } from './workspace'

function member(id: string, role: string, capabilities: string[], managerId?: string): OrganizationAgent {
  return {
    id,
    name: id,
    role,
    managerId,
    persistent: true,
    lifecycle: 'active',
    status: 'idle',
    capabilities,
    responsibilities: [],
    summary: '',
    provider: null,
    model: null
  }
}

function snapshot(): OrganizationSnapshot {
  return {
    source: 'runtime',
    objectives: [],
    tasks: [],
    requests: [],
    activity: [],
    knowledge: [],
    agents: [
      member('executive', 'Executive', ['request.accept']),
      member('manager', 'Manager', ['request.plan', 'request.integrate'], 'executive'),
      member('writer', 'Worker', ['work.draft'], 'manager')
    ],
    connection: { scope: 'socket', state: 'ready', ownerRoute: { connectionId: 'connection', profile: 'research' } },
    runtime: {
      state: 'ready',
      profile: 'default',
      maxWorkers: 2,
      capabilities: ['work.draft'],
      scope: '',
      readFileEnabled: false,
      projectExecutionEnabled: false,
      sourceIntegrationEnabled: false,
      availableProjects: [],
      setup: {
        version: 1,
        provider: { status: 'warning', blockers: ['codex_app_server'], inheritedMembers: 3, overriddenMembers: 0 },
        backgroundOptIn: true
      }
    }
  }
}

function Location() {
  const location = useLocation()

  return (
    <>
      <output aria-label="Current route">
        {location.pathname}
        {location.search}
      </output>
      <Link to="/home">Back home</Link>
    </>
  )
}

it('offers scoped setup navigation on Home and Organization without starting work, changing grants or selecting a model', async () => {
  $activeGatewayProfile.set('research')
  setSettingsScope('unrelated')
  const initial = snapshot()
  const before = JSON.stringify(initial)
  const request = vi.fn(async () => initial)

  const adapter = createRuntimeAdapter({
    request: request as OrganizationGateway['request'],
    getScope: () => ({ key: 'socket', connected: true, ownerRoute: initial.connection!.ownerRoute }),
    subscribeScope: () => () => undefined
  })

  const view = render(
    <MemoryRouter initialEntries={['/home']}>
      <OrganizationWorkspace adapter={adapter} />
      <Location />
    </MemoryRouter>
  )

  const panel = within(await screen.findByRole('region', { name: 'Organization setup' }))
  await panel.findByText('Configured transport needs review')
  expect(panel.getByText(/The effective provider route has not been resolved/)).toBeTruthy()
  expect(panel.getByText(/Authentication, model availability and execution have not been tested/)).toBeTruthy()
  fireEvent.click(panel.getByText('Review roster, repository and background configuration'))
  expect(panel.getByText('executive: Legacy manager planning')).toBeTruthy()
  expect(panel.getByText('Members using profile defaults').nextElementSibling?.textContent).toBe('3')
  expect(panel.getByText('Background gateway opt-in').nextElementSibling?.textContent).toBe('Configured')
  expect(panel.getByText(/Opt-in does not prove a gateway is running/)).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Create objective' })).toBeTruthy()
  expect($settingsScopeProfile.get()).toBe('unrelated')
  fireEvent.click(panel.getByRole('link', { name: 'Review profile model' }))
  expect(screen.getByRole('status', { name: 'Current route' }).textContent).toBe('/settings?tab=config:model')
  expect($settingsScopeProfile.get()).toBe('research')
  expect($settingsScopeOverride.get()).toBeNull()
  fireEvent.click(screen.getByRole('link', { name: 'Back home' }))
  setSettingsScope('unrelated')
  fireEvent.click(screen.getByRole('link', { name: 'Review provider accounts' }))
  expect(screen.getByRole('status', { name: 'Current route' }).textContent).toBe('/settings?tab=providers')
  expect($settingsScopeProfile.get()).toBe('research')
  fireEvent.click(screen.getByRole('link', { name: 'Back home' }))
  fireEvent.click(screen.getByRole('link', { name: 'Review organization roster' }))
  expect(screen.getByRole('heading', { name: 'Organization' })).toBeTruthy()
  expect(screen.getByRole('region', { name: 'Organization setup' })).toBeTruthy()
  await waitFor(() => expect(request).toHaveBeenCalledTimes(1))
  expect(JSON.stringify(initial)).toBe(before)
  view.unmount()
  $activeGatewayProfile.set('default')
  $settingsScopeOverride.set(null)
})

it('keeps unchecked, stale, legacy and retired configuration distinct and localizes setup guidance', () => {
  const initial = snapshot()
  initial.runtime!.setup!.provider = { status: 'unchecked', blockers: [], inheritedMembers: 3, overriddenMembers: 0 }
  initial.agents.push(
    { ...member('retired', 'Worker', []), lifecycle: 'retired' },
    { ...member('legacy', 'Worker', []), provider: undefined, model: undefined },
    member('apply', 'Worker', ['control.apply']),
    member('director', 'Manager', ['request.hire']),
    member('decomposer', 'Executive', ['request.decompose']),
    member('new-executive', 'Executive', ['request.decompose', 'request.accept'])
  )

  const draw = (state: OrganizationSnapshot, locale = 'en') => (
    <I18nProvider configClient={null} initialLocale={locale} key={locale}>
      <MemoryRouter>
        <RuntimeSetup snapshot={state} />
        <Organization snapshot={state} />
      </MemoryRouter>
    </I18nProvider>
  )

  const view = render(draw(initial))
  expect(screen.getByText('Provider execution not checked')).toBeTruthy()
  expect(screen.getByText('new-executive: Executive delegation')).toBeTruthy()
  expect(within(screen.getByRole('button', { name: 'Inspect decomposer' })).getByText('Provider: Uses profile default · Model: Uses profile default')).toBeTruthy()
  expect(
    within(screen.getByRole('button', { name: 'Inspect writer' })).getByText(
      'Provider: Uses profile default · Model: Uses profile default'
    )
  ).toBeTruthy()

  for (const id of ['retired', 'legacy', 'apply', 'director']) {
    expect(
      within(screen.getByRole('button', { name: `Inspect ${id}` })).getByText(
        'Provider: Not reported by runtime · Model: Not reported by runtime'
      )
    ).toBeTruthy()
  }

  for (const state of ['error', 'disconnected', 'connecting'] as const) {
    view.rerender(draw({ ...initial, connection: { ...initial.connection!, state } }))
    expect(screen.getByText(/Last received configuration/)).toBeTruthy()
    expect(screen.queryByRole('link', { name: 'Review profile model' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'Review provider accounts' })).toBeNull()
  }

  view.rerender(draw({ ...initial, runtime: { ...initial.runtime!, setup: undefined } }))
  expect(screen.getByText(/Setup guidance is not reported by this runtime/)).toBeTruthy()
  expect(screen.queryByText('Provider execution not checked')).toBeNull()
  expect(
    within(screen.getByRole('button', { name: 'Inspect writer' })).getByText(
      'Provider: Not reported by runtime · Model: Not reported by runtime'
    )
  ).toBeTruthy()
  view.rerender(draw({ ...initial, runtime: undefined, connection: { ...initial.connection!, state: 'connecting' } }))
  expect(screen.getByText('Connect to the organization runtime to load setup guidance.')).toBeTruthy()
  expect(screen.queryByText(/Last received configuration/)).toBeNull()
  view.rerender(draw(initial, 'ja'))
  expect(screen.getByRole('region', { name: '組織の設定' })).toBeTruthy()
  expect(screen.getByRole('link', { name: 'プロファイルのモデルを確認' })).toBeTruthy()
})

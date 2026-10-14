import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { modelRoutingCopy } from '@/i18n/organization-model-routing'

import { Organization } from './organization'
import { createRuntimeAdapter, type OrganizationGateway } from './runtime-adapter'
import { RuntimeExecutionAudit } from './runtime-execution-audit'
import { RuntimeModelDispatch } from './runtime-model-dispatch'
import { AgentModelAssignment, RuntimeModelRouting } from './runtime-model-routing'
import { modelDispatch, modelRoutingAudit, modelRoutingSnapshot } from './runtime-model-routing.test-support'

it('keeps current policy, legacy settings and immutable dispatches distinct without mutating or losing disclosure on polls', async () => {
  const snapshot = modelRoutingSnapshot()
  const read = vi.fn().mockResolvedValue(modelRoutingAudit())

  const adapter = createRuntimeAdapter({
    request: read as OrganizationGateway['request'], getScope: () => ({ key: 'owner-socket', connected: true }),
    subscribeScope: () => () => undefined
  })

  const view = render(<MemoryRouter><Organization adapter={adapter} snapshot={snapshot} /></MemoryRouter>)
  const summary = screen.getByText('Model routing')
  const details = summary.closest('details')!
  expect(details.open).toBe(false)
  fireEvent.click(summary)
  summary.focus()
  expect(screen.getByRole('region', { name: 'Configured routes' }).textContent).toContain('current-economy-model')
  expect(screen.getByText(/Configured routes do not prove availability/)).toBeTruthy()
  expect(screen.getByText(/Subscription quota is unknown/)).toBeTruthy()
  const configuration = screen.getByRole('region', { name: 'Owner configuration' })
  expect(configuration.textContent).toContain('--expected-generation 7')
  expect(configuration.textContent).toContain('HERMES_HOME')
  expect(within(configuration).queryByRole('button')).toBeNull()
  expect(read).not.toHaveBeenCalled()
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'auto' } })
  expect(screen.queryByRole('button', { name: 'Inspect Executive' })).toBeNull()
  expect(screen.getByRole('button', { name: 'Inspect Worker' })).toBeTruthy()
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '' } })

  view.rerender(<MemoryRouter><Organization adapter={adapter} snapshot={{ ...snapshot }} /></MemoryRouter>)
  expect(details.open).toBe(true)
  expect(globalThis.document.activeElement).toBe(summary)
  fireEvent.click(screen.getByRole('button', { name: 'Inspect Worker' }))
  const assigned = screen.getByRole('region', { name: 'Configured model assignment' })
  expect(within(assigned).getByText('Auto')).toBeTruthy()
  expect(within(assigned).queryByText('Owner worker policy')).toBeNull()
  expect(within(assigned).getByText('Assignment route IDs').nextElementSibling?.textContent).toBe('quality, economy')
  expect(screen.getByText('Legacy provider').nextElementSibling?.textContent).toBe('legacy-provider')
  expect(screen.getByText('Legacy model').nextElementSibling?.textContent).toBe('legacy-model')
  expect(within(assigned).queryByText('recorded-model')).toBeNull()
  const changed = modelRoutingSnapshot()
  changed.runtime!.modelRouting!.policyGeneration++
  changed.agents[1].modelAssignment = { mode: 'fixed', routeIds: ['quality'], minimumEvidence: 3, maxFallbacks: 0, label: 'Updated owner policy' }
  view.rerender(<MemoryRouter><Organization adapter={adapter} snapshot={changed} /></MemoryRouter>)
  expect(within(screen.getByRole('region', { name: 'Configured model assignment' })).getByText('Fixed')).toBeTruthy()
  expect(within(screen.getByRole('region', { name: 'Configured model assignment' })).getByText('Assignment route IDs').nextElementSibling?.textContent).toBe('quality')
  expect(screen.getByRole('region', { name: 'Owner configuration' }).textContent).toContain('--expected-generation 8')
  expect(read).not.toHaveBeenCalled()

  const nextOwner = modelRoutingSnapshot()
  nextOwner.connection = { scope: 'different-owner-socket', ownerScope: 'different-owner', state: 'ready' }
  nextOwner.agents = []
  delete nextOwner.runtime!.modelRouting
  view.rerender(<MemoryRouter><Organization adapter={adapter} snapshot={nextOwner} /></MemoryRouter>)
  expect(screen.queryByRole('region', { name: 'Configured model assignment' })).toBeNull()
  expect(screen.queryByText('Model routing')).toBeNull()
  view.unmount()

  const auditView = render(<RuntimeExecutionAudit adapter={adapter} request={snapshot.requests![0]} />)
  expect(read).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Inspect model and evidence audit' }))
  const dispatches = await screen.findByRole('region', { name: 'Recorded model dispatches' })
  const recorded = within(dispatches).getByRole('region', { name: 'Recorded model dispatch: economy-v1' })
  expect(recorded.textContent).toContain('recorded-model')
  expect(recorded.textContent).not.toContain('current-economy-model')
  expect(within(recorded).getByText('Reasoning effort').nextElementSibling?.textContent).toBe('low')
  expect(within(recorded).getByText('Policy generation').nextElementSibling?.textContent).toBe('6')
  expect(within(recorded).getByText('Captured input / output price (USD per million tokens)').nextElementSibling?.textContent).toBe('0.000000001 / 1.25')
  expect(recorded.textContent).toContain('cheapest-eligible-configured-ceiling')
  expect(screen.getByRole('region', { name: 'Model call reservations' }).textContent).toContain('Dispatch ID: dispatch-recorded')
  expect(read).toHaveBeenCalledTimes(1)
  auditView.unmount()
})

it('renders six localized read-only policy and dispatch views with stale state and no invented quota or price', () => {
  for (const locale of ['en', 'ja', 'zh', 'zh-hant', 'ar', 'ru'] as const) {
    const copy = modelRoutingCopy[locale]
    const snapshot = modelRoutingSnapshot()
    snapshot.connection!.state = 'disconnected'

    const view = render(<I18nProvider configClient={null} initialLocale={locale}>
      <RuntimeModelRouting snapshot={snapshot} />
      <AgentModelAssignment agent={snapshot.agents[0]} />
      <RuntimeModelDispatch dispatch={{ ...modelDispatch, mode: 'fixed', pricing: { inputUsdPerMillion: null, outputUsdPerMillion: null } }} />
    </I18nProvider>)

    expect(screen.getByText(copy.heading)).toBeTruthy()
    expect(screen.getByText(copy.stale)).toBeTruthy()
    expect(screen.getByText(copy.unpriced)).toBeTruthy()
    expect(screen.getByText(copy.executiveNote)).toBeTruthy()
    const recorded = screen.getByRole('region', { name: `${copy.dispatch}: ${modelDispatch.routeId}` })
    expect(within(recorded).getByText(copy.pricing).nextElementSibling?.textContent).not.toMatch(/0|NaN|undefined/)
    expect(within(recorded).getByText(copy.quotaNote)).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.queryByRole('textbox')).toBeNull()

    if (locale !== 'en') {
      for (const key of Object.keys(copy) as Array<keyof typeof copy>) {
        expect(copy[key]).toBeTruthy()
        expect(copy[key]).not.toBe(modelRoutingCopy.en[key])
      }
    }

    if (locale === 'ar') {expect(globalThis.document.documentElement.dir).toBe('rtl')}
    view.unmount()
  }
})

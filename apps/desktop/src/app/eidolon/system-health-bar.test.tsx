import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { stubResizeObserver } from '@/test/jsdom'

import { SystemHealthBar } from './system-health-bar'
import type { OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

const state = vi.hoisted(() => ({
  organization: null as { snapshot: OrganizationSnapshot; adapter: RuntimeOrganizationAdapter } | null
}))

vi.mock('./runtime-provider', () => ({ useRuntimeOrganization: () => state.organization }))
vi.mock('../contrib/organization-shell', () => ({ OrganizationWorkIndicator: () => <button>Scoped requests</button> }))
vi.mock('../contrib/panes', () => ({ useTitlebarToolContributions: () => [] }))
vi.mock('../shell/titlebar-controls', () => ({
  TitlebarControls: ({ inline }: { inline?: boolean }) => (
    <button>{inline ? 'Retained application controls' : 'Top controls'}</button>
  )
}))

beforeEach(stubResizeObserver)
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  state.organization = null
})

it('opens keyboard-accessible details with grants, settings and retained controls, then restores focus', async () => {
  state.organization = {
    adapter: { refresh: vi.fn() } as unknown as RuntimeOrganizationAdapter,
    snapshot: {
      source: 'runtime',
      objectives: [],
      requests: [],
      tasks: [],
      agents: [],
      knowledge: [],
      activity: [],
      connection: { scope: 'test', state: 'ready' },
      runtime: {
        state: 'ready',
        maxWorkers: 2,
        capabilities: ['work.edit'],
        scope: 'Managed workspace only.',
        supportsWorkspaceEdits: true,
        workspaceApplyEnabled: false
      }
    }
  }
  render(
    <MemoryRouter>
      <SystemHealthBar
        controls={<button>Retained gateway controls</button>}
        fresh
        gateway="open"
        readiness={{ ready: true, checksDisagree: false, source: 'runtime_check', reason: null }}
      />
    </MemoryRouter>
  )
  const bar = screen.getByRole('contentinfo', { name: 'System health' })
  const trigger = within(bar).getByRole('button', { name: 'System health: Unknown' })
  trigger.focus()
  fireEvent.click(trigger)
  const dialog = await screen.findByRole('dialog', { name: 'System health details' })
  expect(within(dialog).getByRole('link', { name: 'Gateway settings' }).getAttribute('href')).toBe(
    '/settings?tab=gateway'
  )
  fireEvent.click(within(dialog).getByText('Configured capabilities'))
  expect(within(dialog).getByText('No patch grant configured')).toBeTruthy()
  fireEvent.click(within(dialog).getByText('Advanced application controls'))
  expect(within(dialog).getByRole('button', { name: 'Retained application controls' })).toBeTruthy()
  expect(within(dialog).getByRole('button', { name: 'Retained gateway controls' })).toBeTruthy()
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  expect(screen.queryByRole('dialog')).toBeNull()
  await waitFor(() => expect(globalThis.document.activeElement).toBe(trigger))
  expect(state.organization.adapter.refresh).not.toHaveBeenCalled()
})

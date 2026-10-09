import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'

import { I18nProvider } from '@/i18n/context'

import { Organization } from './organization'
import { RuntimeCapabilities } from './runtime-capabilities'
import type { OrganizationSnapshot } from './types'

const snapshot: OrganizationSnapshot = {
  source: 'runtime',
  objectives: [],
  requests: [],
  tasks: [],
  activity: [],
  knowledge: [],
  agents: [
    {
      id: 'active',
      name: 'File Analyst',
      role: 'Employee',
      team: 'research',
      capabilities: ['work.inspect'],
      responsibilities: ['Inspect approved files'],
      requestTypes: ['work.inspect'],
      lifecycle: 'active',
      status: 'idle',
      tools: ['read_file'],
      provider: 'configured-provider',
      model: 'configured-model',
      summary: 'Configured file analysis.'
    },
    {
      id: 'available',
      name: 'Available Writer',
      role: 'Employee',
      team: 'research',
      capabilities: ['work.draft'],
      responsibilities: ['Draft from submitted text'],
      lifecycle: 'available',
      status: 'idle',
      tools: [],
      summary: 'Available configuration.'
    },
    {
      id: 'disabled',
      name: 'Disabled Writer',
      role: 'Employee',
      team: 'research',
      capabilities: ['work.draft'],
      responsibilities: [],
      lifecycle: 'disabled',
      status: 'offline',
      tools: [],
      summary: 'Disabled configuration.'
    },
    {
      id: 'retired',
      name: 'Former Writer',
      role: 'Employee',
      team: 'research',
      capabilities: [],
      responsibilities: [],
      lifecycle: 'retired',
      status: 'offline',
      tools: [],
      summary: 'Historical configuration.'
    }
  ],
  runtime: {
    state: 'ready',
    capabilities: ['work.inspect'],
    maxWorkers: 2,
    scope: 'Inspect explicitly granted local files.',
    readFileEnabled: true,
    readRoots: ['/approved/context'],
    maxToolCalls: 3
  }
}

describe('configured organization capabilities', () => {
  it('shows configured models, real tools and lifecycle separately from activity without enabling staff or grants', () => {
    render(
      <MemoryRouter>
        <Organization snapshot={snapshot} />
      </MemoryRouter>
    )
    const active = within(screen.getByRole('button', { name: 'Inspect File Analyst' }))
    expect(active.getByText('Lifecycle: Active')).toBeTruthy()
    expect(active.getByText('● idle')).toBeTruthy()
    expect(active.queryByText('configured-model')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Inspect File Analyst' }))
    const configured = within(screen.getByRole('complementary', { name: 'Agent details' }))
    expect(configured.getByText('read_file')).toBeTruthy()
    expect(configured.getByText('configured-provider')).toBeTruthy()
    expect(configured.getByText('configured-model')).toBeTruthy()
    fireEvent.keyDown(globalThis.document, { key: 'Escape' })
    expect(
      within(screen.getByRole('button', { name: 'Inspect Available Writer' })).getByText(
        'Lifecycle: Available · inactive'
      )
    ).toBeTruthy()
    expect(
      within(screen.getByRole('button', { name: 'Inspect Disabled Writer' })).getByText('Lifecycle: Disabled')
    ).toBeTruthy()
    expect(
      within(screen.getByRole('button', { name: 'Inspect Former Writer' })).getByText('Lifecycle: Retired')
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Available Writer' }))
    const inspector = within(screen.getByRole('complementary', { name: 'Agent details' }))
    expect(inspector.getByText('Text-only · no tools enabled')).toBeTruthy()
    expect(inspector.getByText('Available · inactive')).toBeTruthy()
    expect(inspector.queryByText('configured-model')).toBeNull()
    expect(screen.queryByRole('switch')).toBeNull()
    expect(screen.queryByRole('button', { name: /hire|activate|enable|grant/i })).toBeNull()
    fireEvent.keyDown(globalThis.document, { key: 'Escape' })
    fireEvent.change(screen.getByPlaceholderText('Name or responsibility…'), {
      target: { value: 'configured-provider' }
    })
    expect(screen.getByRole('button', { name: 'Inspect File Analyst' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Inspect Available Writer' })).toBeNull()
  })

  it('shows only explicit grants, keeps roots collapsed, and distinguishes missing grant data in the selected locale', () => {
    const runtime = snapshot.runtime!
    const { rerender } = render(<RuntimeCapabilities runtime={runtime} />)
    const grant = within(screen.getByRole('region', { name: 'Configured capabilities' }))
    expect(grant.getByText(runtime.capabilities[0])).toBeTruthy()
    expect(grant.getByText('Enabled for configured roots')).toBeTruthy()
    expect(grant.getByText('Tool-call limit per attempt')).toBeTruthy()
    const roots = grant.getByText('Granted read roots (1)').closest('details')!
    expect(roots.open).toBe(false)
    fireEvent.click(within(roots).getByText('Granted read roots (1)'))
    expect(roots.open).toBe(true)
    expect(within(roots).getByText('/approved/context')).toBeTruthy()
    rerender(<RuntimeCapabilities runtime={{ ...runtime, readFileEnabled: false }} />)
    expect(screen.getByText('Not enabled · submitted text only')).toBeTruthy()
    expect(screen.queryByText('/approved/context')).toBeNull()
    rerender(<RuntimeCapabilities runtime={{ ...runtime, readFileEnabled: undefined, readRoots: undefined }} />)
    expect(screen.getByText('Read-file grant not reported')).toBeTruthy()
    rerender(
      <I18nProvider configClient={null} initialLocale="ja">
        <RuntimeCapabilities runtime={runtime} />
      </I18nProvider>
    )
    expect(screen.getByRole('region', { name: '設定済みの機能' })).toBeTruthy()
    expect(screen.getByText('設定済みルート内で有効')).toBeTruthy()
  })
})

it('shows explicit project grants independently from host support, with exact recipes and no grant controls', () => {
  const runtime = { ...snapshot.runtime!, projectExecutionEnabled: true, sourceIntegrationEnabled: false,
    projectRecipes: [{ id: 'bounded-tests', root: 'root0', recipe: 'python_unittest', files: ['root0/app.py', 'root0/test_app.py'] }] }

  const view = render(<RuntimeCapabilities runtime={runtime} />)
  expect(screen.getByText('Project test grant').nextElementSibling?.textContent).toBe('Configured · availability checked at execution')
  expect(screen.getByText('Source branch integration grant').nextElementSibling?.textContent).toBe('Not enabled')
  expect(screen.getByText(/Linux with bubblewrap and libseccomp only/)).toBeTruthy()
  expect(screen.getByText(/macOS and Windows execution is unavailable/)).toBeTruthy()
  expect(screen.getByText(/No third-party dependencies, shell, network, subprocesses, threads or file creation/)).toBeTruthy()
  const recipes = screen.getByText('Explicit project recipes (1)').closest('details')!
  expect(recipes.open).toBe(false)
  fireEvent.click(within(recipes).getByText('Explicit project recipes (1)'))
  expect(recipes.open).toBe(true)
  expect(within(recipes).getByText('root0/test_app.py')).toBeTruthy()
  expect(within(recipes).getByText('python_unittest')).toBeTruthy()
  expect(screen.queryByRole('button', { name: /grant|enable|run/i })).toBeNull()
  view.rerender(<RuntimeCapabilities runtime={{ ...runtime, projectExecutionEnabled: false, sourceIntegrationEnabled: true, projectRecipes: [] }} />)
  expect(screen.getByText('Project test grant').nextElementSibling?.textContent).toBe('Not enabled')
  expect(screen.getByText('Source branch integration grant').nextElementSibling?.textContent).toBe('Configured · availability checked at execution')
  expect(screen.getByText('No exact project recipe configured.')).toBeTruthy()
  view.rerender(<RuntimeCapabilities runtime={snapshot.runtime!} />)
  expect(screen.queryByText('Project test grant')).toBeNull()
  expect(screen.queryByText('Supported project runner')).toBeNull()
})

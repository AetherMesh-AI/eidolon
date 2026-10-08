import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway } from './runtime-adapter'
import type { OrganizationSnapshot } from './types'
import { OrganizationWorkspace } from './workspace'

it('submits explicit acceptance, delivery and required verification while leaving grants to the runtime', async () => {
  const snapshot: OrganizationSnapshot = { source: 'runtime', objectives: [], agents: [], tasks: [], knowledge: [], activity: [], requests: [], runtime: { capabilities: ['work.edit'], scope: 'Managed edits', state: 'ready', maxWorkers: 2, supportsWorkspaceEdits: true, workspaceApplyEnabled: false } }
  const request = vi.fn().mockImplementation((method: string) => method === 'organization.create' ? Promise.reject(new Error('Test admission paused')) : Promise.resolve(snapshot))
  const adapter = createRuntimeAdapter({ request: request as OrganizationGateway['request'], getScope: () => ({ key: 'test', connected: true }), subscribeScope: () => () => undefined })
  render(<MemoryRouter initialEntries={['/home']}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
  await waitFor(() => expect(screen.getByRole('button', { name: 'Create objective' })).toHaveProperty('disabled', true))
  await waitFor(() => expect(adapter.getSnapshot().connection?.state).toBe('ready'))
  fireEvent.change(screen.getByRole('textbox', { name: 'Objective' }), { target: { value: 'Update the project' } })
  fireEvent.change(screen.getByRole('textbox', { name: 'Acceptance criteria' }), { target: { value: 'The result explains all changes\nKeep current behavior' } })
  fireEvent.change(screen.getByRole('combobox', { name: 'Delivery scope' }), { target: { value: 'managed_artifact' } })
  fireEvent.click(screen.getByRole('checkbox', { name: /Project tests/ }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'Priority' }))
  fireEvent.change(screen.getByRole('slider', { name: 'Priority level' }), { target: { value: 4 } })
  fireEvent.click(screen.getByRole('button', { name: 'Create objective' }))
  await screen.findByText('Test admission paused')
  expect(request.mock.calls.find(call => call[0] === 'organization.create')![1]).toMatchObject({ acceptanceCriteria: ['The result explains all changes', 'Keep current behavior'], deliveryMode: 'managed_artifact', requiredChecks: ['project_tests'], priority: 'P1' })
  expect(snapshot.runtime?.workspaceApplyEnabled).toBe(false)
  expect(screen.queryByText(/No external tools are executed|Files, websites and external tools are not accessed/)).toBeNull()
})

it('submits selected persistent leaders and clears an incompatible manager when the executive changes', async () => {
  const leader = (id: string, role: string, managerId?: string) => ({ id, name: id, role, managerId, persistent: true, lifecycle: 'active' as const, status: 'idle' as const, responsibilities: [], capabilities: role === 'Executive' ? ['request.accept'] : ['request.plan', 'request.integrate'], summary: id })

  const snapshot: OrganizationSnapshot = {
    source: 'runtime', objectives: [], tasks: [], knowledge: [], activity: [], requests: [],
    agents: [leader('executive', 'Executive'), leader('manager', 'Manager', 'executive'), leader('lunavale', 'Executive'), leader('accountservices', 'Manager', 'lunavale'), { ...leader('disabled-manager', 'Manager', 'lunavale'), lifecycle: 'disabled' }, { ...leader('staffing-only', 'Manager', 'lunavale'), capabilities: ['request.hire'] }],
    runtime: { capabilities: ['work.analyze'], scope: 'Submitted text', state: 'ready', maxWorkers: 2 }
  }

  const request = vi.fn().mockImplementation((method: string) => method === 'organization.create' ? Promise.reject(new Error('Test admission paused')) : Promise.resolve(snapshot))
  const adapter = createRuntimeAdapter({ request: request as OrganizationGateway['request'], getScope: () => ({ key: 'ownership', connected: true }), subscribeScope: () => () => undefined })
  render(<MemoryRouter initialEntries={['/home']}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
  const executive = await screen.findByRole('combobox', { name: 'Executive owner' })
  const manager = screen.getByRole('combobox', { name: 'Responsible manager' })
  expect(executive).toHaveProperty('value', 'executive')
  expect(manager).toHaveProperty('value', 'manager')
  fireEvent.change(screen.getByRole('textbox', { name: 'Objective' }), { target: { value: 'Analyze account services' } })
  fireEvent.change(executive, { target: { value: 'lunavale' } })
  expect(manager).toHaveProperty('value', '')
  expect(screen.queryByRole('option', { name: 'manager' })).toBeNull()
  expect(screen.queryByRole('option', { name: 'disabled-manager' })).toBeNull()
  expect(screen.queryByRole('option', { name: 'staffing-only' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Create objective' }))
  expect(screen.getByRole('alert').textContent).toBe('Choose an active executive and one of their active managers.')
  expect(request.mock.calls.some(call => call[0] === 'organization.create')).toBe(false)
  fireEvent.change(manager, { target: { value: 'accountservices' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create objective' }))
  await screen.findByText('Test admission paused')
  expect(request.mock.calls.find(call => call[0] === 'organization.create')![1]).toMatchObject({ title: 'Analyze account services', executiveId: 'lunavale', managerId: 'accountservices' })
  fireEvent.change(executive, { target: { value: 'executive' } })
  expect(manager).toHaveProperty('value', '')
  expect(screen.queryByRole('option', { name: 'accountservices' })).toBeNull()
})

it('submits only selected configured repository IDs and keeps a failed submission editable', async () => {
  const projects = [
    { id: 'frontend', root: 'root0', recipe: 'web-tests', team: 'web' },
    { id: 'backend', root: 'root1', recipe: 'api-tests', team: 'api' }
  ]

  const snapshot: OrganizationSnapshot = { source: 'runtime', objectives: [], agents: [], tasks: [], knowledge: [], activity: [], requests: [], runtime: { capabilities: [], scope: 'Configured projects', state: 'ready', maxWorkers: 2, availableProjects: projects } }
  const request = vi.fn().mockImplementation((method: string) => method === 'organization.create' ? Promise.reject(new Error('Test admission paused')) : Promise.resolve(snapshot))
  const adapter = createRuntimeAdapter({ request: request as OrganizationGateway['request'], getScope: () => ({ key: 'projects', connected: true }), subscribeScope: () => () => undefined })
  render(<MemoryRouter initialEntries={['/home']}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
  const frontend = await screen.findByRole('checkbox', { name: /frontend/ })
  const backend = screen.getByRole('checkbox', { name: /backend/ })
  fireEvent.change(screen.getByRole('textbox', { name: 'Objective' }), { target: { value: 'Coordinate the release' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create objective' }))
  expect(screen.getByText('Select at least one repository for source-project delivery.')).toBeTruthy()
  expect(request.mock.calls.some(call => call[0] === 'organization.create')).toBe(false)
  fireEvent.click(frontend)
  fireEvent.click(backend)
  fireEvent.click(frontend)
  fireEvent.click(screen.getByRole('button', { name: 'Create objective' }))
  await screen.findByText('Test admission paused')
  const params = request.mock.calls.find(call => call[0] === 'organization.create')![1]
  expect(params.projectIds).toEqual(['backend'])
  expect(JSON.stringify(params)).not.toContain('/owner/')
  expect(backend).toHaveProperty('checked', true)
  expect(backend).toHaveProperty('disabled', false)
})

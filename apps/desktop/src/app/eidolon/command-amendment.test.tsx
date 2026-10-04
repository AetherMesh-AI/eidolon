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

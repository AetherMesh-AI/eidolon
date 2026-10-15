import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { OrganizationMilestones } from './organization-milestones'
const mocks = vi.hoisted(() => ({ eligible: true, current: true, pending: false, read: vi.fn(), save: vi.fn() }))
vi.mock('@/i18n/context', () => ({ useI18n: () => ({ locale: 'en' }) }))
vi.mock('./use-organization-metadata', () => ({ useOrganizationMetadata: () => ({ scope: { eligible: mocks.eligible }, view: { descriptor: { metadata: { organizationId: 'org' } } } }), organizationMetadataClient: { getSnapshot: () => ({ descriptor: { metadata: { organizationId: 'org', owner: { ownerId: 'owner' } } } }) }, readOrganizationMetadataScope: () => ({ eligible: mocks.eligible }) }))
vi.mock('./organization-milestone-client', () => ({ createMilestoneClient: () => ({ current: () => mocks.current, pending: () => mocks.pending, read: mocks.read, save: mocks.save }) }))
const view = { milestones: [], objectives: [{ id: 'objective', title: 'Build release', projectIds: ['project'] }] }
beforeEach(() => { mocks.eligible = true; mocks.current = true; mocks.pending = false; mocks.read.mockReset().mockResolvedValue(view); mocks.save.mockReset().mockResolvedValue({}) })
describe('owner milestone panel', () => {
  it('requires review and keeps uncertain intent fields locked for retry', async () => {
    mocks.save.mockImplementationOnce(() => { mocks.pending = true;

 return Promise.reject(new Error('timeout')) })
    const page = render(<OrganizationMilestones />)
    fireEvent.click(screen.getByText('Load milestones'))
    await screen.findByText('New milestone')
    fireEvent.change(screen.getByLabelText('Milestone ID'), { target: { value: 'launch' } })
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Launch' } })
    fireEvent.click(screen.getByLabelText('Build release · project'))
    expect((screen.getByText('Review and save milestone') as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByLabelText('I reviewed the linked objectives and prerequisites.'))
    fireEvent.click(screen.getByText('Review and save milestone'))
    await screen.findByText('timeout')
    expect((screen.getByLabelText('Title') as HTMLInputElement).disabled).toBe(true)
    mocks.current = false; page.rerender(<OrganizationMilestones />)
    expect(screen.getByText('Return to the same owner connection and check the pending save receipt before retrying.')).toBeTruthy()
    expect((screen.getByText('Load milestones') as HTMLButtonElement).disabled).toBe(true)
    mocks.current = true; page.rerender(<OrganizationMilestones />)
    fireEvent.click(screen.getByText('Retry the same save'))
    await waitFor(() => expect(mocks.save).toHaveBeenCalledTimes(2))
    expect(mocks.save.mock.calls[1]).toEqual(mocks.save.mock.calls[0])
  })
  it('disables old selection when owner scope changes', async () => {
    const page = render(<OrganizationMilestones />)
    fireEvent.click(screen.getByText('Load milestones')); await screen.findByText('New milestone')
    mocks.current = false; mocks.eligible = false; page.rerender(<OrganizationMilestones />)
    expect(screen.getByText('The owner connection changed. Load milestones to open the current organization.')).toBeTruthy()
    expect((screen.getByLabelText('Title') as HTMLInputElement).disabled).toBe(true)
    expect(mocks.save).not.toHaveBeenCalled()
    mocks.eligible = true; page.rerender(<OrganizationMilestones />)
    expect((screen.getByText('Load milestones') as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(screen.getByText('Load milestones'))
    await waitFor(() => expect(mocks.read).toHaveBeenCalledTimes(2))
  })
})

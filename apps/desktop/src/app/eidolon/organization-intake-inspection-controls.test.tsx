import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { intakeInspectionCopy } from '@/i18n/organization-intake-inspection'

import type { IntakeInspectionDescriptor } from './organization-intake-inspection-client'
import { OrganizationIntakeInspectionControls } from './organization-intake-inspection-controls'

const state = vi.hoisted(() => ({
  key: 'socket',
  ownerKey: 'owner',
  profile: 'default',
  eligible: true,
  request: vi.fn()
}))

vi.mock('./use-organization-metadata', () => {
  const source = () => ({ ...state })

  return { readOrganizationMetadataScope: source, useOrganizationMetadata: () => ({ scope: source() }) }
})
const copy = intakeInspectionCopy.en
let serial = 0

function descriptor(): IntakeInspectionDescriptor {
  return {
    profile: 'default',
    organizationId: 'org',
    revision: 0,
    enabled: false,
    workerId: null,
    projectIds: [],
    paths: [],
    workers: [{ id: 'worker', name: 'Research worker', team: 'research' }],
    projects: [
      { id: 'docs', root: 'root0', team: 'research' },
      { id: 'private', root: 'root1', team: 'other' }
    ]
  }
}

function backend() {
  let value = descriptor()
  state.request.mockImplementation(async (method: string, params: Record<string, unknown>) => {
    if (method.endsWith('.configure')) {
      value = {
        ...value,
        enabled: params.enabled as boolean,
        workerId: params.workerId as string | null,
        projectIds: params.projectIds as string[],
        paths: params.paths as string[],
        revision: value.revision + 1
      }
    }

    return structuredClone(value)
  })
}

async function open() {
  fireEvent.click(screen.getByText(copy.title))
  fireEvent.click(screen.getByRole('button', { name: copy.read }))
  await screen.findByLabelText(copy.enabled)
}

function edit() {
  fireEvent.click(screen.getByLabelText(copy.enabled))
  fireEvent.change(screen.getByLabelText(copy.worker), { target: { value: 'worker' } })
  fireEvent.click(screen.getByLabelText('docs · root0'))
  fireEvent.change(screen.getByLabelText(copy.paths), { target: { value: 'root0/README.md\nroot0/context.md' } })
}

const mutations = () => state.request.mock.calls.filter(([method]) => method.endsWith('.configure'))
const confirm = () => fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: copy.confirm }))

beforeEach(() => {
  state.key = `socket-${++serial}`
  state.ownerKey = 'owner'
  state.profile = 'default'
  state.eligible = true
  state.request.mockReset()
  backend()
})
afterEach(cleanup)

describe('fresh context owner controls', () => {
  it('mounts passively, reviews named scope, cancels, confirms and explicitly disables', async () => {
    render(<OrganizationIntakeInspectionControls />)
    expect(state.request).not.toHaveBeenCalled()
    await open()
    edit()
    expect(screen.queryByLabelText('private · root1')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: copy.review }))
    const dialog = within(screen.getByRole('dialog'))
    expect(dialog.getByText('Research worker · worker')).toBeTruthy()
    expect(dialog.getByText('docs · root0')).toBeTruthy()
    expect(dialog.getByText('root0/README.md, root0/context.md')).toBeTruthy()
    expect(dialog.getByText(copy.bounds, { exact: false })).toBeTruthy()
    expect(dialog.getByText(copy.safety, { exact: false })).toBeTruthy()
    fireEvent.click(dialog.getByRole('button', { name: copy.cancel }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(mutations()).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: copy.review }))
    confirm()
    await screen.findByText(copy.saved)
    expect(mutations()).toHaveLength(1)
    expect(mutations()[0][1]).toMatchObject({
      enabled: true,
      workerId: 'worker',
      projectIds: ['docs'],
      paths: ['root0/README.md', 'root0/context.md'],
      confirmed: true
    })
    fireEvent.click(screen.getByLabelText(copy.enabled))
    fireEvent.click(screen.getByRole('button', { name: copy.review }))
    expect(within(screen.getByRole('dialog')).getByText(copy.disableNote, { exact: false })).toBeTruthy()
    confirm()
    await screen.findByText(copy.saved)
    expect(mutations()[1][1]).toMatchObject({
      expectedRevision: 1,
      enabled: false,
      workerId: null,
      projectIds: [],
      paths: []
    })
  })

  it('preserves exact uncertain retries across remounts and never writes from refresh or repeated review', async () => {
    let mounted = render(<OrganizationIntakeInspectionControls />)
    await open()
    edit()
    fireEvent.click(screen.getByRole('button', { name: copy.review }))
    state.request.mockRejectedValueOnce(new Error('Reply lost'))
    confirm()
    await screen.findByText(copy.uncertain)
    const sent = structuredClone(mutations()[0])
    expect(screen.getByRole<HTMLButtonElement>('button', { name: copy.read }).disabled).toBe(true)
    expect(screen.getByLabelText<HTMLTextAreaElement>(copy.paths).disabled).toBe(true)
    mounted.unmount()
    mounted = render(<OrganizationIntakeInspectionControls />)
    expect(screen.getByText(copy.uncertain)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: copy.retry }))
    await screen.findByText(copy.saved)
    expect(mutations()).toEqual([sent, sent])
    mounted.unmount()
  })

  it('closes stale confirmations, clears drafts and ignores old in-flight completions on scope changes', async () => {
    const mounted = render(<OrganizationIntakeInspectionControls />)
    await open()
    edit()
    fireEvent.click(screen.getByRole('button', { name: copy.review }))
    state.key = 'replacement-socket'
    mounted.rerender(<OrganizationIntakeInspectionControls />)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.queryByLabelText(copy.paths)).toBeNull()
    expect(mutations()).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: copy.read }))
    await screen.findByLabelText(copy.enabled)
    edit()
    fireEvent.click(screen.getByRole('button', { name: copy.review }))
    let resolve!: (value: IntakeInspectionDescriptor) => void
    state.request.mockReturnValueOnce(
      new Promise<IntakeInspectionDescriptor>(yes => {
        resolve = yes
      })
    )
    confirm()
    state.ownerKey = 'new-owner'
    mounted.rerender(<OrganizationIntakeInspectionControls />)
    await act(async () => {
      resolve({
        ...descriptor(),
        enabled: true,
        workerId: 'worker',
        projectIds: ['docs'],
        paths: ['root0/README.md', 'root0/context.md'],
        revision: 1
      })
    })
    await waitFor(() => expect(screen.queryByText(copy.saved)).toBeNull())
    expect(screen.queryByLabelText(copy.paths)).toBeNull()
    expect(screen.queryByRole('button', { name: copy.retry })).toBeNull()
    expect(mutations()).toHaveLength(1)
  })
})

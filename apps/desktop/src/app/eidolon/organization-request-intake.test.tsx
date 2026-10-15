import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { requestIntakeCopy } from '@/i18n/organization-request-intake'

import { OrganizationRequestIntake } from './organization-request-intake'
import { intakeProposal, intakeRecord } from './organization-request-intake-test-support'
import type { OrganizationSnapshot } from './types'

const state = vi.hoisted(() => ({
  key: 'socket',
  ownerKey: 'owner',
  eligible: true,
  request: vi.fn(),
  history: vi.fn()
}))

vi.mock('./use-organization-metadata', () => {
  const source = () => ({
    key: state.key,
    ownerKey: state.ownerKey,
    eligible: state.eligible,
    profile: 'default',
    request: (method: string, params: Record<string, unknown>) =>
      method === 'organization.intake.list' ? state.history(method, params) : state.request(method, params)
  })

  return { readOrganizationMetadataScope: source, useOrganizationMetadata: () => ({ scope: source() }) }
})

const snapshot = {
  agents: [
    { id: 'executive', name: 'Executive team' },
    { id: 'manager', name: 'Launch team' }
  ]
} as OrganizationSnapshot

const copy = requestIntakeCopy.en

const flush = async () => {
  await act(async () => {
    await Promise.resolve()
  })
}

const tick = async () => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000)
  })
}

const mount = () =>
  render(
    <MemoryRouter>
      <OrganizationRequestIntake snapshot={snapshot} />
    </MemoryRouter>
  )

let testOwner = 0

beforeEach(() => {
  state.key = `socket-${++testOwner}`
  state.ownerKey = `owner-${testOwner}`
  state.eligible = true
  state.request.mockReset()
  state.history.mockReset().mockResolvedValue({ profile: 'default', items: [] })
  vi.useFakeTimers()
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('plain-language request intake', () => {
  it('submits only on intent, polls passive reads through admission, and answers clarification with a separate explicit action', async () => {
    let key = ''
    const proposal = intakeProposal()
    state.request.mockImplementation(async (method: string, params: Record<string, unknown>) => {
      if (method === 'organization.intake.submit') {
        key = String(params.idempotencyKey)

        return intakeRecord({ idempotencyKey: key })
      }

      return intakeRecord({ idempotencyKey: key, status: 'proposed', proposal })
    })
    const view = mount()
    await tick()
    expect(state.request).not.toHaveBeenCalled()
    expect(state.history).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText(copy.request), { target: { value: 'Fix launch failure' } })
    fireEvent.change(screen.getByLabelText(copy.priority), { target: { value: 'P1' } })
    fireEvent.click(screen.getByRole('button', { name: copy.submit }))
    await flush()
    expect(screen.getByRole('status').textContent).toBe(copy.pending)
    expect(state.request.mock.calls[0]).toEqual([
      'organization.intake.submit',
      { text: 'Fix launch failure', ownerPriority: 'P1', idempotencyKey: key }
    ])
    await tick()
    expect(screen.getByRole('status').textContent).toBe(copy.proposed)
    expect(screen.getByText(proposal.rationale)).toBeTruthy()
    expect(screen.getByText('Launch team')).toBeTruthy()
    expect(screen.getByText(/Owner priority applied/)).toBeTruthy()
    state.request.mockResolvedValueOnce(
      intakeRecord({ idempotencyKey: key, status: 'admitted', proposal, objectiveId: 'objective-a' })
    )
    await tick()
    expect(screen.getByRole('status').textContent).toBe(copy.admitted)
    expect(screen.getByRole('link', { name: 'Open objective' }).getAttribute('href')).toBe('/objectives/objective-a')
    const count = state.request.mock.calls.length
    await tick()
    expect(state.request).toHaveBeenCalledTimes(count)
    expect(state.request.mock.calls.slice(1).every(([method]) => method === 'organization.intake.read')).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: copy.next }))
    state.request.mockImplementationOnce(async (_method: string, params: Record<string, unknown>) =>
      intakeRecord({
        id: 'question-a',
        idempotencyKey: String(params.idempotencyKey),
        request: 'Which project needs this?',
        status: 'needs_answer',
        proposal: {
          ...proposal,
          status: 'needs_answer',
          question: {
            text: 'Which app is affected?',
            neededFor: 'Choose the responsible project.',
            checkedEvidenceIds: []
          }
        }
      })
    )
    fireEvent.change(screen.getByLabelText(copy.request), { target: { value: 'Which project needs this?' } })
    fireEvent.click(screen.getByRole('button', { name: copy.submit }))
    await flush()
    expect(screen.getByText('Which app is affected?')).toBeTruthy()
    fireEvent.change(screen.getByLabelText(copy.answer), { target: { value: 'The desktop app' } })
    const beforeAnswer = state.request.mock.calls.length
    await tick()
    expect(state.request).toHaveBeenCalledTimes(beforeAnswer)
    state.request.mockImplementationOnce(async (_method: string, params: Record<string, unknown>) =>
      intakeRecord({
        id: 'answer-a',
        parentIntakeId: 'question-a',
        idempotencyKey: String(params.idempotencyKey),
        request: 'Combined request and clarification'
      })
    )
    fireEvent.click(screen.getByRole('button', { name: copy.sendAnswer }))
    await flush()
    expect(state.request.mock.lastCall).toEqual([
      'organization.intake.answer',
      { intakeId: 'question-a', text: 'The desktop app', idempotencyKey: expect.any(String) }
    ])
    expect(screen.queryByLabelText(copy.answer)).toBeNull()
    view.unmount()
    await tick()
    expect(state.request).toHaveBeenCalledTimes(beforeAnswer + 1)
  })

  it('keeps uncertain writes across remounts, stops failed reads, and leaves review and manual fallback visible without duplicating', async () => {
    state.request.mockRejectedValueOnce(new Error('Connection lost after sending'))
    let view = mount()
    fireEvent.change(screen.getByLabelText(copy.request), { target: { value: 'Fix launch failure' } })
    fireEvent.click(screen.getByRole('button', { name: copy.submit }))
    await flush()
    const sent = state.request.mock.calls[0]
    expect(screen.getByText(copy.uncertainTransport)).toBeTruthy()
    expect(screen.getByLabelText<HTMLTextAreaElement>(copy.request).disabled).toBe(true)
    view.unmount()
    view = mount()
    await tick()
    expect(state.request).toHaveBeenCalledTimes(1)
    state.request.mockResolvedValueOnce(intakeRecord({ idempotencyKey: sent[1].idempotencyKey }))
    fireEvent.click(screen.getByRole('button', { name: copy.retry }))
    await flush()
    expect(state.request.mock.lastCall).toEqual(sent)
    state.request.mockRejectedValueOnce(new Error('Read unavailable'))
    await tick()
    expect(screen.getByText(copy.readFailed)).toBeTruthy()
    const count = state.request.mock.calls.length
    await tick()
    expect(state.request).toHaveBeenCalledTimes(count)
    state.request.mockResolvedValueOnce(
      intakeRecord({
        idempotencyKey: sent[1].idempotencyKey,
        status: 'needs_review',
        proposal: { ...intakeProposal(), duplicateCandidateIds: ['existing-objective'] }
      })
    )
    fireEvent.click(screen.getByRole('button', { name: copy.read }))
    await flush()
    expect(screen.getByRole('status').textContent).toBe(copy.needs_review)
    expect(screen.getByText('existing-objective')).toBeTruthy()
    expect(screen.getByText(copy.manual, { exact: false })).toBeTruthy()
    expect(screen.queryByRole('link', { name: 'Open objective' })).toBeNull()
    expect(state.request.mock.calls.filter(([method]) => method === 'organization.intake.submit')).toEqual([sent, sent])
    state.ownerKey = 'another-owner'
    state.key = 'another-socket'
    view.rerender(
      <MemoryRouter>
        <OrganizationRequestIntake snapshot={snapshot} />
      </MemoryRouter>
    )
    expect(screen.getByLabelText<HTMLTextAreaElement>(copy.request).value).toBe('')
    expect(screen.queryByText('existing-objective')).toBeNull()
    await tick()
    expect(state.request).toHaveBeenCalledTimes(count + 1)
  })
})

it('opens an existing persisted clarification from recent requests without resubmitting it', async () => {
  const question = intakeRecord({
    request: 'Persisted owner request',
    status: 'needs_answer',
    proposal: {
      ...intakeProposal(),
      status: 'needs_answer',
      question: { text: 'Which app was affected?', neededFor: 'Route the work.', checkedEvidenceIds: [] }
    }
  })

  state.history.mockResolvedValueOnce({ profile: 'default', items: [question] })
  state.request.mockResolvedValueOnce(question)
  mount()
  await flush()
  expect(state.history).not.toHaveBeenCalled()
  const history = screen.getByText(copy.recent).closest('details')!
  history.open = true
  fireEvent(history, new Event('toggle'))
  await flush()
  expect(state.history).toHaveBeenCalledWith('organization.intake.list', {})
  expect(screen.queryByLabelText(copy.answer)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: `Persisted owner request · ${copy.needs_answer}` }))
  await flush()
  expect(screen.getByText('Which app was affected?')).toBeTruthy()
  expect(screen.getByLabelText<HTMLTextAreaElement>(copy.request).value).toBe('Persisted owner request')
  expect(state.request.mock.calls).toEqual([['organization.intake.read', { intakeId: question.id }]])
  await tick()
  expect(state.request).toHaveBeenCalledTimes(1)
  fireEvent.change(screen.getByLabelText(copy.answer), { target: { value: 'Desktop' } })
  state.request.mockImplementationOnce(async (_method: string, params: Record<string, unknown>) =>
    intakeRecord({
      id: 'restored-answer',
      parentIntakeId: question.id,
      idempotencyKey: String(params.idempotencyKey),
      request: 'Persisted owner request with clarification'
    })
  )
  fireEvent.click(screen.getByRole('button', { name: copy.sendAnswer }))
  await flush()
  expect(state.request.mock.lastCall).toEqual([
    'organization.intake.answer',
    { intakeId: question.id, text: 'Desktop', idempotencyKey: expect.any(String) }
  ])
  expect(state.request.mock.calls.some(([method]) => method === 'organization.intake.submit')).toBe(false)
})


it('loads history only when opened and never on close or an ineligible scope', async () => {
  const rendered = mount()
  await tick()
  expect(state.history).not.toHaveBeenCalled()
  const history = screen.getByText(copy.recent).closest('details')!
  history.open = true
  fireEvent(history, new Event('toggle'))
  await flush()
  expect(state.history).toHaveBeenCalledTimes(1)
  history.open = false
  fireEvent(history, new Event('toggle'))
  await tick()
  expect(state.history).toHaveBeenCalledTimes(1)
  state.key = 'different-ineligible-socket'
  state.ownerKey = 'different-owner'
  state.eligible = false
  rendered.rerender(<MemoryRouter><OrganizationRequestIntake snapshot={snapshot} /></MemoryRouter>)
  await tick()
  expect(state.history).toHaveBeenCalledTimes(1)
  expect(screen.queryByText(copy.recent)).toBeNull()
})

import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router'
import { expect, it, vi } from 'vitest'

import { RuntimeReplacement } from './runtime-replacement'
import type { Objective, ObjectiveReplacementDraft, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

const original: Objective = {
  id: 'original',
  title: 'Expired work',
  description: 'Retain scope',
  status: 'needs_input',
  source: 'runtime',
  ownerId: 'executive',
  createdAt: '2026-10-01T00:00:00Z',
  replacementEligible: true
}

const draft: ObjectiveReplacementDraft = {
  reasonCodes: ['model_calls'],
  sourceUsage: {
    modelCalls: 8,
    modelCallLimit: 8,
    reservedTokens: 4096,
    tokenLimit: 8192,
    stages: 5,
    stageLimit: 10,
    inputTokens: 0,
    outputTokens: 0,
    usageComplete: false,
    perCallOutputLimit: 512,
    configuredCostReservedUsd: null,
    configuredCostLimitUsd: '1.00',
    deadlineAt: '2026-10-11T00:00:00Z'
  },
  sourceId: original.id,
  sourceVersion: 'a'.repeat(64),
  title: original.title,
  description: original.description,
  acceptanceCriteria: ['Keep evidence'],
  executiveId: 'executive',
  managerId: 'manager',
  projectIds: ['repo'],
  requiredChecks: ['project_tests'],
  deliveryMode: 'source_project',
  allowance: {
    modelCalls: 8,
    tokens: 4096,
    durationSeconds: 3600,
    costUsd: '1.00',
    maxReplans: 2,
    maxStages: 10,
    projectRuns: 1
  }
}

const snapshot: OrganizationSnapshot = {
  source: 'runtime',
  objectives: [original],
  agents: [],
  tasks: [],
  activity: [],
  knowledge: [],
  connection: { state: 'ready', scope: 'socket', ownerScope: 'owner' }
}

function Location() {
  return <output aria-label="Location">{useLocation().pathname}</output>
}

function setup() {
  let resolve!: (objective: Objective) => void

  const result = new Promise<Objective>(yes => {
    resolve = yes
  })

  const replaceObjective = vi.fn(() => result)
  const previewReplacement = vi.fn().mockResolvedValue(draft)
  const adapter = { previewReplacement, replaceObjective } as unknown as RuntimeOrganizationAdapter

  const view = render(
    <MemoryRouter initialEntries={['/objectives/original']}>
      <Location />
      <RuntimeReplacement adapter={adapter} objective={original} snapshot={snapshot} />
    </MemoryRouter>
  )

  return { view, resolve, previewReplacement, replaceObjective }
}

it('requires fresh consent, preserves editable draft, and submits once across rapid clicks', async () => {
  const test = setup()
  fireEvent.click(screen.getByRole('button', { name: 'Review replacement' }))
  await screen.findByRole('textbox', { name: 'Revised scope' })
  expect(
    (screen.getByRole('button', { name: 'Cancel original and create replacement' }) as HTMLButtonElement).disabled
  ).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(test.replaceObjective).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Review replacement' }))
  await screen.findByRole('textbox', { name: 'Revised scope' })
  fireEvent.change(screen.getByRole('textbox', { name: 'Revised scope' }), {
    target: { value: 'Revised scope from owner' }
  })
  expect(screen.getByText('Model-call allowance exhausted')).toBeTruthy()
  expect(screen.getByLabelText('Original retained usage').textContent).toContain('8 / 8')
  fireEvent.click(screen.getByRole('checkbox'))
  const confirm = screen.getByRole('button', { name: 'Cancel original and create replacement' })
  fireEvent.click(confirm)
  fireEvent.click(confirm)
  expect(test.replaceObjective).toHaveBeenCalledTimes(1)
  expect(test.replaceObjective).toHaveBeenCalledWith({
    sourceId: 'original',
    sourceVersion: draft.sourceVersion,
    title: original.title,
    description: 'Revised scope from owner',
    acceptanceCriteria: ['Keep evidence'],
    confirmed: true
  })
  await act(async () => {
    test.resolve({ ...original, id: 'replacement', replacesObjectiveId: original.id })
  })
  expect(screen.getByLabelText('Location').textContent).toBe('/objectives/replacement')
})

it('does not navigate a closed review on late success and exposes durable replacement links after reload', async () => {
  const test = setup()
  fireEvent.click(screen.getByRole('button', { name: 'Review replacement' }))
  await screen.findByRole('textbox', { name: 'Revised scope' })
  expect(screen.getByText('Model-call allowance exhausted')).toBeTruthy()
  expect(screen.getByLabelText('Original retained usage').textContent).toContain('8 / 8')
  fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(screen.getByRole('button', { name: 'Cancel original and create replacement' }))
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  await act(async () => {
    test.resolve({ ...original, id: 'replacement' })
  })
  expect(screen.getByLabelText('Location').textContent).toBe('/objectives/original')
  test.view.unmount()
  render(
    <MemoryRouter>
      <RuntimeReplacement
        adapter={{} as RuntimeOrganizationAdapter}
        objective={{ ...original, replacementEligible: false, replacementObjectiveId: 'replacement' }}
        snapshot={snapshot}
      />
    </MemoryRouter>
  )
  expect(screen.getByRole('link', { name: 'Replacement objective' }).getAttribute('href')).toBe(
    '/objectives/replacement'
  )
  expect(screen.queryByRole('button', { name: 'Review replacement' })).toBeNull()
})

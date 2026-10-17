import { expect, it, vi } from 'vitest'

import { createTeamOnboardingClient, type TeamPreview, type TeamSetup } from './team-onboarding-client'

it('captures the authenticated scope and permanently retires writes after navigation, including A → B → A', async () => {
  let finish!: (value: unknown) => void

  const request = vi.fn(
    () =>
      new Promise(resolve => {
        finish = resolve
      })
  )

  let key = 'A'

  let changed = () => {}

  const client = createTeamOnboardingClient(
    () => ({ key, ownerKey: key, profile: 'default', eligible: true, request: request as never }),
    listener => {
      changed = listener

      return () => {}
    }
  )

  const pending = client.setup('organization')
  key = 'B'
  changed()
  key = 'A'
  changed()
  finish({ state: 'available' })
  await expect(pending).rejects.toThrow('stale')
  await expect(client.create({} as TeamPreview, 'intent')).rejects.toThrow('stale')
  expect(request).toHaveBeenCalledTimes(1)
})

it('serializes clicks and sends only explicit confirmed creation, retaining the retry receipt identity', async () => {
  let finish!: (value: unknown) => void

  const request = vi.fn(
    () =>
      new Promise(resolve => {
        finish = resolve
      })
  )

  const client = createTeamOnboardingClient(
    () => ({ key: 'A', ownerKey: 'owner', profile: 'default', eligible: true, request: request as never }),
    () => () => {}
  )

  const setup = { organizationId: 'organization', sourceRevision: 'revision' } as TeamSetup

  const generation = client.generate(setup, {
    jobs: 'Research',
    goals: 'Find evidence',
    namingStyle: '',
    idempotencyKey: 'generation'
  })

  await expect(
    client.generate(setup, { jobs: 'Research', goals: 'Find evidence', namingStyle: '', idempotencyKey: 'generation' })
  ).rejects.toThrow('busy')

  const preview = {
    organizationId: 'organization',
    sourceRevision: 'revision',
    previewVersion: 'preview',
    team: { executive: { name: 'Lead', purpose: '', responsibilities: [] }, members: [] }
  } as unknown as TeamPreview

  finish(preview)
  await generation
  expect(request).toHaveBeenCalledTimes(1)
  const creation = client.create(preview, 'receipt')
  expect(request.mock.calls[1]).toEqual([
    'organization.createTeam',
    {
      expectedOrganizationId: 'organization',
      sourceRevision: 'revision',
      previewVersion: 'preview',
      team: preview.team,
      idempotencyKey: 'receipt',
      confirmed: true
    }
  ])
  finish({ state: 'created' })
  await creation
  const retry = client.create(preview, 'receipt')
  finish({ state: 'created' })
  await retry
  expect(request.mock.calls[2]).toEqual(request.mock.calls[1])
  client.dispose()
  await expect(client.setup('organization')).rejects.toThrow('stale')
})

const improvementSetup = {
  capabilityVersion: 1,
  inputImprovementVersion: 1,
  organizationId: 'organization',
  sourceRevision: 'revision'
} as TeamSetup

const improvementInput = {
  field: 'jobs' as const,
  jobs: 'Research',
  goals: 'A weekly report',
  namingStyle: 'Short names',
  idempotencyKey: 'improve-jobs'
}

const suggestion = {
  capabilityVersion: 1,
  organizationId: 'organization',
  sourceRevision: 'revision',
  field: 'jobs',
  suggestion: 'Research the supplied sources and draft a weekly report.',
  assumptions: ['The report is for your own review.']
}

function improvementClient(response: unknown) {
  const request = vi.fn(async () => response)

  const client = createTeamOnboardingClient(
    () => ({ key: 'A', ownerKey: 'owner', profile: 'default', eligible: true, request: request as never }),
    () => () => {}
  )

  return { client, request }
}

it('requests a review-only suggestion with exact entered context and requires runtime capability', async () => {
  const { client, request } = improvementClient(suggestion)
  await expect(client.improve(improvementSetup, improvementInput)).resolves.toEqual(suggestion)
  expect(request.mock.calls).toEqual([
    [
      'organization.improveTeamInput',
      {
        expectedOrganizationId: 'organization',
        sourceRevision: 'revision',
        ...improvementInput
      }
    ]
  ])
  expect(improvementInput.jobs).toBe('Research')
  await expect(
    client.improve({ ...improvementSetup, inputImprovementVersion: undefined }, improvementInput)
  ).rejects.toThrow('unsupported')
  expect(request).toHaveBeenCalledTimes(1)
})

it.each([
  null,
  [],
  { ...suggestion, capabilityVersion: 2 },
  { ...suggestion, organizationId: 'other' },
  { ...suggestion, sourceRevision: 'other' },
  { ...suggestion, field: 'goals' },
  { ...suggestion, suggestion: ' ' },
  { ...suggestion, suggestion: 'x'.repeat(4001) },
  { ...suggestion, suggestion: 'Hidden\u0000text' },
  { ...suggestion, assumptions: null },
  { ...suggestion, assumptions: [''] },
  { ...suggestion, assumptions: ['x'.repeat(301)] },
  { ...suggestion, assumptions: Array(6).fill('assumption') }
])('rejects malformed or mismatched input-improvement responses %#', async response => {
  const { client } = improvementClient(response)
  await expect(client.improve(improvementSetup, improvementInput)).rejects.toThrow('invalid_suggestion')
})

it('rejects an improvement received after owner navigation and never sends a concurrent generation', async () => {
  let finish!: (value: unknown) => void

  const request = vi.fn(
    () =>
      new Promise(resolve => {
        finish = resolve
      })
  )

  let key = 'A'

  let changed = () => {}

  const client = createTeamOnboardingClient(
    () => ({ key, ownerKey: key, profile: 'default', eligible: true, request: request as never }),
    listener => {
      changed = listener

      return () => {}
    }
  )

  const pending = client.improve(improvementSetup, improvementInput)
  await expect(client.generate(improvementSetup, improvementInput)).rejects.toThrow('busy')
  key = 'B'
  changed()
  key = 'A'
  changed()
  finish(suggestion)
  await expect(pending).rejects.toThrow('stale')
  expect(request).toHaveBeenCalledTimes(1)
})

it.each([
  null,
  { status: 'ready', remainingCalls: 2 },
  { status: ['available'], remainingCalls: 2 },
  { status: 'available', remainingCalls: 0 },
  { status: 'available', remainingCalls: '2' },
  { status: 'available', remainingCalls: 4 },
  { status: 'available', remainingCalls: -1 }
])('rejects malformed authoritative generation allowance %#', async generationAllowance => {
  const { client } = improvementClient({ ...suggestion, generationAllowance })
  await expect(client.improve(improvementSetup, improvementInput)).rejects.toThrow('invalid_allowance')
  await expect(client.setup('organization')).rejects.toThrow('invalid_allowance')
})

it('retains authoritative shared allowance on suggestions without counting attempts locally', async () => {
  const generationAllowance = { status: 'exhausted', remainingCalls: 0 }
  const { client } = improvementClient({ ...suggestion, generationAllowance })
  await expect(client.improve(improvementSetup, improvementInput)).resolves.toEqual({ ...suggestion, generationAllowance })
})

it('accepts explicitly versioned split allowance while preserving operation-specific counters', async () => {
  const generationAllowance = {
    policyVersion: 2, status: 'available', remainingCalls: 7,
    remainingByOperation: { improveTeamInput: 4, generateTeam: 3 }
  }

  const { client } = improvementClient({ ...suggestion, generationAllowance })
  await expect(client.improve(improvementSetup, improvementInput)).resolves.toEqual({ ...suggestion, generationAllowance })
})

it.each([
  { policyVersion: 2, status: 'available', remainingCalls: 7 },
  { policyVersion: 3, status: 'available', remainingCalls: 7, remainingByOperation: { improveTeamInput: 4, generateTeam: 3 } },
  { policyVersion: 2, status: 'available', remainingCalls: 7, remainingByOperation: { improveTeamInput: 8, generateTeam: 3 } },
  { policyVersion: 2, status: 'available', remainingCalls: 7, remainingByOperation: { improveTeamInput: true, generateTeam: 3 } },
  { policyVersion: 2, status: 'available', remainingCalls: 7, remainingByOperation: { improveTeamInput: 4, generateTeam: 3, legacy: 0 } },
  { status: 'available', remainingCalls: 3, remainingByOperation: { improveTeamInput: 3, generateTeam: 3 } }
])('rejects malformed split allowance %#', async generationAllowance => {
  const { client } = improvementClient({ ...suggestion, generationAllowance })
  await expect(client.improve(improvementSetup, improvementInput)).rejects.toThrow('invalid_allowance')
})

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

it('uses the ordinary one-shot helper with exact context independent of setup generation capability', async () => {
  const { client, request } = improvementClient({ text: JSON.stringify(suggestion) })
  await expect(client.improve(improvementSetup, improvementInput)).resolves.toEqual(suggestion)
  expect(request.mock.calls).toEqual([
    [
      'llm.oneshot',
      {
        template: 'improve_team_input',
        session_id: undefined,
        variables: { field: 'jobs', jobs: 'Research', goals: 'A weekly report', namingStyle: 'Short names' }
      }
    ]
  ])
  expect(improvementInput.jobs).toBe('Research')
  await expect(
    client.improve({ ...improvementSetup, inputImprovementVersion: undefined }, improvementInput)
  ).resolves.toEqual(suggestion)
  expect(request).toHaveBeenCalledTimes(2)
})

it.each([
  null,
  [],
  { ...suggestion, suggestion: ' ' },
  { ...suggestion, suggestion: 'x'.repeat(4001) },
  { ...suggestion, suggestion: 'Hidden\u0000text' },
  { ...suggestion, assumptions: null },
  { ...suggestion, assumptions: [''] },
  { ...suggestion, assumptions: ['x'.repeat(301)] },
  { ...suggestion, assumptions: Array(6).fill('assumption') }
])('rejects malformed or mismatched input-improvement responses %#', async response => {
  const { client } = improvementClient({ text: JSON.stringify(response) })
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
  await expect(client.setup('organization')).rejects.toThrow('invalid_allowance')
})

it('retains authoritative shared allowance on suggestions without counting attempts locally', async () => {
  const generationAllowance = { status: 'exhausted', remainingCalls: 0 }
  const { client } = improvementClient({ ...suggestion, generationAllowance })
  await expect(client.setup('organization')).resolves.toEqual({ ...suggestion, generationAllowance })
})

it('accepts explicitly versioned split allowance while preserving operation-specific counters', async () => {
  const generationAllowance = {
    policyVersion: 2,
    status: 'available',
    remainingCalls: 7,
    remainingByOperation: { improveTeamInput: 4, generateTeam: 3 }
  }

  const { client } = improvementClient({ ...suggestion, generationAllowance })
  await expect(client.setup('organization')).resolves.toEqual({ ...suggestion, generationAllowance })
})

it.each([
  { policyVersion: 2, status: 'available', remainingCalls: 7 },
  {
    policyVersion: 3,
    status: 'available',
    remainingCalls: 7,
    remainingByOperation: { improveTeamInput: 4, generateTeam: 3 }
  },
  {
    policyVersion: 2,
    status: 'available',
    remainingCalls: 7,
    remainingByOperation: { improveTeamInput: 8, generateTeam: 3 }
  },
  {
    policyVersion: 2,
    status: 'available',
    remainingCalls: 7,
    remainingByOperation: { improveTeamInput: true, generateTeam: 3 }
  },
  {
    policyVersion: 2,
    status: 'available',
    remainingCalls: 7,
    remainingByOperation: { improveTeamInput: 4, generateTeam: 3, legacy: 0 }
  },
  { status: 'available', remainingCalls: 3, remainingByOperation: { improveTeamInput: 3, generateTeam: 3 } }
])('rejects malformed split allowance %#', async generationAllowance => {
  const { client } = improvementClient({ ...suggestion, generationAllowance })
  await expect(client.setup('organization')).rejects.toThrow('invalid_allowance')
})

const savedRequestSetup = { ...improvementSetup, sourceRevision: 'a'.repeat(64) }

const savedRequestInput = {
  jobs: 'Research',
  goals: 'A weekly report',
  namingStyle: 'Short names',
  operation: 'improve_jobs' as const
}

const savedRequest = {
  version: 1 as const,
  disposition: 'reconcile_unsent' as const,
  idempotencyKey: 'original-key',
  operation: savedRequestInput.operation,
  sourceRevision: savedRequestSetup.sourceRevision!
}

it('keeps exact-input discovery read-only and confirms only the explicit local check with the original key', async () => {
  const { client, request } = improvementClient({ pending: savedRequest, scope: {} })
  await expect(client.inspectSubscriptionRequest(savedRequestSetup, savedRequestInput)).resolves.toEqual(savedRequest)
  expect(request.mock.calls[0]).toEqual([
    'organization.inspectSetupSubscriptionRequest',
    {
      expectedOrganizationId: savedRequestSetup.organizationId,
      sourceRevision: savedRequestSetup.sourceRevision,
      ...savedRequestInput
    }
  ])
  request.mockResolvedValueOnce({ recovery: { ...savedRequest, disposition: 'retry_unsent' }, scope: {} })
  const original = { ...savedRequestInput, idempotencyKey: savedRequest.idempotencyKey }
  await expect(client.reconcileSubscriptionRequest(savedRequestSetup, original)).resolves.toEqual({
    ...savedRequest,
    disposition: 'retry_unsent'
  })
  expect(request.mock.calls[1]).toEqual([
    'organization.reconcileSetupSubscriptionRequest',
    {
      expectedOrganizationId: savedRequestSetup.organizationId,
      sourceRevision: savedRequestSetup.sourceRevision,
      ...original,
      confirmed: true
    }
  ])
  request.mockResolvedValueOnce({ pending: null })
  await expect(client.inspectSubscriptionRequest(savedRequestSetup, savedRequestInput)).resolves.toBeNull()
  expect(request).toHaveBeenCalledTimes(3)
})

it.each(['shape', 'version', 'disposition', 'key', 'oversized_key', 'operation', 'source', 'extra'])(
  'rejects %s discovery/check metadata rather than authorizing a new request',
  async kind => {
    const pending: Record<string, unknown> = { ...savedRequest }

    if (kind === 'version') {
      pending.version = 2
    }

    if (kind === 'disposition') {
      pending.disposition = 'automatic_retry'
    }

    if (kind === 'key') {
      pending.idempotencyKey = ''
    }

    if (kind === 'oversized_key') {
      pending.idempotencyKey = 'x'.repeat(129)
    }

    if (kind === 'operation') {
      pending.operation = 'improve_goals'
    }

    if (kind === 'source') {
      pending.sourceRevision = 'b'.repeat(64)
    }

    if (kind === 'extra') {
      pending.dispatched = false
    }

    const { client, request } = improvementClient(kind === 'shape' ? [] : { pending })
    await expect(client.inspectSubscriptionRequest(savedRequestSetup, savedRequestInput)).rejects.toThrow(
      'invalid_pending_request'
    )
    const recovery = { ...pending, disposition: kind === 'disposition' ? 'reconcile_unsent' : 'retry_unsent' }
    request.mockResolvedValueOnce(kind === 'shape' ? [] : { recovery })
    await expect(
      client.reconcileSubscriptionRequest(savedRequestSetup, {
        ...savedRequestInput,
        idempotencyKey: savedRequest.idempotencyKey
      })
    ).rejects.toThrow('invalid_reconciled_request')
    expect(request).toHaveBeenCalledTimes(2)
  }
)

it.each(['resolve', 'reject'] as const)('fences a late local check %s after owner change', async outcome => {
  let resolve!: (value: unknown) => void, reject!: (failure: unknown) => void
  let key = 'owner-a'

  let changed = () => {}

  const request = vi.fn(
    () =>
      new Promise((yes, no) => {
        resolve = yes
        reject = no
      })
  )

  const client = createTeamOnboardingClient(
    () => ({ key, ownerKey: key, profile: 'default', eligible: true, request: request as never }),
    listener => {
      changed = listener

      return () => {}
    }
  )

  const check = client.reconcileSubscriptionRequest(savedRequestSetup, {
    ...savedRequestInput,
    idempotencyKey: savedRequest.idempotencyKey
  })

  key = 'owner-b'
  changed()

  if (outcome === 'resolve') {
    resolve({ recovery: { ...savedRequest, disposition: 'retry_unsent' } })
  } else {
    reject({ code: 5076, data: { recovery: { ...savedRequest, disposition: 'retry_unsent' } } })
  }

  await expect(check).rejects.toThrow('stale')
  expect(request).toHaveBeenCalledTimes(1)
})

it('pins the selected live session and discards its result after session selection changes', async () => {
  let selected: string | null = 'live-session'
  let finish!: (value: unknown) => void

  const request = vi.fn(
    () =>
      new Promise(resolve => {
        finish = resolve
      })
  )

  const client = createTeamOnboardingClient(
    () => ({ key: 'A', ownerKey: 'owner', profile: 'default', eligible: true, request: request as never }),
    () => () => {},
    () => selected
  )

  const pending = client.improve(improvementSetup, improvementInput)
  expect(request).toHaveBeenCalledWith('llm.oneshot', expect.objectContaining({ session_id: 'live-session' }))
  selected = null
  finish({ text: JSON.stringify(suggestion) })
  await expect(pending).rejects.toThrow('stale')
  expect(request).toHaveBeenCalledTimes(1)
})

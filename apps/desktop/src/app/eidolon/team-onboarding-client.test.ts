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

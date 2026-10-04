import { expect, it } from 'vitest'

import { createPrototypeAdapter } from './adapter'

it.each([-1, 101, NaN, Infinity])('rejects invalid progress %s atomically on create and update', progress => {
  const adapter = createPrototypeAdapter()
  const objective = adapter.createObjective('Scope')
  const before = adapter.getSnapshot()
  expect(() => adapter.createObjective('Invalid', { progress })).toThrow(/Progress/)
  expect(() => adapter.updateObjectiveMetadata(objective.id, { progress })).toThrow(/Progress/)
  expect(adapter.getSnapshot()).toBe(before)
})

it('preserves identity, clears estimates to unknown and rejects missing objectives', () => {
  const adapter = createPrototypeAdapter()
  const objective = adapter.createObjective('Scope', { progress: 0 })
  adapter.updateObjectiveMetadata(objective.id, { progress: undefined, autonomyIntent: '' })
  expect(adapter.getSnapshot().objectives[0]).toMatchObject({ id: objective.id, status: 'planning', progress: undefined })
  expect(() => adapter.updateObjectiveMetadata('missing', {})).toThrow(/not found/)
})

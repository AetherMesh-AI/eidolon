import { expect, it } from 'vitest'
import { sourceRuntimeCapability, sourceCapabilityText } from './organization-source-capability'

const capability = {
  version: 1,
  mode: 'scheduler-scoped-organization',
  requestTypes: ['work.inspect', 'work.edit'],
  provider: 'openai-api',
  credentialScope: 'profile-local',
  grantsIncluded: false,
  host: { sourceReading: 'supported', projectValidation: 'requires-sandbox' }
}
it.each([
  undefined,
  null,
  1,
  'old',
  {},
  { ...capability, version: 9 },
  { ...capability, host: null },
  { ...capability, requestTypes: ['work.inspect'] },
  { ...capability, provider: 'codex' },
  { ...capability, grantsIncluded: true }
])('rejects unknown or malformed source support %j without restricting text work', value => {
  expect(sourceRuntimeCapability(value)).toBeNull()
  expect(sourceCapabilityText(value)).toContain('unknown or unavailable')
  expect(sourceCapabilityText(value)).toContain('does not describe text-only')
})
it.each(['none', 'revoked'])('support never asserts effective grants: %s', grants => {
  const value = { ...capability, effectiveGrants: grants }
  expect(sourceRuntimeCapability(value)).toEqual(value)
  expect(sourceCapabilityText(value)).toContain('Explicit folder and tool grants required')
  expect(sourceCapabilityText(value)).toContain('not a readiness or permission check')
  expect(sourceCapabilityText(value)).toContain('readiness has not been checked')
})
it('discloses Windows source refusal and Mac validation limitation separately', () => {
  const windows = { ...capability, host: { sourceReading: 'unsupported', projectValidation: 'unsupported' } }
  expect(sourceCapabilityText(windows)).toContain('Source reading is unsupported')
  expect(sourceCapabilityText(windows)).toContain('validation and integration are unsupported')
  const mac = { ...capability, host: { sourceReading: 'supported', projectValidation: 'unsupported' } }
  expect(sourceCapabilityText(mac)).not.toContain('Source reading is unsupported')
  expect(sourceCapabilityText(mac)).toContain('validation and integration are unsupported')
})

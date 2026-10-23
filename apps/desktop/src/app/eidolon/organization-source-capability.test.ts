import { expect, it } from 'vitest'

import { organizationCreationCopy } from '@/i18n/organization-creation'

import { sourceCapabilityText, sourceRuntimeCapability } from './organization-source-capability'

const copy = organizationCreationCopy.en.sourceCapability

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
  expect(sourceCapabilityText(value, copy)).toContain('unknown or unavailable')
  expect(sourceCapabilityText(value, copy)).toContain('does not describe text-only')
})
it.each(['none', 'revoked'])('support never asserts effective grants: %s', grants => {
  const value = { ...capability, effectiveGrants: grants }
  expect(sourceRuntimeCapability(value)).toEqual(value)
  expect(sourceCapabilityText(value, copy)).toContain('Explicit folder and tool grants are required')
  expect(sourceCapabilityText(value, copy)).toContain('does not confirm permission or readiness')
  expect(sourceCapabilityText(value, copy)).toContain('readiness has not been checked')
})
it('discloses Windows source refusal and Mac validation limitation separately', () => {
  const windows = { ...capability, host: { sourceReading: 'unsupported', projectValidation: 'unsupported' } }
  expect(sourceCapabilityText(windows, copy)).toContain('Source reading is unsupported')
  expect(sourceCapabilityText(windows, copy)).toContain('validation and integration are unsupported')
  const mac = { ...capability, host: { sourceReading: 'supported', projectValidation: 'unsupported' } }
  expect(sourceCapabilityText(mac, copy)).not.toContain('Source reading is unsupported')
  expect(sourceCapabilityText(mac, copy)).toContain('validation and integration are unsupported')
})

it.each(Object.entries(organizationCreationCopy))(
  'uses the %s locale and leads with Windows refusal',
  (_locale, translation) => {
    const copy = translation.sourceCapability
    expect(sourceCapabilityText(undefined, copy)).toBe(copy.unknown)
    expect(sourceCapabilityText(capability, copy)).toBe(
      [copy.available, copy.validationRequired, copy.grants].join(' ')
    )
    const windows = { ...capability, host: { sourceReading: 'unsupported', projectValidation: 'unsupported' } }
    expect(sourceCapabilityText(windows, copy)).toBe(
      [copy.sourceUnsupported, copy.validationUnsupported, copy.grants].join(' ')
    )
    expect(sourceCapabilityText(windows, copy)).not.toContain(copy.available)
  }
)

it.each(Object.entries(organizationCreationCopy))(
  'discloses the v2 subscription output contract in %s',
  (_locale, translation) => {
    const current = {
      ...capability,
      version: 2,
      providers: ['openai-api', 'openai-codex'],
      outputContracts: { 'openai-api': 'provider-cap', 'openai-codex': 'subscription-reservation' }
    }

    expect(sourceRuntimeCapability(current)).toEqual(current)
    expect(sourceCapabilityText(current, translation.sourceCapability)).toContain(
      translation.sourceCapability.subscriptionLimitation
    )
    expect(sourceRuntimeCapability({ ...current, outputContracts: { 'openai-codex': 'provider-cap' } })).toBeNull()
  }
)

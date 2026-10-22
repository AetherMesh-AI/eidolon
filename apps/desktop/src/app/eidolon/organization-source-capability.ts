import type { SourceCapabilityCopy } from '@/i18n/organization-creation'

/** Presentation only. Backend admission owns every grant and execution decision. */
export interface SourceRuntimeCapability {
  version: 1
  mode: 'scheduler-scoped-organization'
  /** Supported source assignments, not an exhaustive list of organization work. */
  requestTypes: ['work.inspect', 'work.edit']
  provider: 'openai-api'
  credentialScope: 'profile-local'
  grantsIncluded: false
  host: {
    sourceReading: 'supported' | 'unsupported'
    projectValidation: 'requires-sandbox' | 'unsupported'
  }
}

export function sourceRuntimeCapability(value: unknown): SourceRuntimeCapability | null {
  const item = value as SourceRuntimeCapability | null
  if (
    !item ||
    item.version !== 1 ||
    item.mode !== 'scheduler-scoped-organization' ||
    !Array.isArray(item.requestTypes) ||
    item.requestTypes.length !== 2 ||
    item.requestTypes[0] !== 'work.inspect' ||
    item.requestTypes[1] !== 'work.edit' ||
    item.provider !== 'openai-api' ||
    item.credentialScope !== 'profile-local' ||
    item.grantsIncluded !== false ||
    !['supported', 'unsupported'].includes(item.host?.sourceReading) ||
    !['requires-sandbox', 'unsupported'].includes(item.host?.projectValidation)
  ) {
    return null
  }
  return item
}

export function sourceCapabilityText(value: unknown, copy: SourceCapabilityCopy): string {
  const capability = sourceRuntimeCapability(value)
  if (!capability) {
    return copy.unknown
  }
  return [
    capability.host.sourceReading === 'unsupported' ? copy.sourceUnsupported : copy.available,
    capability.host.projectValidation === 'unsupported' ? copy.validationUnsupported : copy.validationRequired,
    copy.grants
  ].join(' ')
}

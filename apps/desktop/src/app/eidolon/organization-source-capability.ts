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

export function sourceCapabilityText(value: unknown): string {
  const capability = sourceRuntimeCapability(value)
  if (!capability) {
    return 'Current source-work capability is unknown or unavailable. This does not describe text-only organization work.'
  }
  return (
    'Scoped source work supported. Explicit folder and tool grants required. ' +
    'This is not a readiness or permission check; backend admission checks current grants for every assignment. ' +
    (capability.host.sourceReading === 'unsupported' ? 'Source reading is unsupported on this host. ' : '') +
    (capability.host.projectValidation === 'unsupported'
      ? 'Successful project validation and integration are unsupported on this host.'
      : 'Project validation and integration require the supported sandbox and project dependencies; readiness has not been checked.')
  )
}

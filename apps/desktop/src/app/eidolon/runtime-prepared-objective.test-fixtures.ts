import { preparedDescriptor } from './runtime-prepared-validation.test-fixtures'

export function availableDescriptor() {
  const descriptor = preparedDescriptor()
  descriptor.projects[0] = { ...descriptor.projects[0], enabled: true, registrationRevision: 2, registrationHash: 'c'.repeat(64) }
  descriptor.registrationRefs = [{ projectId: 'desktop', revision: 2, registrationSha256: 'c'.repeat(64) }]

  return descriptor
}

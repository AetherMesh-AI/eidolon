import { preparedHash, preparedInteger, preparedRecord } from './runtime-prepared-validation-contract'
import type { ObjectiveMetadata, PreparedValidationReference } from './types'

/** Shape/intent validation only; current owner registration authority is checked by the backend. */
export function validatePreparedObjectiveMetadata(metadata: ObjectiveMetadata): PreparedValidationReference[] | undefined {
  const references = metadata.preparedValidations

  if (references === undefined || (Array.isArray(references) && references.length === 0)) {
    return undefined
  }

  if (!Array.isArray(references) || references.length > 8 || !references.every(reference => preparedRecord(reference) &&
    Object.keys(reference).sort().join(',') === 'projectId,registrationSha256,revision' &&
    typeof reference.projectId === 'string' && /^[a-z][a-z0-9_-]{0,63}$/.test(reference.projectId) &&
    preparedInteger(reference.revision, 1) && preparedHash(reference.registrationSha256)) ||
    new Set(references.map(reference => reference.projectId)).size !== references.length) {
    throw new Error('Prepared validation requires at most eight unique exact project, revision and hash references.')
  }

  const projects = metadata.projectIds

  if (!Array.isArray(projects) || projects.length !== references.length || new Set(projects).size !== projects.length ||
    !references.every(reference => projects.includes(reference.projectId))) {
    throw new Error('Explicitly select a current prepared validation registration for every selected project.')
  }

  const checks = metadata.requiredChecks

  if (metadata.deliveryMode !== 'managed_artifact' || !Array.isArray(checks) ||
    !checks.includes('project_typecheck') || !checks.includes('project_tests') ||
    checks.some(check => !['project_typecheck', 'project_tests', 'project_build', 'managed_validation'].includes(check))) {
    throw new Error('Prepared validation requires managed artifact delivery and TypeScript and UI test checks, plus renderer build for v2 registrations, without source integration.')
  }

  return references.map(reference => ({ ...reference })).sort((left, right) => left.projectId < right.projectId ? -1 : left.projectId > right.projectId ? 1 : 0)
}

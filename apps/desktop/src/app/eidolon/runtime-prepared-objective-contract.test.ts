import { describe, expect, it } from 'vitest'

import { validatePreparedObjectiveMetadata } from './runtime-prepared-objective-contract'
import type { ObjectiveMetadata } from './types'

export function preparedObjectiveMetadata(): ObjectiveMetadata {
  return { projectIds: ['desktop', 'library'], deliveryMode: 'managed_artifact', requiredChecks: ['project_typecheck', 'project_tests'],
    preparedValidations: [{ projectId: 'library', revision: 2, registrationSha256: 'b'.repeat(64) },
      { projectId: 'desktop', revision: 1, registrationSha256: 'a'.repeat(64) }] }
}

describe('prepared objective explicit intent shape', () => {
  it('leaves omitted and empty legacy selections outside the prepared path', () => {
    expect(validatePreparedObjectiveMetadata({ deliveryMode: 'source_project' })).toBeUndefined()
    expect(validatePreparedObjectiveMetadata({ preparedValidations: [] })).toBeUndefined()
  })
  it('copies exact selected references and canonicalizes only their order', () => {
    const metadata = preparedObjectiveMetadata()
    const references = validatePreparedObjectiveMetadata(metadata)!
    expect(references).toEqual([...metadata.preparedValidations!].reverse())
    metadata.preparedValidations![1].revision = 7
    expect(references[0].revision).toBe(1)
  })
  it.each(['non_array', 'extra', 'duplicate', 'missing', 'unknown', 'invalid_id', 'boolean_revision', 'bad_hash', 'capacity', 'duplicate_projects',
    'source_delivery', 'missing_types', 'missing_tests', 'source_integration', 'unknown_check'])('rejects %s without inferring authority', change => {
    const metadata = preparedObjectiveMetadata()
    const references = metadata.preparedValidations!

    if (change === 'non_array') { Object.assign(metadata, { preparedValidations: null }) }

    if (change === 'extra') { Object.assign(references[0], { command: 'npm install' }) }

    if (change === 'duplicate') { references.push({ ...references[0] }) }

    if (change === 'missing') { references.pop() }

    if (change === 'unknown') { references[0].projectId = 'other' }

    if (change === 'invalid_id') { references[0].projectId = 'UPPER' }

    if (change === 'boolean_revision') { Object.assign(references[0], { revision: true }) }

    if (change === 'bad_hash') { references[0].registrationSha256 = 'not-a-hash' }

    if (change === 'capacity') { metadata.preparedValidations = Array.from({ length: 9 }, (_, index) => ({ ...references[0], projectId: `project${index}` })) }

    if (change === 'duplicate_projects') { metadata.projectIds!.push('desktop') }

    if (change === 'source_delivery') { metadata.deliveryMode = 'source_project' }

    if (change === 'missing_types') { metadata.requiredChecks = ['project_tests'] }

    if (change === 'missing_tests') { metadata.requiredChecks = ['project_typecheck'] }

    if (change === 'source_integration') { metadata.requiredChecks!.push('source_integration') }

    if (change === 'unknown_check') { Object.assign(metadata, { requiredChecks: ['project_typecheck', 'project_tests', 'arbitrary_command'] }) }
    expect(() => validatePreparedObjectiveMetadata(metadata)).toThrow()
  })
})

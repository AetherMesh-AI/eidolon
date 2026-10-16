import { policyEqual } from './organization-owner-policy-contract'
import { preparedHash, preparedRecord, validatePreparedDescriptor, validPreparedCandidate, validPreparedSelection } from './runtime-prepared-validation-contract'
import {
  type PreparedValidationDescriptor,
  PreparedValidationError,
  type PreparedValidationIntent,
  type PreparedValidationPreview,
  type PreparedValidationReceipt,
  type PreparedValidationRevokeIntent,
  type PreparedValidationRevokeReceipt,
  type PreparedValidationScope,
  type PreparedValidationSelection
} from './runtime-prepared-validation-types'

/** A single captured owner socket; reads cannot silently retarget an existing review. */
export function createPreparedValidationClient(source: () => PreparedValidationScope, newKey: () => string = () => crypto.randomUUID()) {
  let captured: PreparedValidationScope | null = null
  let descriptor: PreparedValidationDescriptor | null = null
  let reviewed: PreparedValidationPreview | null = null
  let intent: PreparedValidationIntent | null = null
  let revocation: PreparedValidationRevokeIntent | null = null
  let busy = false
  let editRevision = 0
  const identity = (scope: PreparedValidationScope) => [scope.key, scope.ownerKey, scope.profile, scope.eligible, scope.organizationId, scope.ownerId]
  const current = () => !!captured && captured.eligible && policyEqual(identity(captured), identity(source()))

  const requireCurrent = () => { if (!current()) { throw new PreparedValidationError('changed') } }

  const requestIdentity = () => ({ expectedOrganizationId: descriptor!.organizationId, expectedOwnerId: descriptor!.ownerId,
    expectedGeneration: descriptor!.policyGeneration, sourceRevision: descriptor!.sourceRevision, expectedSidecarRevision: descriptor!.sidecarRevision })

  return {
    current,
    hasRetainedIntent: () => !!intent || !!revocation,
    invalidatePreview() {
      if (intent || revocation) { throw new PreparedValidationError('pending') }
      editRevision++; reviewed = null
    },
    async read() {
      if (busy || intent || revocation) { throw new PreparedValidationError('pending') }

      if (captured) { requireCurrent() }
      captured = { ...source() }

      if (!captured.eligible || !captured.organizationId || !captured.ownerId) { throw new PreparedValidationError('unavailable') }
      descriptor = null; reviewed = null; editRevision++
      busy = true

      try {
        const result = await captured.request('organization.preparedValidationSetup', { expectedOrganizationId: captured.organizationId })
        requireCurrent()
        descriptor = structuredClone(validatePreparedDescriptor(result, captured))

        return structuredClone(descriptor)
      } finally { busy = false }
    },
    async preview(selection: PreparedValidationSelection) {
      requireCurrent()

      if (busy || intent || revocation) { throw new PreparedValidationError('pending') }

      if (!descriptor || descriptor.blockers.length || !descriptor.platform.supported) { throw new PreparedValidationError('blocked') }

      if (!validPreparedSelection(selection) || !descriptor.projects.some(project => project.id === selection.projectId && !project.rootRevoked)) { throw new PreparedValidationError('input') }
      const selected = structuredClone(selection)
      const revision = ++editRevision
      reviewed = null; busy = true

      try {
        const result = await captured!.request('organization.previewPreparedValidation', { ...requestIdentity(), selection: structuredClone(selected) })
        requireCurrent()

        if (revision !== editRevision) { throw new PreparedValidationError('edited') }
        const value = validatePreparedDescriptor(result, captured!) as PreparedValidationPreview

        if (!policyEqual({ ...value, candidate: undefined, candidateHash: undefined, previewVersion: undefined },
          { ...descriptor, candidate: undefined, candidateHash: undefined, previewVersion: undefined }) ||
          !preparedHash(value.candidateHash) || !preparedHash(value.previewVersion) || !validPreparedCandidate(value.candidate, selected, descriptor)) { throw new PreparedValidationError('invalid') }

        reviewed = structuredClone(value)

        return structuredClone(reviewed)
      } finally { busy = false }
    },
    async save(confirmed: boolean): Promise<PreparedValidationReceipt> {
      requireCurrent()

      if (!confirmed || !descriptor || !reviewed) { throw new PreparedValidationError('confirm') }

      if (busy || revocation) { throw new PreparedValidationError('pending') }
      intent ??= structuredClone({ ...requestIdentity(), candidate: reviewed.candidate, candidateHash: reviewed.candidateHash,
        previewVersion: reviewed.previewVersion, idempotencyKey: newKey(), confirmed: true })
      busy = true

      try {
        // The transport and the caller never receive the retained object itself.
        const result = await captured!.request('organization.savePreparedValidation', structuredClone(intent) as unknown as Record<string, unknown>)
        requireCurrent()

        if (!preparedRecord(result) || result.capabilityVersion !== 1 || !policyEqual(result.scope, descriptor.scope) || !preparedRecord(result.receipt)) { throw new PreparedValidationError('uncertain') }
        const receipt = result.receipt

        if (receipt.organizationId !== descriptor.organizationId || receipt.ownerId !== descriptor.ownerId || receipt.policyGeneration !== descriptor.policyGeneration ||
          receipt.sourceRevision !== descriptor.sourceRevision || receipt.previousSidecarRevision !== descriptor.sidecarRevision || receipt.sidecarRevision !== descriptor.sidecarRevision + 1 ||
          receipt.projectId !== intent.candidate.contract.projectId || receipt.registrationRevision !== intent.candidate.contract.revision ||
          receipt.candidateHash !== intent.candidateHash || receipt.previewVersion !== intent.previewVersion || receipt.idempotencyKey !== intent.idempotencyKey ||
          !policyEqual(receipt.candidate, intent.candidate)) { throw new PreparedValidationError('uncertain') }

        intent = null; reviewed = null

        return structuredClone(receipt) as unknown as PreparedValidationReceipt
      } finally { busy = false }
    },
    async revoke(projectId: string, confirmed: boolean): Promise<PreparedValidationRevokeReceipt> {
      requireCurrent()

      if (busy || intent) { throw new PreparedValidationError('pending') }
      const project = descriptor?.projects.find(item => item.id === projectId)

      if (!confirmed || !descriptor || !project?.enabled || !project.registrationHash) { throw new PreparedValidationError('confirm') }

      if (revocation && revocation.projectId !== projectId) { throw new PreparedValidationError('pending') }
      revocation ??= { expectedOrganizationId: descriptor.organizationId, expectedOwnerId: descriptor.ownerId,
        expectedGeneration: descriptor.policyGeneration, expectedSidecarRevision: descriptor.sidecarRevision,
        projectId, registrationRevision: project.registrationRevision, registrationHash: project.registrationHash,
        idempotencyKey: newKey(), confirmed: true }
      reviewed = null; editRevision++; busy = true

      try {
        const result = await captured!.request('organization.revokePreparedValidation', structuredClone(revocation) as unknown as Record<string, unknown>)
        requireCurrent()

        if (!preparedRecord(result) || result.capabilityVersion !== 1 || !policyEqual(result.scope, descriptor.scope) || !preparedRecord(result.receipt)) { throw new PreparedValidationError('uncertain') }
        const receipt = result.receipt

        if (receipt.organizationId !== descriptor.organizationId || receipt.ownerId !== descriptor.ownerId || receipt.policyGeneration !== descriptor.policyGeneration ||
          receipt.previousSidecarRevision !== descriptor.sidecarRevision || receipt.sidecarRevision !== descriptor.sidecarRevision + 1 ||
          receipt.projectId !== revocation.projectId || receipt.previousRegistrationRevision !== revocation.registrationRevision ||
          receipt.registrationRevision !== revocation.registrationRevision + 1 || receipt.registrationHash !== revocation.registrationHash ||
          receipt.idempotencyKey !== revocation.idempotencyKey || receipt.disabled !== true) { throw new PreparedValidationError('uncertain') }

        revocation = null

        return structuredClone(receipt) as unknown as PreparedValidationRevokeReceipt
      } finally { busy = false }
    }
  }
}

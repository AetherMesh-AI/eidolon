import type { OwnerPolicyScope } from './organization-owner-policy-types'

export interface PreparedValidationLimits {
  max_files: number
  max_file_bytes: number
  max_total_bytes: number
  max_path_bytes: number
  max_depth: number
}
export interface PreparedValidationSelection {
  projectId: string
  enabled: boolean
  source: { files: string[]; limits: PreparedValidationLimits; executionOnlyHashes: Record<string, string> }
  dependencies: { directory: string; manifest: Record<string, unknown> }
  profile: Record<string, unknown>
}
export interface PreparedValidationProject {
  id: string
  root: string
  recipe: string | null
  team: string
  registrationRevision: number
  registrationHash: string | null
  rootRevoked: boolean
  enabled: boolean
  preparedProfileVersion?: 1 | 2
}
export interface PreparedValidationDescriptor {
  capabilityVersion: 1
  organizationId: string
  ownerId: string
  policyGeneration: number
  sourceRevision: string
  sidecarRevision: number
  projects: PreparedValidationProject[]
  registrationRefs: { projectId: string; revision: number; registrationSha256: string }[]
  blockers: string[]
  platform: { supported: boolean; system: string; architecture: string; backend: 'linux-docker-pinned'; runtimeReadiness: 'not_probed' }
  scope: { kind: 'served-profile'; profile: string }
}
export interface PreparedValidationContract {
  version: 1 | 2
  contract: 'prepared-validation-v1' | 'prepared-validation-v2'
  organizationId: string
  ownerId: string
  projectId: string
  projectBindingSha256: string
  revision: number
  root: { alias: string; device: number; inode: number }
  source: PreparedValidationSelection['source']
  dependencies: PreparedValidationSelection['dependencies'] & {
    device: number
    inode: number
    bundleSha256: string
    inventorySha256: string
    lockfileSha256: string
    toolchain: Record<string, unknown>
  }
  profile: PreparedValidationSelection['profile']
  runtime: { backend: 'linux-docker-pinned'; image: string; allowLocalDocker: true; network: 'none'; credentials: 'none' }
}
export interface PreparedValidationCandidate {
  enabled: boolean
  contract: PreparedValidationContract
  verification: {
    state: 'verified_bytes_runtime_unprobed'
    snapshotSha256: string
    sourceFiles: number
    sourceBytes: number
    dependencyFiles: number
    dependencyBytes: number
    stagingCleanupConfirmed: true
  }
  selection: PreparedValidationSelection
}
export interface PreparedValidationPreview extends PreparedValidationDescriptor {
  candidate: PreparedValidationCandidate
  candidateHash: string
  previewVersion: string
}
export interface PreparedValidationRequest {
  expectedOrganizationId: string
  expectedOwnerId: string
  expectedGeneration: number
  sourceRevision: string
  expectedSidecarRevision: number
}
export interface PreparedValidationIntent extends PreparedValidationRequest {
  candidate: PreparedValidationCandidate
  candidateHash: string
  previewVersion: string
  idempotencyKey: string
  confirmed: true
}
export interface PreparedValidationReceipt {
  organizationId: string
  ownerId: string
  policyGeneration: number
  sourceRevision: string
  previousSidecarRevision: number
  sidecarRevision: number
  projectId: string
  registrationRevision: number
  candidateHash: string
  previewVersion: string
  idempotencyKey: string
  candidate: PreparedValidationCandidate
}
export interface PreparedValidationRevokeIntent {
  expectedOrganizationId: string
  expectedOwnerId: string
  expectedGeneration: number
  expectedSidecarRevision: number
  projectId: string
  registrationRevision: number
  registrationHash: string
  idempotencyKey: string
  confirmed: true
}
export interface PreparedValidationRevokeReceipt {
  organizationId: string
  ownerId: string
  policyGeneration: number
  previousSidecarRevision: number
  sidecarRevision: number
  projectId: string
  registrationRevision: number
  previousRegistrationRevision: number
  registrationHash: string
  idempotencyKey: string
  disabled: true
}
export type PreparedValidationScope = OwnerPolicyScope
export type PreparedValidationErrorCode = 'changed' | 'unavailable' | 'invalid' | 'pending' | 'confirm' | 'blocked' | 'edited' | 'uncertain' | 'input'
export class PreparedValidationError extends Error {
  constructor(public code: PreparedValidationErrorCode) { super(code) }
}

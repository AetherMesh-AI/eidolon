import type { MetadataScope } from './organization-metadata-client'

export type CredentialAction = 'create' | 'edit_metadata' | 'replace_value' | 'revoke' | 'remove' | 'configure_grant'
export type CredentialStatus = 'missing' | 'available' | 'expired' | 'revoked' | 'locked' | 'unavailable'
export interface CredentialMetadata {
  service: string
  label: string
  type: string
  description: string
  kind: 'secret' | 'config'
  expiresAt: number | null
}
export interface CredentialEntry extends CredentialMetadata {
  id: string
  organizationId: string
  name: string
  revision: number
  version: number
  status: CredentialStatus
  createdAt: number
  updatedAt: number
  updatedBy: string
  permissions: string[]
  inFlight: number
}
export interface CredentialGrant {
  actor: string
  scope: string
  revision: number
  permissions: string[]
  bindings: { tool: string; destination: string }[]
}
export interface CredentialCatalog {
  version: 1
  organizationId: string
  backend: { provider: string; state: 'available' | 'locked' | 'unavailable' }
  entries: CredentialEntry[]
  agents: { id: string; identityId: string; name: string }[]
  grants: CredentialGrant[]
  audit: unknown[]
  tombstones: unknown[]
  canCreate: boolean
  environmentInjection: false
  providerCredentialCreation: false
  secureInputAvailable: boolean
}
export interface CredentialScope extends MetadataScope {
  organizationId: string | null
  ownerId: string | null
}
export interface CredentialIntent {
  expectedOrganizationId: string
  expectedOwnerId: string
  action: CredentialAction
  entryId: string | null
  expectedRevision: number | null
  operationId: string
  metadata: CredentialMetadata | null
  actorId?: string
  permissions?: string[]
}
export interface CredentialReceipt {
  operationId: string
  action: CredentialAction
  entryId?: string
  actorId?: string
  scope?: string
  revision: number
  version?: number
  inFlight?: number
  queuedCancelled?: number
  providerRevoked?: false
  storedValueDeleted?: false
}
export type CredentialError = 'unavailable' | 'unsupported' | 'invalid' | 'conflict' | 'uncertain' | 'secureInput' | 'storage'
export interface CredentialView {
  catalog: CredentialCatalog | null
  intent: CredentialIntent | null
  receipt: CredentialReceipt | null
  cancelledOperationId: string | null
  busy: boolean
  error: CredentialError | null
}

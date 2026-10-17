import type { MetadataDescriptor, OrganizationMetadata } from './organization-metadata-client'

export interface WorkspaceCreationIntent {
  capabilityVersion: 1
  expectedOrganizationId: string
  expectedOwnerId: string
  displayName: string
  ownerDisplayName: string
  idempotencyKey: string
}
export interface WorkspaceCreationCapability {
  capabilityVersion: 1
  state: 'available'
  isolationVersion: 1
  credentialMode: 'profile-local-openai-api'
  executionMode: 'tool-free-organization'
  activation: 'explicit-client'
  supportedProviders: ['openai-api']
  origin: { profile: string; organizationId: string; ownerId: string }
}
export interface CreatedWorkspace {
  capabilityVersion: 1
  state: 'created'
  idempotencyKey: string
  profile: string
  organizationId: string
  ownerId: string
  isolationVersion: 1
  credentialMode: 'profile-local-openai-api'
  executionMode: 'tool-free-organization'
  descriptor: OrganizationMetadata
  activated: false
}

const uuid = (value: unknown): value is string =>
  typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value)

export const validWorkspaceName = (value: string) =>
  Boolean(value.trim()) && [...value.trim()].length <= 160 && !/\p{C}/u.test(value)
export const validWorkspaceModel = (value: string) => /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$/.test(value.trim())

const mode = (value: { isolationVersion?: unknown; credentialMode?: unknown; executionMode?: unknown }) =>
  value.isolationVersion === 1 &&
  value.credentialMode === 'profile-local-openai-api' &&
  value.executionMode === 'tool-free-organization'

const providers = (value: unknown) => Array.isArray(value) && value.length === 1 && value[0] === 'openai-api'

export function creationCapability(value: unknown, profile: string): WorkspaceCreationCapability {
  const item = value as WorkspaceCreationCapability

  if (
    !item ||
    item.capabilityVersion !== 1 ||
    item.state !== 'available' ||
    !mode(item) ||
    item.activation !== 'explicit-client' ||
    !providers(item.supportedProviders) ||
    item.origin?.profile !== profile ||
    !uuid(item.origin.organizationId) ||
    !uuid(item.origin.ownerId) ||
    item.origin.organizationId === item.origin.ownerId
  ) {
    throw new Error('Unsupported organization creation capability')
  }

  return item
}

export function restoreCreationIntent(raw: string, capability: WorkspaceCreationCapability): WorkspaceCreationIntent {
  if (raw.length > 8192) {
    throw new Error('Invalid saved creation intent')
  }

  const item = JSON.parse(raw) as WorkspaceCreationIntent

  if (
    !item ||
    Object.keys(item).sort().join(',') !==
      'capabilityVersion,displayName,expectedOrganizationId,expectedOwnerId,idempotencyKey,ownerDisplayName' ||
    item.capabilityVersion !== 1 ||
    item.expectedOrganizationId !== capability.origin.organizationId ||
    item.expectedOwnerId !== capability.origin.ownerId ||
    typeof item.idempotencyKey !== 'string' ||
    !uuid(item.idempotencyKey) ||
    typeof item.displayName !== 'string' ||
    !validWorkspaceName(item.displayName) ||
    item.displayName !== item.displayName.trim() ||
    typeof item.ownerDisplayName !== 'string' ||
    !validWorkspaceName(item.ownerDisplayName) ||
    item.ownerDisplayName !== item.ownerDisplayName.trim()
  ) {
    throw new Error('Invalid saved creation intent')
  }

  return item
}

export function createdWorkspace(value: unknown, intent: WorkspaceCreationIntent): CreatedWorkspace {
  const item = value as CreatedWorkspace
  const metadata = item?.descriptor

  if (
    !item ||
    item.capabilityVersion !== 1 ||
    item.state !== 'created' ||
    !mode(item) ||
    item.activated !== false ||
    item.idempotencyKey !== intent.idempotencyKey ||
    typeof item.profile !== 'string' ||
    !/^org-[0-9a-f]{32}$/.test(item.profile) ||
    !uuid(item.organizationId) ||
    !uuid(item.ownerId) ||
    item.organizationId === item.ownerId ||
    item.organizationId === intent.expectedOrganizationId ||
    item.ownerId === intent.expectedOwnerId ||
    metadata?.organizationId !== item.organizationId ||
    metadata.owner?.ownerId !== item.ownerId ||
    metadata.displayName !== intent.displayName ||
    metadata.owner.displayName !== intent.ownerDisplayName ||
    metadata.revision !== 1 ||
    !Number.isFinite(Date.parse(metadata.createdAt)) ||
    metadata.createdAt !== metadata.updatedAt
  ) {
    throw new Error('Invalid workspace creation receipt')
  }

  return item
}

export function verifyCreatedWorkspace(
  value: unknown,
  expected: Pick<CreatedWorkspace, 'profile' | 'organizationId' | 'ownerId'>
): void {
  const item = value as MetadataDescriptor & {
    isolation?: {
      isolationVersion: number
      credentialMode: string
      executionMode: string
      profile: string
      organizationId: string
      ownerId: string
      supportedProviders: string[]
    }
  }

  const isolation = item?.isolation

  if (
    !item ||
    item.capabilityVersion !== 1 ||
    item.state !== 'ready' ||
    item.scope?.kind !== 'served-profile' ||
    item.scope.profile !== expected.profile ||
    item.metadata?.organizationId !== expected.organizationId ||
    item.metadata.owner?.ownerId !== expected.ownerId ||
    !isolation ||
    !mode(isolation) ||
    !providers(isolation.supportedProviders) ||
    isolation.profile !== expected.profile ||
    isolation.organizationId !== expected.organizationId ||
    isolation.ownerId !== expected.ownerId
  ) {
    throw new Error('Destination organization identity or isolation could not be verified')
  }
}

export function isolatedWorkspaceIdentity(value: unknown, profile: string) {
  const descriptor = value as MetadataDescriptor
  const organizationId = descriptor?.metadata?.organizationId
  const ownerId = descriptor?.metadata?.owner?.ownerId

  if (!uuid(organizationId) || !uuid(ownerId) || organizationId === ownerId) {
    throw new Error('Invalid organization identity')
  }

  const expected = { profile, organizationId, ownerId }
  verifyCreatedWorkspace(value, expected)

  return expected
}

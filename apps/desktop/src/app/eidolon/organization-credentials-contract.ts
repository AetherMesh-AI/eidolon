import type {
  CredentialCatalog,
  CredentialIntent,
  CredentialMetadata,
  CredentialReceipt,
  CredentialScope
} from './organization-credentials-types'

export const CREDENTIAL_GRANT_PERMISSIONS = ['discover', 'edit_metadata', 'replace_value', 'revoke', 'remove'] as const
export const CREDENTIAL_NAME_PART = /^[a-z][A-Za-z0-9]{0,47}$/

export function validCredentialCreateScope(scope: string) {
  const parts = scope.split(':')

  return parts.length === 5 && parts[0] === 'create' && parts.slice(1, 4).every(part => CREDENTIAL_NAME_PART.test(part)) && ['secret', 'config'].includes(parts[4])
}

export function validCredentialGrantPermissions(scope: string, permissions: string[]) {
  return new Set(permissions).size === permissions.length && (scope.startsWith('create:')
    ? validCredentialCreateScope(scope) && (permissions.length === 0 || (permissions.length === 1 && permissions[0] === 'create'))
    : permissions.every(permission => CREDENTIAL_GRANT_PERMISSIONS.includes(permission as typeof CREDENTIAL_GRANT_PERMISSIONS[number])))
}

export const credentialName = (metadata: Pick<CredentialMetadata, 'service' | 'label' | 'type'>) =>
  `${metadata.service}_${metadata.label}_${metadata.type}`

const record = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === 'object' && !Array.isArray(value))
const text = (value: unknown): value is string => typeof value === 'string' && value.length > 0 && value.length <= 500
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0
const timestamp = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0 && value < 253402300800

export function validCredentialMetadata(value: unknown): value is CredentialMetadata {
  if (!record(value)) {
    return false
  }

  return ['service', 'label', 'type'].every(key => typeof value[key] === 'string' && CREDENTIAL_NAME_PART.test(value[key])) &&
    typeof value.description === 'string' && Boolean(value.description.trim()) && [...value.description].length <= 1000 && !/\p{C}/u.test(value.description) &&
    (value.kind === 'secret' || value.kind === 'config') &&
    (value.expiresAt === null || timestamp(value.expiresAt))
}

/** Pick only catalog metadata. Unknown backend fields never enter the UI or retained intent. */
export function credentialMetadata(value: CredentialMetadata): CredentialMetadata {
  return { service: value.service, label: value.label, type: value.type, description: value.description, kind: value.kind, expiresAt: value.expiresAt }
}

export function validCredentialIntent(value: unknown, scope: CredentialScope): value is CredentialIntent {
  if (!record(value)) {
    return false
  }

  return value.expectedOrganizationId === scope.organizationId && value.expectedOwnerId === scope.ownerId &&
    text(value.operationId) && ['create', 'edit_metadata', 'replace_value', 'revoke', 'remove', 'configure_grant'].includes(String(value.action)) &&
    (value.action === 'create' ? value.entryId === null && value.expectedRevision === null : text(value.entryId) && integer(value.expectedRevision) && (value.action === 'configure_grant' || value.expectedRevision > 0)) &&
    (['create', 'edit_metadata'].includes(String(value.action)) ? validCredentialMetadata(value.metadata) : value.metadata === null) &&
    (value.action !== 'configure_grant' || (text(value.actorId) && Array.isArray(value.permissions) &&
      typeof value.entryId === 'string' && validCredentialGrantPermissions(value.entryId, value.permissions)))
}

export function validateCredentialCatalog(value: unknown, scope: CredentialScope): CredentialCatalog {
  const catalog = value as CredentialCatalog

  if (!record(value) || catalog.version !== 1 || catalog.organizationId !== scope.organizationId ||
    !record(catalog.backend) || !text(catalog.backend.provider) || !['available', 'locked', 'unavailable'].includes(catalog.backend.state) ||
    !Array.isArray(catalog.entries) || !Array.isArray(catalog.agents) || !Array.isArray(catalog.grants) || !Array.isArray(catalog.audit) || !Array.isArray(catalog.tombstones) ||
    typeof catalog.canCreate !== 'boolean' || typeof catalog.secureInputAvailable !== 'boolean' ||
    catalog.environmentInjection !== false || catalog.providerCredentialCreation !== false) {
    throw new Error('Invalid credential catalog')
  }

  const ids = new Set<string>()

  const entries = catalog.entries.map(entry => {
    if (!validCredentialMetadata(entry) || !text(entry.id) || ids.has(entry.id) || entry.organizationId !== scope.organizationId ||
      entry.name !== credentialName(entry) || !integer(entry.revision) || entry.revision < 1 || !integer(entry.version) ||
      !['missing', 'available', 'expired', 'revoked', 'locked', 'unavailable'].includes(entry.status) || !timestamp(entry.createdAt) || !timestamp(entry.updatedAt) ||
      !text(entry.updatedBy) || !Array.isArray(entry.permissions) || !entry.permissions.every(text) ||
      !integer(entry.inFlight)) {
      throw new Error('Invalid credential entry')
    }

    ids.add(entry.id)

    return { ...credentialMetadata(entry), id: entry.id, organizationId: entry.organizationId, name: entry.name,
      revision: entry.revision, version: entry.version, status: entry.status, createdAt: entry.createdAt,
      updatedAt: entry.updatedAt, updatedBy: entry.updatedBy, permissions: [...entry.permissions], inFlight: entry.inFlight }
  })

  const agents = catalog.agents.map(agent => {
    if (!record(agent) || !text(agent.id) || !text(agent.identityId) || !text(agent.name)) {
      throw new Error('Invalid credential agent')
    }

    return { id: agent.id, identityId: agent.identityId, name: agent.name }
  })

  const grants = catalog.grants.map(grant => {
    if (!record(grant) || !text(grant.actor) || !text(grant.scope) || !integer(grant.revision) ||
      !Array.isArray(grant.permissions) || !grant.permissions.every(text) || !Array.isArray(grant.bindings) ||
      !grant.bindings.every(binding => record(binding) && text(binding.tool) && text(binding.destination))) {
      throw new Error('Invalid credential grant')
    }

    return { actor: grant.actor, scope: grant.scope, revision: grant.revision, permissions: [...grant.permissions],
      bindings: grant.bindings.map(binding => ({ tool: binding.tool, destination: binding.destination })) }
  })

  return { version: 1, organizationId: catalog.organizationId, backend: { provider: catalog.backend.provider, state: catalog.backend.state },
    entries, agents, grants, audit: [], tombstones: [], canCreate: catalog.canCreate,
    environmentInjection: false, providerCredentialCreation: false, secureInputAvailable: catalog.secureInputAvailable }
}

export function validateCredentialReceipt(value: unknown, intent: CredentialIntent, capturedVersion?: number): CredentialReceipt {
  const receipt = value as CredentialReceipt
  const expectedRevision = intent.action === 'create' ? 1 : Number(intent.expectedRevision) + 1
  const expectedVersion = intent.action === 'create' ? 0 : capturedVersion === undefined ? undefined : capturedVersion + (intent.action === 'replace_value' ? 1 : 0)

  if (record(value) && intent.action === 'configure_grant') {
    if (receipt.operationId !== intent.operationId || receipt.action !== intent.action || receipt.actorId !== intent.actorId ||
      receipt.scope !== intent.entryId || !integer(receipt.revision) || receipt.revision !== expectedRevision) {
      throw new Error('Invalid credential grant receipt')
    }

    return { operationId: receipt.operationId, action: receipt.action, actorId: receipt.actorId, scope: receipt.scope, revision: receipt.revision }
  }

  if (!record(value) || receipt.operationId !== intent.operationId || receipt.action !== intent.action ||
    !text(receipt.entryId) ||
    (intent.entryId !== null && receipt.entryId !== intent.entryId) || !integer(receipt.revision) || receipt.revision !== expectedRevision || !integer(receipt.version) ||
    (expectedVersion !== undefined && receipt.version !== expectedVersion)) {
    throw new Error('Invalid credential receipt')
  }

  if (['revoke', 'remove'].includes(receipt.action) &&
    (!integer(receipt.inFlight) || !integer(receipt.queuedCancelled) || receipt.providerRevoked !== false || receipt.storedValueDeleted !== false)) {
    throw new Error('Invalid credential revocation receipt')
  }

  return { operationId: receipt.operationId, action: receipt.action,
    entryId: receipt.entryId, revision: receipt.revision, version: receipt.version,
    ...(['revoke', 'remove'].includes(receipt.action) ? { inFlight: receipt.inFlight, queuedCancelled: receipt.queuedCancelled,
      providerRevoked: false as const, storedValueDeleted: false as const } : {}) }
}

import { policyEqual } from './organization-owner-policy-contract'
import type { OwnerPolicyScope } from './organization-owner-policy-types'

export interface FolderDescriptor {
  capabilityVersion: 1
  organizationId: string
  ownerId: string
  policyGeneration: number
  sourceRevision: string
  rootAliases: string[]
  teams: string[]
  blockers: string[]
  inspectionEnabled: boolean
  scope: { kind: 'served-profile'; profile: string }
}
export interface FolderCandidate {
  folder: { path: string; projectId: string; team: string }
  project: { id: string; root: string; recipe: null; team: string }
  directoryIdentity: { device: number; inode: number }
}
export interface FolderPreview extends FolderDescriptor {
  candidate: FolderCandidate
  previewVersion: string
}
const equal = policyEqual
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(item => typeof item === 'string')
const hash = (value: unknown) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)

/** One captured authenticated owner socket. Retries retain the exact reviewed intent. */
export function createFolderClient(source: () => OwnerPolicyScope, newKey: () => string = () => crypto.randomUUID()) {
  let captured: OwnerPolicyScope | null = null
  let descriptor: FolderDescriptor | null = null
  let preview: FolderPreview | null = null
  let intent: Record<string, unknown> | null = null
  let busy = false
  const identity = (scope: OwnerPolicyScope) => [scope.key, scope.ownerKey, scope.profile, scope.eligible, scope.organizationId, scope.ownerId]
  const current = () => !!captured && captured.eligible && equal(identity(captured), identity(source()))

  const requireCurrent = () => { if (!current()) { throw new Error('Organization connection changed. Reopen folder setup.') } }

  const validate = (value: unknown): FolderDescriptor => {
    if (!captured || !object(value) || value.capabilityVersion !== 1 || value.organizationId !== captured.organizationId || value.ownerId !== captured.ownerId ||
      !object(value.scope) || value.scope.kind !== 'served-profile' || value.scope.profile !== captured.profile ||
      !Number.isSafeInteger(value.policyGeneration) || (value.policyGeneration as number) < 1 || !hash(value.sourceRevision) ||
      !strings(value.rootAliases) || value.rootAliases.length > 8 || !strings(value.teams) || !value.teams.length ||
      !strings(value.blockers) || typeof value.inspectionEnabled !== 'boolean') {
      throw new Error('Folder setup returned an invalid owner descriptor.')
    }

    return value as unknown as FolderDescriptor
  }

  return {
    current,
    async read() {
      if (busy || intent) { throw new Error('Resolve the current folder registration before refreshing.') }
      captured = { ...source() }

      if (!captured.eligible || !captured.organizationId || !captured.ownerId) { throw new Error('An authenticated organization owner is required.') }
      descriptor = null; preview = null; intent = null
      busy = true

      try {
        const result = await captured.request('organization.folderSetup', { expectedOrganizationId: captured.organizationId })
        requireCurrent()
        descriptor = structuredClone(validate(result))

        return structuredClone(descriptor)
      } finally { busy = false }
    },
    async preview(folder: FolderCandidate['folder']) {
      requireCurrent()

      if (busy || !descriptor || descriptor.blockers.length || intent) { throw new Error('Folder setup is not ready for another preview.') }
      busy = true

      try {
        const result = await captured!.request('organization.previewProjectFolder', {
          expectedOrganizationId: descriptor.organizationId, expectedGeneration: descriptor.policyGeneration,
          sourceRevision: descriptor.sourceRevision, folder
        })

        requireCurrent()
        const value = validate(result) as FolderPreview
        const candidate = value.candidate

        if (value.policyGeneration !== descriptor.policyGeneration || value.sourceRevision !== descriptor.sourceRevision || !hash(value.previewVersion) ||
          !object(candidate) || !equal(candidate.folder, folder) || !object(candidate.project) ||
          candidate.project.id !== folder.projectId || candidate.project.team !== folder.team || candidate.project.recipe !== null ||
          candidate.project.root !== `root${descriptor.rootAliases.length}` || !object(candidate.directoryIdentity) ||
          !Number.isSafeInteger(candidate.directoryIdentity.device) || !Number.isSafeInteger(candidate.directoryIdentity.inode)) {
          throw new Error('Folder preview does not match the selected folder.')
        }

        preview = structuredClone(value)

        return structuredClone(value)
      } finally { busy = false }
    },
    async register(confirmed: boolean) {
      requireCurrent()

      if (!confirmed || busy || !preview || !descriptor) { throw new Error('Review and confirm the exact folder access first.') }
      intent ??= { expectedOrganizationId: descriptor.organizationId, expectedGeneration: descriptor.policyGeneration,
        sourceRevision: descriptor.sourceRevision, candidate: preview.candidate, previewVersion: preview.previewVersion,
        idempotencyKey: newKey(), confirmed: true }
      busy = true

      try {
        const result = await captured!.request('organization.registerProjectFolder', intent)
        requireCurrent()

        if (!object(result) || result.capabilityVersion !== 1 || !object(result.scope) || result.scope.kind !== 'served-profile' || result.scope.profile !== captured!.profile || !object(result.receipt)) {
          throw new Error('Folder registration outcome is uncertain. Retry the same reviewed request.')
        }

        const receipt = result.receipt

        if (receipt.idempotencyKey !== intent.idempotencyKey || receipt.organizationId !== descriptor.organizationId || receipt.ownerId !== descriptor.ownerId ||
          receipt.policyGeneration !== descriptor.policyGeneration + 1 || receipt.sourceRevision !== intent.sourceRevision || receipt.previewVersion !== intent.previewVersion ||
          !equal(receipt.candidate, preview.candidate)) {
          throw new Error('Folder registration receipt does not match the reviewed request. Retry without changing it.')
        }

        const project = structuredClone(preview.candidate.project)
        intent = null; preview = null

        return project
      } finally { busy = false }
    }
  }
}

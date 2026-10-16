import { policyEqual } from './organization-owner-policy-contract'
import {
  type PreparedValidationCandidate,
  type PreparedValidationDescriptor,
  PreparedValidationError,
  type PreparedValidationScope,
  type PreparedValidationSelection
} from './runtime-prepared-validation-types'

export const preparedRecord = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)
export const preparedHash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
export const preparedInteger = (value: unknown, min = 0): value is number => Number.isSafeInteger(value) && (value as number) >= min
const text = (value: unknown): value is string => typeof value === 'string' && value.length > 0 && value.length <= 4096
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(text)
const exact = (value: Record<string, unknown>, keys: string[]) => policyEqual(Object.keys(value).sort(), [...keys].sort())

// Python normalization orders Unicode code points, not JavaScript UTF-16 units.
const compare = (a: string, b: string) => {
  const left = Array.from(a), right = Array.from(b)

  for (let index = 0; index < Math.min(left.length, right.length); index++) {
    const difference = left[index].codePointAt(0)! - right[index].codePointAt(0)!

    if (difference) { return difference }
  }

  return left.length - right.length
}

const sortedRecords = (value: unknown, key: string): unknown => Array.isArray(value) && value.every(item => preparedRecord(item) && typeof item[key] === 'string')
  ? [...value].sort((a, b) => compare(a[key], b[key])) : value

/** Only documented order normalization is allowed; fields and values remain exact. */
export function normalizePreparedSelection(selection: PreparedValidationSelection): PreparedValidationSelection {
  const value = structuredClone(selection)
  value.source.files.sort(compare)
  const manifest = value.dependencies.manifest

  if (strings(manifest.selectedWorkspaces)) { manifest.selectedWorkspaces.sort(compare) }

  for (const key of ['packageManifests', 'workspaceLinks', 'files']) { manifest[key] = sortedRecords(manifest[key], 'path') }

  if (preparedRecord(manifest.provenance)) { manifest.provenance.registryPackages = sortedRecords(manifest.provenance.registryPackages, 'path') }

  if (Array.isArray(value.profile.steps)) {
    for (const step of value.profile.steps) {
      if (!preparedRecord(step)) { continue }

      if (strings(step.testFiles)) { step.testFiles.sort(compare) }

      if (strings(step.sourceDirectories)) { step.sourceDirectories.sort(compare) }
    }

    value.profile.steps = sortedRecords(value.profile.steps, 'id')
  }

  return value
}

export function validPreparedSelection(value: unknown): value is PreparedValidationSelection {
  if (!preparedRecord(value) || !exact(value, ['projectId', 'enabled', 'source', 'dependencies', 'profile']) || !text(value.projectId) || value.enabled !== true ||
    !preparedRecord(value.source) || !exact(value.source, ['files', 'limits', 'executionOnlyHashes']) || !strings(value.source.files) || !value.source.files.length ||
    !preparedRecord(value.source.limits) || !preparedRecord(value.source.executionOnlyHashes) ||
    !preparedRecord(value.dependencies) || !exact(value.dependencies, ['directory', 'manifest']) || !text(value.dependencies.directory) ||
    !value.dependencies.directory.startsWith('/') || !preparedRecord(value.dependencies.manifest) || !preparedRecord(value.profile)) { return false }

  const limits = value.source.limits
  const ceilings = { max_files: 10000, max_file_bytes: 33554432, max_total_bytes: 134217728, max_path_bytes: 512, max_depth: 32 }

  if (!exact(limits, Object.keys(ceilings)) || !Object.entries(ceilings).every(([key, max]) => preparedInteger(limits[key], 1) && limits[key] <= max) ||
    (limits.max_file_bytes as number) > (limits.max_total_bytes as number) || value.source.files.length > (limits.max_files as number)) { return false }

  const paths = value.source.files

  if (new Set(paths).size !== paths.length || paths.some(path => path.startsWith('/') || /[\\:*?[\]{}]/.test(path) || [...path].some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127) || path.split('/').some(part => ['', '.', '..'].includes(part))) ||
    !Object.entries(value.source.executionOnlyHashes).every(([path, hash]) => paths.includes(path) && preparedHash(hash))) { return false }

  return validPreparedProfile(value.profile)
}

const profileId = (value: unknown): value is string => typeof value === 'string' && /^[a-z][a-z0-9_-]{0,63}$/.test(value)

const profilePath = (value: unknown): value is string => typeof value === 'string' && value.length > 0 &&
  new TextEncoder().encode(value).length <= 512 && !value.startsWith('/') && !/[\\:*?[\]{}]/.test(value) &&
  ![...value].some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127) &&
  value.split('/').length <= 32 && !value.split('/').some(part => ['', '.', '..'].includes(part))

/** Versioned recipes only; neither a profile nor a step may introduce a command. */
export function validPreparedProfile(profile: unknown): boolean {
  if (!preparedRecord(profile) || !exact(profile, ['version', 'id', 'workspace', 'bundleSha256', 'steps', 'limits']) ||
    ![1, 2].includes(profile.version as number) || !profileId(profile.id) || !profilePath(profile.workspace) ||
    !preparedHash(profile.bundleSha256) || !preparedRecord(profile.limits) || !Array.isArray(profile.steps)) { return false }

  const bounds: Record<string, [number, number]> = { wallSeconds: [1, 1800], memoryMiB: [256, 8192], cpus: [1, 1],
    pids: [64, 64], scratchMiB: [16, 2048], logBytes: [1024, 1048576] }

  const limits = profile.limits
  const workspace = profile.workspace

  const kinds: Record<string, string> = { workspace_typescript: 'project_typecheck', workspace_vitest_ui: 'project_tests',
    ...(profile.version === 2 ? { workspace_vite_build: 'project_build' } : {}) }

  const stepCount = Object.keys(kinds).length

  if (!exact(limits, Object.keys(bounds)) || !Object.entries(bounds).every(([key, [min, max]]) => preparedInteger(limits[key], min) && limits[key] <= max) ||
    profile.steps.length !== stepCount || new TextEncoder().encode(JSON.stringify(profile)).length > 65536) { return false }

  return profile.steps.every(step => {
    if (!preparedRecord(step) || !exact(step, ['id', 'recipe', 'checkKind', 'config', 'testFiles', 'sourceDirectories']) ||
      !profileId(step.id) || typeof step.recipe !== 'string' || !Object.hasOwn(kinds, step.recipe) || step.checkKind !== kinds[step.recipe] ||
      !profilePath(step.config) || !step.config.startsWith(`${workspace}/`) || !Array.isArray(step.testFiles) ||
      !Array.isArray(step.sourceDirectories) || step.sourceDirectories.length > 8 || !step.sourceDirectories.every(profilePath)) { return false }

    const files = step.testFiles
    const directories = step.sourceDirectories.map(path => path.toLowerCase()).sort()

    if (new Set(directories).size !== directories.length || directories.some((left, index) => directories.slice(index + 1).some(right => right.startsWith(`${left}/`)))) { return false }

    if (step.recipe === 'workspace_typescript') { return step.config.endsWith('.json') && files.length === 0 && directories.length > 0 }

    if (directories.length) { return false }

    if (step.recipe === 'workspace_vite_build') { return files.length === 0 }

    return files.length >= 1 && files.length <= 512 && files.every(path => profilePath(path) && path.startsWith(`${workspace}/src/`) && /\.test\.tsx?$/.test(path)) &&
      new Set(files.map(path => path.toLowerCase())).size === files.length
  }) && new Set(profile.steps.map(step => step.recipe)).size === stepCount && new Set(profile.steps.map(step => step.id)).size === stepCount
}

export function validatePreparedDescriptor(value: unknown, scope: PreparedValidationScope): PreparedValidationDescriptor {
  if (!preparedRecord(value) || value.capabilityVersion !== 1 || value.organizationId !== scope.organizationId || value.ownerId !== scope.ownerId ||
    !preparedInteger(value.policyGeneration, 1) || !preparedHash(value.sourceRevision) || !preparedInteger(value.sidecarRevision) || !strings(value.blockers) ||
    !preparedRecord(value.scope) || value.scope.kind !== 'served-profile' || value.scope.profile !== scope.profile ||
    !preparedRecord(value.platform) || typeof value.platform.supported !== 'boolean' || !text(value.platform.system) || !text(value.platform.architecture) ||
    value.platform.backend !== 'linux-docker-pinned' || value.platform.runtimeReadiness !== 'not_probed' ||
    !Array.isArray(value.projects) || value.projects.length > 8 || !value.projects.every(project => preparedRecord(project) && text(project.id) && project.id.length <= 64 && text(project.root) &&
      (project.recipe === null || text(project.recipe)) && text(project.team) && preparedInteger(project.registrationRevision) &&
      (project.registrationHash === null || preparedHash(project.registrationHash)) && typeof project.enabled === 'boolean' && typeof project.rootRevoked === 'boolean' &&
      (!Object.hasOwn(project, 'preparedProfileVersion') || (project.enabled && [1, 2].includes(project.preparedProfileVersion as number))) &&
      (!project.enabled || (project.registrationRevision > 0 && preparedHash(project.registrationHash)))) ||
    !Array.isArray(value.registrationRefs) || value.registrationRefs.length > 8 || !value.registrationRefs.every(item => preparedRecord(item) && exact(item, ['projectId', 'revision', 'registrationSha256']) && text(item.projectId) && preparedInteger(item.revision, 1) && preparedHash(item.registrationSha256)) ||
    new Set(value.registrationRefs.map(item => item.projectId)).size !== value.registrationRefs.length ||
    new Set(value.projects.map(project => project.id)).size !== value.projects.length) { throw new PreparedValidationError('invalid') }

  const projects = value.projects

  if (!value.registrationRefs.every(item => projects.some(project => project.id === item.projectId && project.enabled && !project.rootRevoked &&
    project.registrationRevision === item.revision && project.registrationHash === item.registrationSha256))) { throw new PreparedValidationError('invalid') }

  if (value.platform.supported && (value.platform.system.toLowerCase() !== 'linux' || !['x86_64', 'x64', 'amd64'].includes(value.platform.architecture.toLowerCase()))) { throw new PreparedValidationError('invalid') }

  return value as unknown as PreparedValidationDescriptor
}

export function validPreparedCandidate(value: unknown, selection: PreparedValidationSelection, descriptor: PreparedValidationDescriptor): value is PreparedValidationCandidate {
  if (!preparedRecord(value) || !exact(value, ['enabled', 'contract', 'verification', 'selection']) || value.enabled !== true ||
    !validPreparedSelection(value.selection) || !policyEqual(value.selection, normalizePreparedSelection(selection)) || !preparedRecord(value.contract) || !preparedRecord(value.verification)) { return false }

  const contract = value.contract
  const project = descriptor.projects.find(item => item.id === selection.projectId)

  if (!project || !exact(contract, ['version', 'contract', 'organizationId', 'ownerId', 'projectId', 'projectBindingSha256', 'revision', 'root', 'source', 'dependencies', 'profile', 'runtime']) || contract.version !== value.selection.profile.version || contract.contract !== `prepared-validation-v${contract.version}` || contract.organizationId !== descriptor.organizationId ||
    contract.ownerId !== descriptor.ownerId || contract.projectId !== project.id || contract.revision !== project.registrationRevision + 1 || !preparedHash(contract.projectBindingSha256) ||
    !preparedRecord(contract.root) || !exact(contract.root, ['alias', 'device', 'inode']) || contract.root.alias !== project.root || !preparedInteger(contract.root.device) || !preparedInteger(contract.root.inode) ||
    !policyEqual(contract.source, value.selection.source) || !policyEqual(contract.profile, value.selection.profile) || !preparedRecord(contract.dependencies) || !exact(contract.dependencies, ['directory', 'device', 'inode', 'manifest', 'bundleSha256', 'inventorySha256', 'lockfileSha256', 'toolchain']) ||
    contract.dependencies.directory !== value.selection.dependencies.directory || !policyEqual(contract.dependencies.manifest, value.selection.dependencies.manifest) ||
    !preparedInteger(contract.dependencies.device) || !preparedInteger(contract.dependencies.inode) ||
    !['bundleSha256', 'inventorySha256', 'lockfileSha256'].every(key => preparedHash(contract.dependencies && (contract.dependencies as Record<string, unknown>)[key])) ||
    !preparedRecord(contract.dependencies.toolchain) || contract.dependencies.toolchain.platform !== 'linux' || contract.dependencies.toolchain.arch !== 'x64' || contract.dependencies.toolchain.libc !== 'glibc' ||
    contract.dependencies.bundleSha256 !== value.selection.profile.bundleSha256 || !policyEqual(contract.dependencies.toolchain, value.selection.dependencies.manifest.toolchain) ||
    !preparedRecord(contract.runtime) || !exact(contract.runtime, ['backend', 'image', 'allowLocalDocker', 'network', 'credentials']) || contract.runtime.backend !== 'linux-docker-pinned' || contract.runtime.allowLocalDocker !== true ||
    contract.runtime.network !== 'none' || contract.runtime.credentials !== 'none' || typeof contract.runtime.image !== 'string' || !/@sha256:[a-f0-9]{64}$/.test(contract.runtime.image)) { return false }

  const verification = value.verification

  return exact(verification, ['state', 'snapshotSha256', 'sourceFiles', 'sourceBytes', 'dependencyFiles', 'dependencyBytes', 'stagingCleanupConfirmed']) && verification.state === 'verified_bytes_runtime_unprobed' && verification.stagingCleanupConfirmed === true && preparedHash(verification.snapshotSha256) &&
    verification.sourceFiles === value.selection.source.files.length && preparedInteger(verification.sourceBytes) &&
    verification.sourceBytes <= value.selection.source.limits.max_total_bytes && preparedInteger(verification.dependencyFiles, 1) && preparedInteger(verification.dependencyBytes)
}

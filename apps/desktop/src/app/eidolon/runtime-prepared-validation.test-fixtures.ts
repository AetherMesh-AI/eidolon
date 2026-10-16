import { normalizePreparedSelection } from './runtime-prepared-validation-contract'
import type { PreparedValidationCandidate, PreparedValidationDescriptor, PreparedValidationIntent, PreparedValidationRevokeIntent, PreparedValidationSelection } from './runtime-prepared-validation-types'

export function preparedDescriptor(): PreparedValidationDescriptor {
  return { capabilityVersion: 1, organizationId: 'org', ownerId: 'owner', policyGeneration: 3, sourceRevision: 'a'.repeat(64), sidecarRevision: 4,
    projects: [{ id: 'desktop', root: 'root0', recipe: null, team: 'engineering', registrationRevision: 0, registrationHash: null, rootRevoked: false, enabled: false }],
    registrationRefs: [], blockers: [], platform: { supported: true, system: 'linux', architecture: 'x86_64', backend: 'linux-docker-pinned', runtimeReadiness: 'not_probed' },
    scope: { kind: 'served-profile', profile: 'default' } }
}

export function preparedSelection(version: 1 | 2 = 1): PreparedValidationSelection {
  const toolchain = { platform: 'linux', arch: 'x64', libc: 'glibc', nodeVersion: '24.11.0', npmVersion: '11.17.0', nodeAbi: '137', tools: { typescript: '5.9.3', vitest: '3.2.4', ...(version === 2 ? { vite: '7.1.12' } : {}) } }

  return { projectId: 'desktop', enabled: true,
    source: { files: ['package-lock.json', 'package.json', 'apps/desktop/package.json', 'apps/desktop/tsconfig.json', 'apps/desktop/vitest.config.ts', 'apps/desktop/src/main.test.tsx', ...(version === 2 ? ['apps/desktop/vite.config.ts', 'apps/desktop/index.html'] : [])],
      limits: { max_files: 1000, max_file_bytes: 8388608, max_total_bytes: 67108864, max_path_bytes: 512, max_depth: 32 }, executionOnlyHashes: { 'apps/desktop/src/main.test.tsx': 'b'.repeat(64) } },
    dependencies: { directory: '/prepared/dependencies', manifest: { schemaVersion: 1, lockfile: { path: 'package-lock.json', sha256: 'c'.repeat(64) },
      packageManifests: [{ path: 'package.json', sha256: 'd'.repeat(64) }, { path: 'apps/desktop/package.json', sha256: 'e'.repeat(64) }],
      selectedWorkspaces: ['apps/desktop'], workspaceLinks: [], toolchain,
      provenance: { kind: 'owner-supplied', preparationRecordSha256: 'f'.repeat(64), registryPackages: [] },
      files: [{ path: 'node_modules/vitest/dist/node.js', bytes: 40, sha256: '1'.repeat(64), mode: 420, kind: 'file' },
        { path: 'node_modules/typescript/lib/typescript.js', bytes: 60, sha256: '2'.repeat(64), mode: 420, kind: 'file' }] } },
    profile: { version, id: 'renderer', workspace: 'apps/desktop', bundleSha256: '3'.repeat(64),
      steps: [{ id: 'types', recipe: 'workspace_typescript', checkKind: 'project_typecheck', config: 'apps/desktop/tsconfig.json', testFiles: [], sourceDirectories: ['apps/desktop/src'] },
        { id: 'tests', recipe: 'workspace_vitest_ui', checkKind: 'project_tests', config: 'apps/desktop/vitest.config.ts', testFiles: ['apps/desktop/src/main.test.tsx'], sourceDirectories: [] },
        ...(version === 2 ? [{ id: 'build', recipe: 'workspace_vite_build', checkKind: 'project_build', config: 'apps/desktop/vite.config.ts', testFiles: [], sourceDirectories: [] }] : [])],
      limits: { wallSeconds: 300, memoryMiB: 2048, cpus: 1, pids: 64, scratchMiB: 256, logBytes: 65536 } } }
}

export function preparedCandidate(descriptor: PreparedValidationDescriptor, selection: PreparedValidationSelection): PreparedValidationCandidate {
  const selected = normalizePreparedSelection(selection)

  return { enabled: true, selection: selected,
    contract: { version: selected.profile.version as 1 | 2, contract: selected.profile.version === 2 ? 'prepared-validation-v2' : 'prepared-validation-v1', organizationId: descriptor.organizationId, ownerId: descriptor.ownerId, projectId: selected.projectId,
      projectBindingSha256: '4'.repeat(64), revision: descriptor.projects.find(project => project.id === selected.projectId)!.registrationRevision + 1,
      root: { alias: 'root0', device: 1, inode: 2 }, source: selected.source,
      dependencies: { ...selected.dependencies, device: 1, inode: 3, bundleSha256: '3'.repeat(64), inventorySha256: '5'.repeat(64), lockfileSha256: 'c'.repeat(64), toolchain: selected.dependencies.manifest.toolchain as Record<string, unknown> },
      profile: selected.profile, runtime: { backend: 'linux-docker-pinned', image: `node@sha256:${'6'.repeat(64)}`, allowLocalDocker: true, network: 'none', credentials: 'none' } },
    verification: { state: 'verified_bytes_runtime_unprobed', snapshotSha256: '7'.repeat(64), sourceFiles: selected.source.files.length, sourceBytes: 1000, dependencyFiles: 2, dependencyBytes: 100, stagingCleanupConfirmed: true } }
}

export function preparedPreview(descriptor = preparedDescriptor(), selection = preparedSelection()) {
  return { ...structuredClone(descriptor), candidate: preparedCandidate(descriptor, selection), candidateHash: '8'.repeat(64), previewVersion: '9'.repeat(64) }
}

export function preparedSaveResult(descriptor: PreparedValidationDescriptor, intent: PreparedValidationIntent) {
  return { capabilityVersion: 1, scope: descriptor.scope, receipt: { organizationId: descriptor.organizationId, ownerId: descriptor.ownerId,
    policyGeneration: descriptor.policyGeneration, sourceRevision: descriptor.sourceRevision, previousSidecarRevision: descriptor.sidecarRevision, sidecarRevision: descriptor.sidecarRevision + 1,
    projectId: intent.candidate.contract.projectId, registrationRevision: intent.candidate.contract.revision, candidateHash: intent.candidateHash,
    previewVersion: intent.previewVersion, idempotencyKey: intent.idempotencyKey, candidate: structuredClone(intent.candidate) } }
}

export function preparedRevokeResult(descriptor: PreparedValidationDescriptor, intent: PreparedValidationRevokeIntent) {
  return { capabilityVersion: 1, scope: descriptor.scope, receipt: { organizationId: descriptor.organizationId, ownerId: descriptor.ownerId,
    policyGeneration: descriptor.policyGeneration, previousSidecarRevision: descriptor.sidecarRevision, sidecarRevision: descriptor.sidecarRevision + 1,
    projectId: intent.projectId, registrationRevision: intent.registrationRevision + 1, previousRegistrationRevision: intent.registrationRevision,
    registrationHash: intent.registrationHash, idempotencyKey: intent.idempotencyKey, disabled: true } }
}

export function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })

  return { promise, resolve, reject }
}

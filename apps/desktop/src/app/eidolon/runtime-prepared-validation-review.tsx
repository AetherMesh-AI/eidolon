import { useMemo, useState } from 'react'

import { useI18n } from '@/i18n/context'

import { preparedValidationCopy } from './runtime-prepared-validation-copy'
import type { PreparedValidationCandidate } from './runtime-prepared-validation-types'

/** The full dependency inventory may contain 100,000 entries. Never stringify it into the DOM. */
export function preparedReviewSummary(candidate: PreparedValidationCandidate) {
  const contract = candidate.contract
  const dependencies = contract.dependencies
  const exceptions = Object.entries(contract.source.executionOnlyHashes)

  return { projectId: contract.projectId, enabled: candidate.enabled, registrationRevision: contract.revision,
    projectBindingSha256: contract.projectBindingSha256, root: contract.root,
    source: { fileCount: contract.source.files.length, firstSourcePaths: contract.source.files.slice(0, 100),
      limits: contract.source.limits, executionOnlyExceptionCount: exceptions.length, firstExecutionOnlyHashes: Object.fromEntries(exceptions.slice(0, 100)) },
    dependencies: { directory: dependencies.directory, device: dependencies.device, inode: dependencies.inode,
      bundleSha256: dependencies.bundleSha256, inventorySha256: dependencies.inventorySha256, lockfileSha256: dependencies.lockfileSha256,
      toolchain: dependencies.toolchain, selectedWorkspaces: dependencies.manifest.selectedWorkspaces,
      verifiedFiles: candidate.verification.dependencyFiles, verifiedBytes: candidate.verification.dependencyBytes },
    profile: contract.profile, runtime: contract.runtime, verification: candidate.verification }
}

export function PreparedValidationReview({ candidate }: { candidate: PreparedValidationCandidate }) {
  const { locale } = useI18n()
  const copy = preparedValidationCopy[locale]
  const [expanded, setExpanded] = useState(false)
  const summary = useMemo(() => expanded ? JSON.stringify(preparedReviewSummary(candidate), null, 2) : '', [candidate, expanded])

  return <details onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary>{copy.exact}</summary>
    {expanded && <>
      <p className="eid-note">{copy.boundedReview}</p>
      <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all text-xs" dir="ltr">{summary}</pre>
    </>}
  </details>
}

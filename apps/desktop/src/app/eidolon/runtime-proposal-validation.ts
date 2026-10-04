import type {
  OrganizationEditFile,
  OrganizationEditProposal,
  OrganizationSourceVerificationReceipt,
  OrganizationValidationResult
} from './types'

export function validEditProposal(proposal: OrganizationEditProposal): boolean {
  if (!proposal || typeof proposal !== 'object') {
    return false
  }

  const revision = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0

  return (
    validProjectEvidence(proposal) &&
    ['id', 'workspaceId', 'sourcePath'].every(
      field =>
        typeof proposal[field as keyof OrganizationEditProposal] === 'string' &&
        proposal[field as keyof OrganizationEditProposal] !== ''
    ) &&
    ['baseSha256', 'newSha256', 'proposalSha256'].every(
      field =>
        typeof proposal[field as keyof OrganizationEditProposal] === 'string' &&
        /^[a-f0-9]{64}$/.test(proposal[field as keyof OrganizationEditProposal] as string)
    ) &&
    ['baseContent', 'newContent', 'diff'].every(
      field => typeof proposal[field as keyof OrganizationEditProposal] === 'string'
    ) &&
    revision(proposal.baseRevision) &&
    revision(proposal.currentRevision) &&
    proposal.currentRevision >= proposal.baseRevision &&
    ['proposed', 'approved', 'applied'].includes(proposal.status) &&
    ['pending', 'approved', 'rejected'].includes(proposal.reviewStatus) &&
    ['reviewReason', 'applicationReason'].every(
      field =>
        proposal[field as keyof OrganizationEditProposal] === undefined ||
        typeof proposal[field as keyof OrganizationEditProposal] === 'string'
    ) &&
    (proposal.status === 'proposed' || proposal.reviewStatus === 'approved') &&
    (proposal.status !== 'applied' ||
      (revision(proposal.appliedRevision) &&
        proposal.appliedRevision! > proposal.baseRevision &&
        proposal.appliedRevision! <= proposal.currentRevision &&
        typeof proposal.appliedAt === 'string' &&
        Number.isFinite(Date.parse(proposal.appliedAt))))
  )
}

const digest = (value: unknown) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const revision = (value: unknown) => Number.isSafeInteger(value) && Number(value) >= 0
const strings = (value: unknown) => Array.isArray(value) && value.every(item => typeof item === 'string')
const text = (value: unknown) => typeof value === 'string'

function validFile(file: OrganizationEditFile) {
  return (
    file &&
    text(file.sourcePath) &&
    !!file.sourcePath &&
    revision(file.baseRevision) &&
    digest(file.baseSha256) &&
    digest(file.newSha256) &&
    text(file.baseContent) &&
    text(file.newContent) &&
    text(file.diff)
  )
}

function validValidation(value: OrganizationValidationResult) {
  return (
    value &&
    text(value.runner) &&
    revision(value.runnerVersion) &&
    value.scope === 'managed_workspace' &&
    ['passed', 'failed'].includes(value.status) &&
    Array.isArray(value.inputs) &&
    value.inputs.every(item => item && text(item.path) && revision(item.revision) && digest(item.sha256)) &&
    Array.isArray(value.checks) &&
    value.checks.every(
      item =>
        item &&
        text(item.kind) &&
        text(item.path) &&
        typeof item.passed === 'boolean' &&
        (item.reason === undefined || text(item.reason)) &&
        (item.checkSha256 === undefined || digest(item.checkSha256))
    ) &&
    strings(value.notExecuted) &&
    text(value.limitations)
  )
}

function validReceipt(value: { requestId: string; resultSha256: string; created: number }) {
  return text(value.requestId) && digest(value.resultSha256) && Number.isFinite(value.created)
}

function validSource(value: OrganizationSourceVerificationReceipt) {
  return (
    value &&
    value.scope === 'latest_validated_project_heads' &&
    ['verified', 'superseded'].includes(value.status) &&
    value.sourceWritesPerformed === false &&
    text(value.limitations) &&
    Array.isArray(value.files) &&
    value.files.every(
      item => item && text(item.path) && revision(item.revision) && digest(item.sha256) && text(item.proposalId)
    ) &&
    strings(value.coveredProposalIds) &&
    Array.isArray(value.validations) &&
    value.validations.every(
      item =>
        item &&
        text(item.proposalId) &&
        digest(item.proposalSha256) &&
        (item.validationSha256 === null || digest(item.validationSha256)) &&
        ['passed', 'failed', 'unknown'].includes(item.status)
    ) &&
    validValidation(value.manifestValidation) &&
    validReceipt(value)
  )
}

function validProjectEvidence(proposal: OrganizationEditProposal) {
  return (
    (proposal.files === undefined ||
      (Array.isArray(proposal.files) &&
        proposal.files.length > 0 &&
        proposal.files.length <= 8 &&
        proposal.files.every(validFile) &&
        new Set(proposal.files.map(file => file.sourcePath)).size === proposal.files.length)) &&
    (proposal.validationReceipt == null ||
      (validValidation(proposal.validationReceipt) && validReceipt(proposal.validationReceipt))) &&
    (proposal.sourceVerificationReceipt == null || validSource(proposal.sourceVerificationReceipt)) &&
    (proposal.notExecuted === undefined || strings(proposal.notExecuted))
  )
}

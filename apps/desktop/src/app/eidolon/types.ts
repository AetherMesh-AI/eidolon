export const objectiveStatusLabels = {
  planning: 'Planning', active: 'Active', waiting: 'Waiting', needs_input: 'Needs Input',
  blocked: 'Blocked', completed: 'Completed', paused: 'Paused', archived: 'Archived', cancelled: 'Cancelled',
} as const
export type ObjectiveStatus = keyof typeof objectiveStatusLabels
export type WorkStatus = 'working' | 'active' | 'thinking' | 'executing' | 'reviewing' | 'needs_input' | 'idle' | 'waiting' | 'review' | 'offline'
export interface ObjectiveMetadata {
  description?: string
  acceptanceCriteria?: string[]
  deliveryMode?: 'source_project' | 'managed_artifact'
  requiredChecks?: ('project_tests' | 'managed_validation' | 'source_integration')[]
  priority?: 'low' | 'normal' | 'high' | 'P5' | 'P4' | 'P3' | 'P2' | 'P1'
  agentId?: string
  progress?: number
  phase?: string
  milestone?: string
  autonomyIntent?: string
}
export interface Objective extends ObjectiveMetadata {
  id: string
  title: string
  description: string
  status: ObjectiveStatus
  source: 'prototype' | 'runtime'
  createdAt: string
  ownerId: string
  sessionId?: string
  result?: string
  acceptance?: OrganizationAcceptance
  usage?: OrganizationUsage
  ownerResolutions?: OrganizationOwnerResolution[]
  projectValidation?: {
    id: string
    status: 'passed' | 'failed'
    scope: 'latest_managed_project_heads'
    resultSha256: string
    filesCount: number
    checksCount: number
    notExecuted: string[]
  }
}
export interface OrganizationAgent {
  id: string
  name: string
  role: string
  team?: string
  managerId?: string
  responsibilities: string[]
  capabilities: string[]
  requestTypes?: string[]
  status: WorkStatus
  summary: string
  model?: string
  provider?: string
  lifecycle?: 'active' | 'available' | 'disabled' | 'retired'
  context?: { used?: number; capacity?: number }
  tools?: string[]
  profileName?: string
  objectiveId?: string
}
export interface OrganizationTask {
  id: string
  objectiveId: string
  title: string
  ownerId: string
  status: 'queued' | 'working' | 'blocked' | 'review' | 'completed' | 'cancelled'
  assignedById?: string
  reviewerId?: string
  requestType?: string
  team?: string
  evidence?: OrganizationEvidence[]
  priority?: 'low' | 'normal' | 'high' | 'P5' | 'P4' | 'P3' | 'P2' | 'P1'
  agentId?: string
  review?: 'required' | 'not_required' | 'approved'
  inputs?: string[]
  results?: string[]
  dependsOn: string[]
  historical?: boolean
  currentRound?: boolean
}
export interface ActivityEvent {
  id: string
  objectiveId?: string
  agentId?: string
  kind: 'planning' | 'delegation' | 'completion' | 'blocker' | 'approval' | 'knowledge' | 'message' | 'decision' | 'tool' | 'file' | 'review' | 'system'
  provenance?: 'fictional'
  description?: string
  text: string
  timestamp: string
  source: 'prototype' | 'runtime'
}
export interface KnowledgeItem {
  id: string
  title: string
  body: string
  kind: 'memory' | 'document' | 'artifact'
  objectiveId?: string
}
export interface OrganizationDecision {
  id: string
  objectiveId: string
  title: string
  summary: string
  status: 'pending' | 'approved' | 'rejected' | 'recorded'
}
export interface OrganizationEvidence {
  id?: string
  sha256?: string
  truncated?: boolean
  kind: string
  content: string
  sessionId?: string
  createdAt?: string
}
export interface OrganizationArtifact {
  id: string
  content: string
  sha256: string
  summary: string
  createdAt: string
  objectiveId: string
  taskId: string
  toolReceipts?: OrganizationToolEvidence[]
  editProposal?: OrganizationEditProposal | null
}
/** Full immutable proposal bytes are available only through organization.evidence.
 * Review and application facts are owned by the managed-workspace runtime. */
export interface OrganizationEditFile {
  sourcePath: string
  baseRevision: number
  baseSha256: string
  newSha256: string
  baseContent: string
  newContent: string
  diff: string
}
export interface OrganizationValidationResult {
  runner: string
  runnerVersion: number
  scope: 'managed_workspace'
  status: 'passed' | 'failed'
  inputs: { path: string; revision: number; sha256: string }[]
  checks: { kind: string; path: string; passed: boolean; checkSha256?: string; reason?: string }[]
  notExecuted: string[]
  limitations: string
}
export interface OrganizationValidationReceipt extends OrganizationValidationResult {
  requestId: string
  resultSha256: string
  created: number
}
export interface OrganizationSourceVerificationReceipt {
  scope: 'latest_validated_project_heads'
  status: 'verified' | 'superseded'
  files: { path: string; revision: number; sha256: string; proposalId: string }[]
  coveredProposalIds: string[]
  validations: { proposalId: string; proposalSha256: string; validationSha256: string | null; status: 'passed' | 'failed' | 'unknown' }[]
  manifestValidation: OrganizationValidationResult
  sourceWritesPerformed: false
  limitations: string
  requestId: string
  resultSha256: string
  created: number
}
export interface OrganizationEditProposal {
  id: string
  workspaceId: string
  sourcePath: string
  baseRevision: number
  currentRevision: number
  baseSha256: string
  newSha256: string
  proposalSha256: string
  diff: string
  baseContent: string
  newContent: string
  status: 'proposed' | 'approved' | 'applied'
  reviewStatus: 'pending' | 'approved' | 'rejected'
  reviewReason?: string
  applicationReason?: string
  appliedRevision?: number
  appliedAt?: string
  files?: OrganizationEditFile[]
  validations?: { kind: string; path: string; [key: string]: unknown }[]
  validationReceipt?: OrganizationValidationReceipt | null
  sourceVerificationReceipt?: OrganizationSourceVerificationReceipt | null
  scope?: 'managed_workspace'
  notExecuted?: string[]
}
export interface OrganizationToolReceipt {
  id: string
  toolCallId: string
  requestId?: string
  attempt?: number
  toolName: string
  status: 'running' | 'completed' | 'blocked' | 'failed' | 'unknown'
  arguments: { path?: string; offset?: number; limit?: number }
  resultSha256?: string
  resultPreview?: string
  createdAt: string
  completedAt?: string
  reason?: string
}
export interface OrganizationToolEvidence extends OrganizationToolReceipt {
  result?: string
}
export type OrganizationResolutionAction = 'provide_input' | 'amend_scope' | 'retry_configuration' | 'request_replan' | 'record_handoff'
export interface OrganizationResolution {
  action: OrganizationResolutionAction
  label: string
  requiresText: boolean
  requiresEvidence: boolean
  evidenceIds?: string[]
  sourceManifest?: { path: string; revision: number; sha256: string; proposalId: string; evidenceId: string }[]
}
export interface OrganizationResolutionInput {
  id: string
  action: OrganizationResolutionAction
  text?: string
  evidenceIds?: string[]
  idempotencyKey?: string
}
export interface OrganizationOwnerResolution {
  id: string
  requestId: string
  action: OrganizationResolutionAction
  text: string
  evidenceIds: string[]
  createdAt: string
}
export interface OrganizationAcceptance {
  status: 'pending' | 'integrating' | 'reviewing' | 'accepted' | 'replanning' | 'blocked' | 'legacy_completed'
  criteria: string[]
  round: number
  maxReplans: number
  summary: string | null
  deliverableId: string | null
}
export interface OrganizationUsage {
  stages: number
  stageLimit: number
  outputTokens: number
  inputTokens: number
  usageComplete: boolean
  perCallOutputLimit: number
}
export interface OrganizationRequest {
  id: string
  objectiveId: string
  taskId?: string
  type: string
  team: string
  priority: number
  status: 'queued' | 'running' | 'completed' | 'pending_intervention' | 'cancelled'
  agentId?: string
  reason?: string
  attempts: number
  leaseExpiresAt?: string
  createdAt: string
  toolReceipts?: OrganizationToolReceipt[]
  toolReceiptCount?: number
  toolReceiptsTruncated?: boolean
  requestedRoutes?: { team: string; type: string }[]
  requestedWorkers?: number
  allowedResolutions?: OrganizationResolution[]
}
export interface OrganizationRuntime {
  capabilities: string[]
  state: string
  maxWorkers: number
  scope: string
  historyLimited?: boolean
  artifactPreviewLimit?: number
  profile?: string
  readFileEnabled?: boolean
  readRoots?: string[]
  maxToolCalls?: number
  supportsWorkspaceEdits?: boolean
  workspaceApplyEnabled?: boolean
}
export interface OrganizationConnection {
  scope: string
  ownerScope?: string
  state: 'connecting' | 'ready' | 'disconnected' | 'error'
  error?: string
  lastUpdatedAt?: string
}
export interface OrganizationSnapshot {
  source?: 'prototype' | 'runtime'
  requests?: OrganizationRequest[]
  runtime?: OrganizationRuntime
  connection?: OrganizationConnection
  decisions?: OrganizationDecision[]
  objectives: Objective[]
  agents: OrganizationAgent[]
  tasks: OrganizationTask[]
  activity: ActivityEvent[]
  knowledge: KnowledgeItem[]
}
interface OrganizationReader {
  getSnapshot: () => OrganizationSnapshot
  subscribe: (listener: () => void) => () => void
}
/** Read-only example reader. No production prototype mutation API exists. */
export interface StaticOrganizationAdapter extends OrganizationReader {
  readonly mode: 'prototype'
}

export interface RuntimeOrganizationAdapter extends OrganizationReader {
  readonly mode: 'runtime'
  createObjective(title: string, metadata?: ObjectiveMetadata, idempotencyKey?: string): Promise<Objective>
  cancelObjective(id: string): Promise<void>
  retryRequest(id: string): Promise<void>
  resolveRequest(input: OrganizationResolutionInput): Promise<void>
  refresh(): Promise<void>
  getEvidence(id: string): Promise<OrganizationArtifact>
  getToolReceipts(requestId: string): Promise<OrganizationToolEvidence[]>
}
export type OrganizationAdapter = StaticOrganizationAdapter | RuntimeOrganizationAdapter

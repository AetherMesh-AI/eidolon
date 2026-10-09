import type { OrganizationAttentionPage, OrganizationAttentionQuery, OrganizationAttentionSeen } from './runtime-attention-types'
import type { HistoryCounts, HistoryMutation, HistoryPage, HistoryQuery, HistoryState } from './runtime-history-types'
import type { OrganizationOutcomePage, OrganizationOutcomeQuery, OrganizationOutcomeSeen } from './runtime-outcome-types'
import type { OwnerChatCancel, OwnerChatRead, OwnerChatRenew, OwnerChatSend, OwnerChatThread } from './runtime-owner-chat-types'

export const objectiveStatusLabels = {
  planning: 'Planning', active: 'Active', waiting: 'Waiting', needs_input: 'Needs Input',
  blocked: 'Blocked', completed: 'Completed', paused: 'Paused', archived: 'Archived', cancelled: 'Cancelled',
} as const
export type ObjectiveStatus = keyof typeof objectiveStatusLabels
export type WorkStatus = 'working' | 'active' | 'thinking' | 'executing' | 'reviewing' | 'needs_input' | 'idle' | 'waiting' | 'review' | 'offline'
export type OrganizationRequiredCheck = 'project_tests' | 'managed_validation' | 'source_integration'
export interface OrganizationProjectBinding {
  id: string
  root: string
  recipe: string
  team: string
  readRoot?: string
}
export interface ObjectiveMetadata {
  projectIds?: string[]
  executiveId?: string
  managerId?: string
  description?: string
  acceptanceCriteria?: string[]
  deliveryMode?: 'source_project' | 'managed_artifact'
  requiredChecks?: OrganizationRequiredCheck[]
  priority?: 'low' | 'normal' | 'high' | 'P5' | 'P4' | 'P3' | 'P2' | 'P1'
  agentId?: string
  progress?: number
  phase?: string
  milestone?: string
  autonomyIntent?: string
}
/** Durable executive delegation. Package completion never implies final acceptance. */
export interface OrganizationWorkPackage {
  id: string
  objectiveId: string
  round: number
  managerId: string
  title: string
  description: string
  /** Zero-based positions in the root criteria of this package's round. */
  criterionIndexes: number[]
  /** Exact criterion text captured for these positions in the package round. */
  criteria?: string[]
  projectIds: string[]
  dependencyIds: string[]
  maxTasks: number
  planRequestId: string
  status: 'planning' | 'planned' | 'working' | 'completed' | 'blocked' | 'cancelled'
  taskIds: string[]
}
export interface Objective extends ObjectiveMetadata {
  /** Absent on older runtimes. Never infer package delegation from task ownership. */
  planningMode?: 'executive_packages' | 'legacy'
  workPackages?: OrganizationWorkPackage[]
  projects?: OrganizationProjectBinding[]
  history?: HistoryState
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
  projectExecution?: OrganizationProjectExecution | OrganizationProjectExecutions | null
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
/** Bounded, persisted context owned by one stable organization identity.
 * This is organizational memory, not a live provider transcript or tool grant. */
export interface OrganizationAgentContext {
  contextSummary?: string
  memory?: { facts: string[]; decisions: string[]; lessons: string[]; openQuestions: string[] }
  recentHistory?: {
    requestId: string
    objectiveId: string
    objectiveTitle: string
    taskId: string | null
    requestType: string
    summary: string
    evidenceIds: string[]
    createdAt: string
  }[]
  revision?: number
  updatedAt?: string | null
  /** Older runtimes may report token usage without persisted memory. */
  used?: number
  capacity?: number
}
export interface OrganizationAgent {
  id: string
  identityId?: string
  persistent?: boolean
  createdAt?: string
  name: string
  role: string
  purpose?: string
  team?: string
  managerId?: string
  responsibilities: string[]
  capabilities: string[]
  requestTypes?: string[]
  status: WorkStatus
  summary: string
  model?: string | null
  provider?: string | null
  lifecycle?: 'active' | 'available' | 'disabled' | 'retired'
  context?: OrganizationAgentContext
  tools?: string[]
  profileName?: string
  objectiveId?: string
}
export interface OrganizationTask {
  workPackageId?: string | null
  projectId?: string | null
  id: string
  objectiveId: string
  title: string
  ownerId: string
  status: 'queued' | 'working' | 'blocked' | 'review' | 'completed' | 'cancelled'
  assignedById?: string
  assignedAgentId?: string | null
  managingAgentId?: string
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
  /** Exact root-aliased paths. Missing, null or empty means legacy unspecified scope. */
  writePaths?: string[] | null
  /** Backend-owned scheduling facts, separate from lifecycle and assignment ownership. */
  coordination?: {
    state: 'unscoped' | 'ready' | 'waiting' | 'reserved' | 'released'
    reason: string | null
    blockingTaskIds: string[]
  }
  historical?: boolean
  currentRound?: boolean
}
export interface ActivityEvent {
  id: string
  objectiveId?: string
  agentId?: string
  kind: 'question' | 'planning' | 'delegation' | 'completion' | 'blocker' | 'approval' | 'knowledge' | 'message' | 'decision' | 'tool' | 'file' | 'review' | 'system'
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
  taskId: string | null
  kind?: string
  toolReceipts?: OrganizationToolEvidence[]
  editProposal?: OrganizationEditProposal | null
}
export type OrganizationProjectExecutionStatus = 'passed' | 'failed' | 'blocked' | 'unsupported' | 'cancelled' | 'timed_out' | 'unknown'
/** Backend-owned execution facts. A syntax-validation receipt cannot populate
 * this record, and a passed run is separate from review and source integration. */
export interface OrganizationProjectExecutions {
  projects: { projectId: string; execution: OrganizationProjectExecution | null }[]
}
export interface OrganizationProjectExecution {
  id: string
  requestId: string
  round: number
  snapshotSha256: string
  status: OrganizationProjectExecutionStatus
  receipt: {
    status: OrganizationProjectExecutionStatus
    exitCode?: number | null
    testCount?: number | null
    durationSeconds?: number | null
    command?: string[] | null
    reason?: string | null
    isolation?: { backend: string; established: boolean; [key: string]: unknown } | null
  }
  review: { approved: boolean; reviewerId: string; summary: string; requestId: string } | null
  sourceIntegration: {
    evidenceId: string
    status: 'integrated'
    sourceBaseCommit: string
    commit: string
    tree: string
    ref: string
    manifestSha256: string
    sourceWritesPerformed: true
    workingTreeWritesPerformed: false
  } | null
  files: { path: string; sha256: string; revision: number }[]
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
export interface OrganizationExecutionReport {
  attemptToken: string
  createdAt: string
  report: Record<string, unknown>
}
export interface OrganizationExecutionAudit {
  requestId: string
  contexts: OrganizationExecutionReport[]
  evidencePasses: OrganizationExecutionReport[]
  modelCalls: {
    id: string
    request_id: string
    provider: string
    model: string
    input_limit: number
    output_limit: number
    reserved_cost_usd: string | null
    createdAt: string
  }[]
}
export type OrganizationResolutionAction = 'answer_request' | 'approve_request' | 'deny_request' | 'provide_input' | 'amend_scope' | 'retry_configuration' | 'request_replan' | 'record_handoff'
export interface OrganizationResolution {
  action: OrganizationResolutionAction
  label: string
  requiresText: boolean
  requiresEvidence: boolean
  evidenceIds?: string[]
  sourceManifest?: { path: string; revision: number; sha256: string; proposalId: string; evidenceId: string }[]
}
export interface OrganizationScopeState {
  scope: string
  acceptanceCriteria: string[]
  requiredChecks: OrganizationRequiredCheck[]
  round: number
}
export interface OrganizationScopeAmendment {
  inputSha256: string
  sha256: string
  choices: { requiredChecks: OrganizationRequiredCheck[] | null; acceptanceCriteria: string[] | null }
  before: OrganizationScopeState
  after: OrganizationScopeState
}
export interface OrganizationResolutionInput {
  id: string
  action: OrganizationResolutionAction
  text?: string
  evidenceIds?: string[]
  requiredChecks?: OrganizationRequiredCheck[]
  acceptanceCriteria?: string[]
  idempotencyKey?: string
}
export interface OrganizationOwnerResolution {
  id: string
  requestId: string
  action: OrganizationResolutionAction
  text: string
  evidenceIds: string[]
  createdAt: string
  scopeAmendment?: OrganizationScopeAmendment | null
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
  modelCalls?: number
  modelCallLimit?: number
  reservedTokens?: number
  tokenLimit?: number
  deadlineAt?: string
  configuredCostReservedUsd?: string | null
  configuredCostLimitUsd?: string | null
  legacyUsageUnknown?: boolean
  budgetScope?: string
}
export interface OrganizationRequest {
  id: string
  /** Revision of the blocker content in this exact request projection. */
  attentionRevision?: number
  objectiveId: string
  taskId?: string
  type: string
  team: string
  priority: number
  status: 'queued' | 'running' | 'completed' | 'pending_intervention' | 'waiting_response' | 'cancelled'
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
  requesterId?: string
  requestedOutcome?: string
  requiredAuthority?: string
  parentRequestId?: string | null
  dependencyIds?: string[]
  evidenceIds?: string[]
  response?: OrganizationRequestResponse | null
  managementProposal?: OrganizationManagementProposal | null
  allowedResolutions?: OrganizationResolution[]
}
export interface OrganizationRequestResponse {
  text: string
  responderId: string
  decision?: 'answered' | 'approved' | 'denied'
  createdAt: string
}
export interface OrganizationResponseInput {
  id: string
  text: string
  decision: 'answered' | 'approved' | 'denied'
  idempotencyKey?: string
}
/** Exact backend-owned roster configuration. Edits preserve fields not changed. */
export interface OrganizationMemberConfiguration {
  id: string
  name: string
  role: 'Executive' | 'Manager' | 'Worker'
  manager_id: string
  team: string
  capabilities: string[]
  enabled: boolean
  provider: string | null
  model: string | null
  tool_grants: string[]
  responsibilities: string[]
  purpose: string
  scope?: string
  authority: string[]
  managed_teams: string[]
}
export interface OrganizationTransfer {
  fromAgentId: string
  toAgentId: string
  taskIds: string[]
  objectiveIds?: string[]
  workPackageIds?: string[]
  includeMemory: boolean
}
export interface OrganizationManagementProposal {
  members: OrganizationMemberConfiguration[]
  transfers: OrganizationTransfer[]
}
export interface OrganizationConfiguration {
  roster: OrganizationMemberConfiguration[]
  max_inflight: number
  max_members: number
  transfers?: OrganizationTransfer[]
}
export interface OrganizationConfigureInput {
  configuration: OrganizationConfiguration
  expectedGeneration: number
  idempotencyKey?: string
}
export interface OrganizationManagementChange {
  id: string
  requestId: string | null
  actorId: string
  kind: string
  subjectId: string
  before: unknown
  after: unknown
  createdAt: string
}
export interface OrganizationManagement {
  generation: number
  configuration: OrganizationConfiguration
  recentChanges?: OrganizationManagementChange[]
  allowedTools: string[]
  allowedCapabilities: string[]
}
export interface OrganizationRuntime {
  /** Configuration-only guidance; never authentication or live execution proof. */
  setup?: {
    version: 1
    provider: {
      status: 'warning' | 'unchecked'
      blockers: 'codex_app_server'[]
      inheritedMembers: number
      overriddenMembers: number
    }
    backgroundOptIn: boolean
  }
  availableProjects?: OrganizationProjectBinding[]
  history?: HistoryCounts
  capabilities: string[]
  state: string
  maxWorkers: number
  /** Simultaneous execution slots; independent from durable roster size. */
  maxInflight?: number
  rosterCount?: number
  workingCount?: number
  scope: string
  historyLimited?: boolean
  artifactPreviewLimit?: number
  profile?: string
  readFileEnabled?: boolean
  readRoots?: string[]
  maxToolCalls?: number
  supportsWorkspaceEdits?: boolean
  workspaceApplyEnabled?: boolean
  projectExecutionEnabled?: boolean
  sourceIntegrationEnabled?: boolean
  projectRecipes?: { id: string; root: string; files: string[]; recipe: string }[]
  management?: OrganizationManagement
}
export interface OrganizationConnection {
  scope: string
  ownerScope?: string
  ownerRoute?: { connectionId: string; profile: string }
  state: 'connecting' | 'ready' | 'disconnected' | 'error'
  error?: string
  lastUpdatedAt?: string
}
/** Retained internal identities and messages; owner inspection never acknowledges delivery. */
export interface OrganizationConversation {
  id: string
  subject: string
  objectiveId: string
  taskId: string | null
  projectId: string | null
  participants: { id: string; name: string; team: string }[]
  status: 'waiting_reply' | 'answered' | 'needs_input' | 'cancelled'
  messages: {
    id: string
    senderId: string
    recipientId: string
    body: string
    createdAt: string
    readAt: string | null
    replyToId: string | null
  }[]
  waitingAgentId: string | null
}
export interface OrganizationSnapshot {
  conversations?: OrganizationConversation[]
  outcomes?: OrganizationOutcomePage
  attention?: OrganizationAttentionPage
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

export interface OrganizationProjectDraft {
  version: 1
  revision: string
  project: OrganizationProjectBinding
  yaml: string
}
export interface OrganizationProjectSave {
  version: 1
  revision: string
  project: OrganizationProjectBinding
  saved: true
}
export interface OrganizationProjectSaveInput {
  project: OrganizationProjectBinding
  expectedRevision: string
  idempotencyKey: string
  confirmSave: true
}
export interface OrganizationProjectSetup {
  version: 1
  revision: string
  projects: OrganizationProjectBinding[]
  storage?: 'profile-ledger'
  ledgerProjects?: OrganizationProjectBinding[]
  registryConflicts?: string[]
  repair?: string | null
  roots: string[]
  recipes: { id: string; root: string }[]
  teams: string[]
  blocked: boolean
  blockers: string[]
}
export interface RuntimeOrganizationAdapter extends OrganizationReader {
  openOwnerChat?(input: { agentId: string; identityId: string }): Promise<OwnerChatThread>
  readOwnerChat?(input: OwnerChatRead): Promise<OwnerChatThread>
  sendOwnerChat?(input: OwnerChatSend): Promise<OwnerChatThread>
  renewOwnerChat?(input: OwnerChatRenew): Promise<OwnerChatThread>
  cancelOwnerChat?(input: OwnerChatCancel): Promise<OwnerChatThread>
  getProjectSetup?(): Promise<OrganizationProjectSetup>
  prepareProjectDraft?(input: { project: OrganizationProjectBinding; expectedRevision: string }): Promise<OrganizationProjectDraft>
  saveProject?(input: OrganizationProjectSaveInput, signal?: AbortSignal): Promise<OrganizationProjectSave>
  readonly mode: 'runtime'
  getOutcomes?(input?: OrganizationOutcomeQuery): Promise<OrganizationOutcomePage>
  markOutcomeSeen?(input: OrganizationOutcomeSeen): Promise<void>
  getAttention?(input?: OrganizationAttentionQuery): Promise<OrganizationAttentionPage>
  markAttentionSeen?(input: OrganizationAttentionSeen): Promise<void>
  getHistory?(input?: HistoryQuery): Promise<HistoryPage>
  getHistoryObjective?(id: string): Promise<OrganizationSnapshot>
  setObjectiveArchived?(input: HistoryMutation): Promise<void>
  createObjective(title: string, metadata?: ObjectiveMetadata, idempotencyKey?: string): Promise<Objective>
  cancelObjective(id: string): Promise<void>
  retryRequest(id: string): Promise<void>
  resolveRequest(input: OrganizationResolutionInput): Promise<void>
  respondRequest(input: OrganizationResponseInput): Promise<void>
  configureOrganization(input: OrganizationConfigureInput): Promise<void>
  refresh(): Promise<void>
  getEvidence(id: string): Promise<OrganizationArtifact>
  getToolReceipts(requestId: string): Promise<OrganizationToolEvidence[]>
  getExecutionAudit?(requestId: string): Promise<OrganizationExecutionAudit>
}
export type OrganizationAdapter = StaticOrganizationAdapter | RuntimeOrganizationAdapter

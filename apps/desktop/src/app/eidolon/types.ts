export const objectiveStatusLabels = {
  planning: 'Planning', active: 'Active', waiting: 'Waiting', needs_input: 'Needs Input',
  blocked: 'Blocked', completed: 'Completed', paused: 'Paused', archived: 'Archived', cancelled: 'Cancelled',
} as const
export type ObjectiveStatus = keyof typeof objectiveStatusLabels
export type WorkStatus = 'working' | 'active' | 'thinking' | 'executing' | 'reviewing' | 'needs_input' | 'idle' | 'waiting' | 'review' | 'offline'
export interface ObjectiveMetadata {
  description?: string
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
export interface PrototypeOrganizationAdapter extends OrganizationReader {
  readonly mode: 'prototype'
  createObjective: (title: string, metadata?: ObjectiveMetadata) => Objective
  updateObjectiveMetadata(id: string, metadata: ObjectiveMetadata): void
  setObjectiveStatus(id: string, status: ObjectiveStatus): void
  recordOutcome(id: string, summary: string): void
  resolveDecision(id: string, status: 'approved' | 'rejected'): void
  loadDemo(): void
  reset(): void
}

export interface RuntimeOrganizationAdapter extends OrganizationReader {
  readonly mode: 'runtime'
  createObjective(title: string, metadata?: ObjectiveMetadata, idempotencyKey?: string): Promise<Objective>
  cancelObjective(id: string): Promise<void>
  retryRequest(id: string): Promise<void>
  refresh(): Promise<void>
  getEvidence(id: string): Promise<OrganizationArtifact>
  getToolReceipts(requestId: string): Promise<OrganizationToolEvidence[]>
}
export type OrganizationAdapter = PrototypeOrganizationAdapter | RuntimeOrganizationAdapter

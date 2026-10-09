/** The organization ledger owns these identities. They are never ordinary sessions. */
export interface OwnerChatMessage {
  id: string
  role: 'owner' | 'agent'
  text: string
  replyToMessageId: string | null
  turnId: string
  createdAt: string
}

export interface OwnerChatTurn {
  id: string
  idempotencyKey: string
  ownerMessageId: string
  replyMessageId: string | null
  status: 'pending' | 'running' | 'completed' | 'cancelled' | 'timed_out' | 'uncertain' | 'blocked'
  reason: string | null
  createdAt: string
  finishedAt: string | null
  context?: {
    includedMessageIds: string[] | null
    omittedMessageCount: number | null
    oldestIncludedMessageId: string | null
  }
}

export interface OwnerChatThread {
  id: string
  identityId: string
  agentId: string
  profile: string
  recipient: {
    id: string
    identityId: string
    name: string
    role: string
    team: string
    lifecycle: string
    provider: string | null
    model: string | null
  }
  messages: OwnerChatMessage[]
  turns: OwnerChatTurn[]
  latestMessageId?: string | null
  history?: { hasMore: boolean; oldestMessageId: string | null }
  policyGeneration?: number
  renewal?: {
    canRenew: boolean
    maxAdditionalCalls: number
    maxOutstandingCalls: number
    maxCumulativeCalls: number
    tokensPerCall: number
    unavailableReason: string | null
  }
  renewalReceipt?: {
    id: string
    idempotencyKey: string
    additionalCalls: number
    budgetVersion: number
    policyGeneration: number
    createdAt: string
  } | null
  activeTurnId: string | null
  canSend: boolean
  unavailableReason: string | null
  budget: {
    version?: number
    maxCalls: number
    callsReserved: number
    maxTokens: number
    tokensReserved: number
    remainingCalls: number
    remainingTokens: number
  }
  limits: { maxMessageChars: number; maxOutputTokens: number; timeoutSeconds: number }
}

export interface OwnerChatTarget {
  threadId: string
  identityId: string
}

export interface OwnerChatSend extends OwnerChatTarget {
  text: string
  replyToMessageId: string | null
  idempotencyKey: string
}

export interface OwnerChatCancel extends OwnerChatTarget {
  turnId: string
}

export interface OwnerChatRead extends OwnerChatTarget {
  beforeMessageId?: string | null
  limit?: number
}

export interface OwnerChatRenew extends OwnerChatTarget {
  idempotencyKey: string
  expectedBudgetVersion: number
  expectedPolicyGeneration: number
  additionalCalls: number
}

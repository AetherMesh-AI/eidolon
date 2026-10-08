/** Archive is reversible ledger visibility, independent of execution status. */
export interface HistoryState {
  archived: boolean
  revision: number
  updatedAt: string | null
  canArchive: boolean
  canRestore: boolean
  blocker: string | null
}

export interface HistoryCounts {
  current: number
  archived: number
  currentLimit: number
  archiveLimit: number
}

export interface HistoryPage {
  items: {
    id: string
    title: string
    createdAt: string
    status: 'cancelled' | 'completed' | 'open'
    history: HistoryState
  }[]
  nextCursor: string | null
  counts: HistoryCounts
}

export interface HistoryQuery {
  query?: string
  state?: 'archived' | 'current' | 'all'
  before?: string
  limit?: number
}

export interface HistoryMutation {
  id: string
  archived: boolean
  expectedRevision: number
  idempotencyKey?: string
}

export type ObjectivePriority = 'P1' | 'P2' | 'P3' | 'P4' | 'P5'
export interface PriorityChange {
  id: string
  priority: ObjectivePriority
  expectedRevision: number
  idempotencyKey: string
}
export interface PriorityReceipt {
  objectiveId: string
  idempotencyKey: string
  previousPriority: ObjectivePriority
  priority: ObjectivePriority
  revision: number
}

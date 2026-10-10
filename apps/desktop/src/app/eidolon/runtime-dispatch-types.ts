export interface DispatchState { paused: boolean; revision: number; runningCount: number }
export interface DispatchChange { id: string; paused: boolean; expectedRevision: number; idempotencyKey: string }
export interface DispatchReceipt { objectiveId: string; idempotencyKey: string; paused: boolean; revision: number }

export function validDispatch(value: DispatchState | undefined): value is DispatchState {
  return !!value && typeof value.paused === 'boolean' && Number.isSafeInteger(value.revision) && value.revision >= 0 && value.revision <= 100
    && Number.isSafeInteger(value.runningCount) && value.runningCount >= 0
}

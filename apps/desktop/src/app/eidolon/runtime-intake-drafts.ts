import { atom } from 'nanostores'

import type { ObjectiveMetadata, SubmissionReceipt } from './types'

export interface IntakeFields {
  goal: string
  criteria: string
  metadata: ObjectiveMetadata
}

export interface IntakeDraft extends IntakeFields {
  idempotencyKey?: string
  submitted?: IntakeFields
  busy?: boolean
  checking?: boolean
  receipt?: SubmissionReceipt
  submissionMissing?: boolean
  error?: string
}

// Window-local only, like owner chat drafts. Never persist supplied context to
// localStorage, a profile file, or another connection's draft. Reload clears it.
export const $intakeDrafts = atom<Record<string, IntakeDraft>>({})
export const emptyIntake: IntakeFields = { goal: '', criteria: '', metadata: {} }

export function setIntakeDraft(key: string, draft: IntakeDraft) {
  $intakeDrafts.set({ ...$intakeDrafts.get(), [key]: draft })
}

export function discardIntakeDraft(key: string) {
  const drafts = { ...$intakeDrafts.get() }
  delete drafts[key]
  $intakeDrafts.set(drafts)
}

export function acknowledgeIntake(key: string, idempotencyKey: string, sent: IntakeFields) {
  const draft = $intakeDrafts.get()[key]

  if (draft?.idempotencyKey !== idempotencyKey) {
    return
  }

  const fields = { goal: draft.goal, criteria: draft.criteria, metadata: draft.metadata }

  if (JSON.stringify(fields) === JSON.stringify(sent)) {
    discardIntakeDraft(key)
  } else {
    setIntakeDraft(key, fields)
  }
}

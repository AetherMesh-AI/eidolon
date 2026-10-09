import { atom } from 'nanostores'

import type { OwnerChatRenew, OwnerChatSend } from './runtime-owner-chat-types'

interface OwnerChatDraft {
  text: string
  intent?: OwnerChatSend
}

/** Window-local intent survives closing/reopening the inspector. The key contains
 * connection/profile owner scope and immutable identity; no transcript is stored. */
export const $ownerChatDrafts = atom<Record<string, OwnerChatDraft>>({})

export function setOwnerChatDraft(key: string, draft: OwnerChatDraft) {
  $ownerChatDrafts.set({ ...$ownerChatDrafts.get(), [key]: draft })
}

export function acknowledgeOwnerChatIntent(key: string, intent: OwnerChatSend) {
  const draft = $ownerChatDrafts.get()[key]

  if (draft?.intent?.idempotencyKey === intent.idempotencyKey) {
    setOwnerChatDraft(key, { text: draft.text === intent.text ? '' : draft.text })
  }
}

/** Preserve uncertain renewals across pane dismissal; retry never mints a new key. */
export const $ownerChatRenewals = atom<Record<string, OwnerChatRenew>>({})

export function setOwnerChatRenewal(key: string, intent: OwnerChatRenew) {
  $ownerChatRenewals.set({ ...$ownerChatRenewals.get(), [key]: intent })
}

export function acknowledgeOwnerChatRenewal(key: string, idempotencyKey: string) {
  if ($ownerChatRenewals.get()[key]?.idempotencyKey !== idempotencyKey) {
    return
  }

  const next = { ...$ownerChatRenewals.get() }
  delete next[key]
  $ownerChatRenewals.set(next)
}

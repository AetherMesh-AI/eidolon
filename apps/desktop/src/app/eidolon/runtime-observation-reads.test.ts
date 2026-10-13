import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter } from './runtime-adapter'
import type { RuntimeOrganizationAdapter } from './types'

const reads: Array<[string, (adapter: RuntimeOrganizationAdapter) => Promise<unknown>]> = [
  ['organization.attention', adapter => adapter.getAttention!()],
  ['organization.outcomes', adapter => adapter.getOutcomes!()],
  ['organization.history', adapter => adapter.getHistory!()],
  ['organization.historyObjective', adapter => adapter.getHistoryObjective!('objective')],
  ['organization.ownerChat.read', adapter => adapter.readOwnerChat!({ threadId: 'thread', identityId: 'identity' })],
  ['organization.projectSetup', adapter => adapter.getProjectSetup!()],
  ['organization.projectDraft', adapter => adapter.prepareProjectDraft!({ project: {} as never, expectedRevision: 'revision' })],
  ['organization.evidence', adapter => adapter.getEvidence('evidence')],
  ['organization.toolReceipts', adapter => adapter.getToolReceipts('request')],
  ['organization.executionAudit', adapter => adapter.getExecutionAudit!('request')],
  ['organization.previewReplacement', adapter => adapter.previewReplacement!('objective')],
  ['organization.checkSubmission', adapter => adapter.checkSubmission!('key')],
]

it.each(reads)('does not fall back from passive %s to the execution/reconnect transport', async (method, read) => {
  const request = vi.fn().mockRejectedValue(new Error('Forbidden legacy request'))
  const observeRead = vi.fn().mockRejectedValue(new Error('Observation unavailable'))
  const observe = vi.fn().mockResolvedValue({ source: 'runtime', objectives: [], agents: [], tasks: [], activity: [], knowledge: [], requests: [], runtime: { profile: 'default', state: 'ready', maxWorkers: 1, capabilities: [], scope: '' } })
  const adapter = createRuntimeAdapter({ request, observeRead, observe, getScope: () => ({ key: 'selected', connected: true, observationOnly: true }), subscribeScope: () => () => {} })
  await adapter.refresh()
  await expect(read(adapter)).rejects.toThrow()
  expect(observeRead).toHaveBeenCalledExactlyOnceWith(method, expect.any(Object), expect.any(AbortSignal))
  expect(request).not.toHaveBeenCalled()
})

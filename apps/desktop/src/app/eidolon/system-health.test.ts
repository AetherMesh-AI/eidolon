import { expect, it } from 'vitest'

import type { RuntimeReadinessResult } from '@/lib/runtime-readiness'

import { systemHealth } from './system-health'
import type { OrganizationSnapshot } from './types'

const readiness: RuntimeReadinessResult = { ready: true, checksDisagree: false, source: 'runtime_check', reason: null }

const snapshot: OrganizationSnapshot = {
  source: 'runtime',
  objectives: [],
  tasks: [],
  requests: [],
  agents: [],
  knowledge: [],
  activity: [],
  connection: { scope: 'a', state: 'ready' },
  runtime: {
    state: 'ready',
    capabilities: [],
    maxWorkers: 2,
    scope: 'Recorded',
    setup: {
      version: 1,
      backgroundOptIn: true,
      provider: { status: 'unchecked', blockers: [], inheritedMembers: 0, overriddenMembers: 0 }
    }
  }
}

it('requires current transport, runtime and provider evidence before reporting ready', () => {
  expect(systemHealth('open', undefined, readiness, true, 0)).toBe('unknown')
  expect(systemHealth('closed', snapshot, readiness, true, 0)).toBe('disconnected')
  expect(systemHealth('connecting', snapshot, readiness, true, 0)).toBe('unknown')
  expect(systemHealth('open', snapshot, null, false, 0)).toBe('unknown')
  expect(systemHealth('open', snapshot, readiness, false, 0)).toBe('stale')
  expect(systemHealth('open', snapshot, readiness, true, 0)).toBe('healthy')
  expect(systemHealth('open', snapshot, { ...readiness, source: 'setup_status' }, true, 0)).toBe('unknown')
  expect(systemHealth('open', snapshot, readiness, true, 2)).toBe('attention')
  expect(systemHealth('open', snapshot, readiness, true, 0, { staleWork: true })).toBe('stale')
  expect(systemHealth('open', snapshot, readiness, true, 0, { failedAgents: 1 })).toBe('degraded')
})

it('keeps disabled, degraded and stale runtime states distinct from provider readiness', () => {
  for (const [state, expected] of [
    ['disabled', 'disabled'],
    ['error', 'degraded'],
    ['unrecognized', 'unknown']
  ] as const) {
    expect(systemHealth('open', { ...snapshot, runtime: { ...snapshot.runtime!, state } }, readiness, true, 0)).toBe(
      expected
    )
  }

  expect(systemHealth('open', { ...snapshot, connection: { scope: 'a', state: 'error' } }, readiness, true, 0)).toBe(
    'degraded'
  )
  expect(
    systemHealth('open', { ...snapshot, connection: { scope: 'a', state: 'connecting' } }, readiness, true, 0)
  ).toBe('stale')
  expect(systemHealth('open', snapshot, { ...readiness, ready: false }, true, 0)).toBe('degraded')
  expect(systemHealth('open', snapshot, { ...readiness, checksDisagree: true }, true, 0)).toBe('degraded')
})

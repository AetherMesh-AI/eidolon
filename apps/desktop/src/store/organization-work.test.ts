import { beforeEach, describe, expect, it } from 'vitest'

import type { OrganizationRequest, OrganizationSnapshot } from '@/app/eidolon/types'

import {
  $organizationWork,
  clearOrganizationWork,
  organizationWorkSummary,
  publishOrganizationWork
} from './organization-work'

function snapshot(
  owner = 'profile-a',
  statuses: OrganizationRequest['status'][] = ['running', 'queued']
): OrganizationSnapshot {
  return {
    source: 'runtime',
    objectives: [
      {
        id: 'objective',
        title: owner,
        source: 'runtime',
        ownerId: 'agent',
        description: '',
        createdAt: '',
        status: 'active'
      }
    ],
    requests: statuses.map((status, id) => ({
      id: String(id),
      status,
      objectiveId: 'objective',
      type: 'work',
      team: 'general',
      priority: 1,
      attempts: 1,
      createdAt: ''
    })),
    connection: { scope: `socket-${owner}`, ownerScope: owner, state: 'ready' },
    tasks: [],
    agents: [],
    knowledge: [],
    activity: []
  }
}

beforeEach(clearOrganizationWork)

describe('organization active work', () => {
  it('counts requests once, not their task/agent mirrors; separates attention from work', () => {
    expect(
      organizationWorkSummary(
        snapshot('profile-a', ['running', 'queued', 'pending_intervention', 'completed', 'cancelled'])
      )
    ).toMatchObject({ count: 2, titles: ['profile-a'], running: 1, queued: 1, needsYou: 1, stale: false })
  })

  it('ignores the retired local preview', () => {
    publishOrganizationWork({ ...snapshot(), source: 'prototype' })
    expect($organizationWork.get().count).toBe(0)
  })

  it('keeps last-known work during a disconnect and clears only after reconciliation', () => {
    publishOrganizationWork(snapshot())
    publishOrganizationWork({
      ...snapshot('profile-a', []),
      connection: { scope: 'lost-socket', ownerScope: 'profile-a', state: 'disconnected' }
    })
    expect($organizationWork.get()).toMatchObject({ count: 2, stale: true })
    publishOrganizationWork(snapshot('profile-a', ['completed']))
    expect($organizationWork.get()).toMatchObject({ count: 0, stale: false })
  })

  it('retains conservative background-profile counts without putting old records in the new profile', () => {
    publishOrganizationWork(snapshot('profile-a'))
    publishOrganizationWork(snapshot('profile-b', ['running']))
    expect($organizationWork.get()).toMatchObject({ count: 3, stale: true })
    publishOrganizationWork(snapshot('profile-a', ['completed']))
    expect($organizationWork.get()).toMatchObject({ count: 1, titles: ['profile-b'], stale: true })
    publishOrganizationWork(snapshot('profile-b', ['completed']))
    expect($organizationWork.get()).toMatchObject({ count: 0, stale: false })
  })

  it('publishes only work facts, never connection credentials or raw request data', () => {
    const state = snapshot('https://user:secret@host/path?token=private')
    state.objectives[0].title = 'Safe task title'
    state.connection!.error = 'secret token'
    state.requests![0].reason = 'confidential diagnostics'
    publishOrganizationWork(state)
    const value = JSON.stringify($organizationWork.get())
    expect(value).not.toMatch(/secret|private|confidential|https/)
    expect(value).toContain('Safe task title')
  })
})

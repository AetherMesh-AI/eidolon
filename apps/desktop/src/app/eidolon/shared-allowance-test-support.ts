import { vi } from 'vitest'

import { createSharedAllowanceClient } from './organization-shared-allowance-client'
import type {
  AllowanceAmounts,
  SharedAllowanceAction,
  SharedAllowanceConfiguration,
  SharedAllowanceDescriptor,
  SharedAllowanceIntent,
  SharedAllowancePreview,
  SharedAllowanceReceipt,
  SharedAllowanceRequest,
  SharedAllowanceScope
} from './organization-shared-allowance-types'

export const rootId = '11111111-1111-4111-8111-111111111111'
export const rootOwner = '22222222-2222-4222-8222-222222222222'
export const memberId = '33333333-3333-4333-8333-333333333333'
export const memberOwner = '44444444-4444-4444-8444-444444444444'
export const amounts = (
  modelCalls: number,
  tokens = modelCalls * 1000,
  configuredCostUsd: string | null = null
): AllowanceAmounts => ({ modelCalls, tokens, configuredCostUsd })

export function allowanceConfiguration(): SharedAllowanceConfiguration {
  return {
    periodId: 'explicit-period',
    startsAt: '2026-10-14T00:00:00+00:00',
    endsAt: '2026-10-15T00:00:00+00:00',
    limits: amounts(20),
    protected: amounts(4),
    allocations: [
      { profile: 'default', organizationId: rootId, ownerId: rootOwner, limits: amounts(8), protected: amounts(2) },
      {
        profile: 'research',
        organizationId: memberId,
        ownerId: memberOwner,
        limits: amounts(12),
        protected: amounts(2)
      }
    ]
  }
}

export function allowanceDescriptor(configured = true): SharedAllowanceDescriptor {
  return {
    capabilityVersion: 1,
    state: configured ? 'ready' : 'unconfigured',
    scope: { kind: 'served-profile', profile: 'default' },
    organizationId: rootId,
    ownerId: rootOwner,
    authority: 'installation-owner',
    generation: configured ? 3 : 0,
    sourceRevision: 'a'.repeat(64),
    configuration: configured ? allowanceConfiguration() : null,
    candidates: [
      {
        profile: 'default',
        organizationId: rootId,
        ownerId: rootOwner,
        displayName: 'Root organization',
        ownerDisplayName: 'Owner'
      },
      {
        profile: 'research',
        organizationId: memberId,
        ownerId: memberOwner,
        displayName: 'Research organization',
        ownerDisplayName: 'Research owner'
      }
    ],
    pool: configured
      ? {
          limits: amounts(20),
          reserved: amounts(3),
          remaining: amounts(17),
          protectedRemaining: amounts(4),
          ordinaryRemaining: amounts(13)
        }
      : null,
    member: configured
      ? {
          limits: amounts(8),
          reserved: amounts(1),
          remaining: amounts(7),
          protectedRemaining: amounts(2),
          ordinaryRemaining: amounts(5)
        }
      : null,
    observation: null,
    pacing: null,
    managed: false,
    capabilities: { configuration: true, observation: configured }
  }
}

export function allowancePreview(
  data: SharedAllowanceDescriptor,
  action: SharedAllowanceAction,
  params: SharedAllowanceRequest
): SharedAllowancePreview {
  const candidate = structuredClone(params[action])

  if (action === 'configuration') {
    ;(candidate as SharedAllowanceConfiguration).allocations.sort((a, b) => a.profile.localeCompare(b.profile))
  }

  return {
    capabilityVersion: 1,
    scope: data.scope,
    organizationId: data.organizationId,
    ownerId: data.ownerId,
    generation: params.expectedGeneration,
    sourceRevision: params.sourceRevision,
    previewVersion: 'b'.repeat(64),
    [action]: candidate,
    changes: [{ field: action, before: data[action], after: candidate }],
    warnings: [
      'owner_declared_allowance',
      'configured_prices_not_invoice',
      'no_refunds_or_auto_reset',
      'existing_local_limits_still_apply',
      'enrollment_requires_idle'
    ].map(code => ({ code: code as SharedAllowancePreview['warnings'][number]['code'], values: {} })),
    impact: { scope: 'installation-allowance', reservations: 'unchanged', automaticReset: false }
  }
}

export function allowanceReceipt(
  data: SharedAllowanceDescriptor,
  intent: SharedAllowanceIntent
): SharedAllowanceReceipt {
  return {
    capabilityVersion: 1,
    scope: data.scope,
    receipt: {
      action: intent.action,
      idempotencyKey: intent.idempotencyKey,
      organizationId: intent.expectedOrganizationId,
      ownerId: intent.expectedOwnerId,
      generation: intent.expectedGeneration + 1,
      sourceRevision: intent.sourceRevision,
      previewVersion: intent.previewVersion,
      [intent.action]: structuredClone(intent[intent.action])
    }
  }
}

let nextId = 0

export async function allowanceHarness(initial = allowanceDescriptor()) {
  let data = initial
  const values = new Map<string, string>()

  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value)
    },
    removeItem: (key: string) => {
      values.delete(key)
    }
  }

  const newKey = vi.fn(() => `original-intent-${++nextId}`)
  const receipts = new Map<string, SharedAllowanceReceipt>()

  const perform = async (method: string, params: Record<string, unknown>): Promise<unknown> => {
    if (method === 'organization.sharedAllowance') {
      return structuredClone(data)
    }

    const action = method.includes('Observation') ? 'observation' : 'configuration'

    if (method.includes('preview')) {
      return allowancePreview(data, action, params as unknown as SharedAllowanceRequest)
    }

    if (receipts.has(params.idempotencyKey as string)) {
      return receipts.get(params.idempotencyKey as string)
    }

    const intent = { ...params, action } as unknown as SharedAllowanceIntent
    const receipt = allowanceReceipt(data, intent)
    receipts.set(intent.idempotencyKey, receipt)
    data = {
      ...data,
      state: 'ready',
      generation: intent.expectedGeneration + 1,
      sourceRevision: 'c'.repeat(64),
      capabilities: { configuration: true, observation: true },
      ...(action === 'configuration'
        ? { configuration: intent.configuration! }
        : {
            observation: {
              ...intent.observation!,
              revision: intent.expectedGeneration + 1,
              status: 'fresh',
              source: 'owner_manual',
              scope: 'installation',
              advisory: true
            }
          })
    }

    return receipt
  }

  const request = vi.fn(perform)

  const scope: SharedAllowanceScope = {
    key: 'socket-a',
    ownerKey: `allowance-owner-${++nextId}`,
    profile: initial.scope.profile,
    eligible: true,
    organizationId: initial.organizationId,
    ownerId: initial.ownerId,
    request: request as SharedAllowanceScope['request']
  }

  const client = createSharedAllowanceClient(() => scope, newKey, storage)
  await client.read()

  return {
    client,
    request,
    scope,
    data: initial,
    values,
    newKey,
    storage,
    perform,
    setData: (next: SharedAllowanceDescriptor) => {
      data = next
    }
  }
}

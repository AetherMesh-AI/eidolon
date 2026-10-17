import type { MetadataScope } from './organization-metadata-client'
import { createTeamOnboardingClient, type TeamSetup } from './team-onboarding-client'
import type { SubscriptionStatus } from './team-setup-subscription-client'

export interface SetupConnectionSnapshot {
  setup: TeamSetup
  status: SubscriptionStatus | null
}

/** Lifecycle only. Never exposes consent, refresh, models, policy or inference. */
export function createOrganizationSetupConnectionClient(
  readScope: () => MetadataScope,
  subscribe: (changed: () => void) => () => void
) {
  const client = createTeamOnboardingClient(readScope, subscribe)
  let busy = false

  const read = async (organizationId: string): Promise<SetupConnectionSnapshot> => {
    const setup = await client.setup(organizationId)

    if (
      setup.capabilityVersion !== 1 ||
      !['available', 'configured'].includes(setup.state) ||
      setup.organizationId !== organizationId ||
      typeof setup.sourceRevision !== 'string' ||
      !/^[a-f0-9]{64}$/.test(setup.sourceRevision) ||
      setup.setupSubscriptionVersion !== 1 ||
      setup.setupSubscriptionState !== 'available'
    ) {
      throw new Error('setup_connection_unavailable')
    }

    let status: SubscriptionStatus | null = null

    try {
      status = await client.subscriptionStatus(setup)
    } catch {
      if (!client.current()) {
        throw new Error('stale')
      }

      // The exact owner/source is verified, but a damaged retained transition
      // may prevent status reads. Keep explicit recovery reachable, without
      // inventing a revision or permitting Disconnect on unverified evidence.
    }

    return { setup, status }
  }

  const act = async (run: () => Promise<SetupConnectionSnapshot>) => {
    if (busy || !client.current()) {
      throw new Error('stale')
    }

    busy = true

    try {
      const result = await run()

      if (!client.current()) {
        throw new Error('stale')
      }

      return result
    } finally {
      busy = false
    }
  }

  const revalidate = async (previous: SetupConnectionSnapshot) => {
    const next = await read(previous.setup.organizationId!)

    // A click on old information cannot become authority for another source/grant.
    if (
      next.setup.sourceRevision !== previous.setup.sourceRevision ||
      next.status?.connectionRevision !== previous.status?.connectionRevision ||
      next.status?.connectionState !== previous.status?.connectionState
    ) {
      throw new Error('stale')
    }

    return next
  }

  return {
    current: client.current,
    dispose: client.dispose,
    inspect: (organizationId: string) => act(() => read(organizationId)),
    disconnect: (previous: SetupConnectionSnapshot) => act(async () => {
      const { setup, status } = await revalidate(previous)

      if (!status) {
        throw new Error('setup_connection_unavailable')
      }

      const prepared = await client.prepareSubscriptionOperation(setup, 'revoke', status.connectionRevision)

      if (!client.current()) {
        throw new Error('stale')
      }

      // A stranded refresh may already have been retired locally by prepare.
      // Execute only a validated revoke ticket; never replay uncertain work.
      if (prepared.state === 'prepared' && prepared.ticket) {
        await client.executeSubscriptionOperation(setup, prepared.ticket)
      }

      return read(setup.organizationId!)
    }),
    recover: (previous: SetupConnectionSnapshot) => act(async () => {
      const { setup } = await revalidate(previous)
      await client.recoverSubscriptionOperation(setup)

      return read(setup.organizationId!)
    })
  }
}

export type OrganizationSetupConnectionClient = ReturnType<typeof createOrganizationSetupConnectionClient>

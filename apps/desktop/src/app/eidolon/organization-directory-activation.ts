import { type ConnectionSwitchOutcome } from '@/store/connections'
import { selectOrganizationConnection } from '@/store/organization-switch'

import type { DirectorySource, OrganizationDirectory, OrganizationDirectoryEntry } from './organization-directory'
import { organizationEndpointIdentity } from './organization-owner'

export type ActivationResult = {
  status:
    | 'disabled'
    | 'cancelled'
    | 'unavailable'
    | 'superseded'
    | 'verified'
    | 'selected-unconfigured'
    | 'committed-unverified'
    | 'unchanged'
  outcome?: ConnectionSwitchOutcome
}
const endpoint = (value: string | null) => (value === null ? null : organizationEndpointIdentity(value))

/** Explicit preparation and confirmation. The default release gate is closed. */
export function createOrganizationDirectoryActivation({
  directory,
  readSource,
  enabled = () => false,
  select = selectOrganizationConnection
}: {
  directory: OrganizationDirectory
  readSource: () => DirectorySource
  enabled?: () => boolean
  select?: typeof selectOrganizationConnection
}) {
  let prepared: { entry: OrganizationDirectoryEntry; owner: string; scope: string } | null = null
  let busy = false

  return {
    prepare(key: string) {
      if (busy) {
        return false
      }

      const entry = directory.getSnapshot().entries.find(item => item.key === key)
      const current = readSource().current

      if (!entry || entry.state === 'unsupported' || !current?.scope.eligible) {
        return false
      }

      prepared = { entry: { ...entry }, owner: current.scope.ownerKey, scope: current.scope.key }

      return true
    },
    cancel() {
      // The reviewed switch API cannot undo publication. Never pretend to cancel an admitted switch.
      if (busy) {
        return false
      }

      prepared = null

      return true
    },
    async confirm(): Promise<ActivationResult> {
      if (!enabled()) {
        return { status: 'disabled' }
      }

      if (busy) {
        return { status: 'unavailable' }
      }

      if (!prepared) {
        return { status: 'cancelled' }
      }

      const { entry, owner, scope } = prepared
      const source = readSource()
      const live = directory.getSnapshot().entries.find(item => item.key === entry.key)
      const registered = source.connections.find(item => item.id === entry.connectionId)

      if (
        !source.current?.scope.eligible ||
        source.current.scope.key !== scope ||
        source.current.scope.ownerKey !== owner
      ) {
        return { status: 'superseded' }
      }

      if (
        !live ||
        live.state === 'unsupported' ||
        live.organizationId !== entry.organizationId ||
        !registered ||
        live.endpointIdentity !== entry.endpointIdentity ||
        endpoint(registered.endpoint) !== entry.endpointIdentity
      ) {
        return { status: 'unavailable' }
      }

      busy = true
      prepared = null

      try {
        const outcome = await select(entry.connectionId, entry.profile)

        if (outcome.status === 'superseded') {
          return { status: 'superseded', outcome }
        }

        if (outcome.status === 'unchanged') {
          return { status: 'unchanged', outcome }
        }

        const active = readSource().current

        if (
          active?.connectionId !== entry.connectionId ||
          active.scope.profile !== entry.profile ||
          endpoint(active.endpoint) !== entry.endpointIdentity ||
          !active.scope.eligible
        ) {
          return { status: 'committed-unverified', outcome }
        }

        const refreshed = await directory.refresh()
        const finalScope = readSource().current

        if (finalScope?.scope.key !== active.scope.key || finalScope.scope.ownerKey !== active.scope.ownerKey) {
          return { status: 'superseded', outcome }
        }

        const verified = directory
          .getSnapshot()
          .entries.find(item => item.key === entry.key && item.freshness === 'fresh')

        if (refreshed !== 'verified' || !verified || verified.organizationId !== entry.organizationId) {
          return { status: 'committed-unverified', outcome }
        }

        if (entry.state === 'unconfigured' && verified.state === 'unconfigured' && verified.organizationId === null) {
          return { status: 'selected-unconfigured', outcome }
        }

        return {
          status:
            entry.state === 'ready' && verified.state === 'ready' && verified.organizationId !== null
              ? 'verified'
              : 'committed-unverified',
          outcome
        }
      } finally {
        busy = false
      }
    }
  }
}

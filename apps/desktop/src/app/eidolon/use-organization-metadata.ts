import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useSyncExternalStore } from 'react'

import { $pendingConnectionId } from '@/store/connections'
import { $activeGatewayRoute, $gateway } from '@/store/gateway'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'
import { $connection, $gatewayState } from '@/store/session'

import { createOrganizationMetadataClient, type MetadataScope } from './organization-metadata-client'
import { createOrganizationScopeReader } from './runtime-provider'

const readOwnerScope = createOrganizationScopeReader()

function currentScope(): MetadataScope {
  const owner = readOwnerScope()
  const gateway = $gateway.get()
  const connection = $connection.get()

  return {
    key: owner.key,
    ownerKey: owner.ownerKey,
    profile: $activeGatewayProfile.get(),
    eligible:
      owner.connected &&
      Boolean(gateway) &&
      !connection?.sharedPrimary &&
      !connection?.sharedRemote &&
      !$pendingConnectionId.get(),
    request: (method, params) => {
      if (!gateway) {
        return Promise.reject(new Error('Gateway unavailable'))
      }

      // Capture the authenticated socket once. No reconnect or retargeting helper.
      return gateway.request(method, params, 15000)
    }
  }
}

const client = createOrganizationMetadataClient(currentScope, () => crypto.randomUUID(), {
  getItem: key => sessionStorage.getItem(key),
  setItem: (key, value) => sessionStorage.setItem(key, value),
  removeItem: key => sessionStorage.removeItem(key)
})

export function useOrganizationMetadata() {
  const gateway = useStore($gateway)
  const route = useStore($activeGatewayRoute)
  const profile = useStore($activeGatewayProfile)
  const swap = useStore($gatewaySwapTarget)
  const connection = useStore($connection)
  const state = useStore($gatewayState)
  const pending = useStore($pendingConnectionId)
  // The compiler cannot infer dependencies hidden behind store.get().
  const scope = useMemo(currentScope, [gateway, route, profile, swap, connection, state, pending])
  const view = useSyncExternalStore(client.subscribe, client.getSnapshot)
  useEffect(() => {
    if (scope.eligible) {
      void client.read()
    }
  }, [scope.key, scope.eligible])

  return { client, view, scope }
}

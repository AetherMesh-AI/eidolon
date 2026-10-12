import { $connectionsRegistry, $pendingConnectionId } from '@/store/connections'
import { $activeGatewayRoute, $gateway } from '@/store/gateway'
import { $organizationObservationEpoch, $organizationObservationSwitching } from '@/store/organization-observation-mode'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'
import { $connection, $gatewayState } from '@/store/session'

import { createOrganizationDirectory, type DirectorySource } from './organization-directory'
import { readOrganizationMetadataScope } from './use-organization-metadata'

/** Store observation only: no registry initialization, profile enumeration or dialing. */
export function createRuntimeOrganizationDirectory() {
  let registry = $connectionsRegistry.get()
  let registryGeneration = 0

  const readSource = (): DirectorySource => {
    const next = $connectionsRegistry.get()

    if (registry !== next) {
      registry = next
      registryGeneration++
    }

    const connection = $connection.get()
    const scope = readOrganizationMetadataScope()

    const connections = (registry?.connections ?? [])
      .filter(item => item.kind === 'local' || item.kind === 'remote')
      .map(item => ({ id: item.id, label: item.label, endpoint: item.kind === 'remote' ? (item.url ?? '') : null }))

    return {
      connections,
      current: connection?.connectionId
        ? {
            connectionId: connection.connectionId,
            endpoint: connection.mode === 'remote' ? connection.baseUrl : null,
            scope: { ...scope, key: `${scope.key}:registry-${registryGeneration}` }
          }
        : null
    }
  }

  const directory = createOrganizationDirectory(readSource, subscribeOrganizationDirectorySource)

  return { directory, readSource }
}

export function subscribeOrganizationDirectorySource(changed: () => void) {
  const stores = [
    $connectionsRegistry,
    $pendingConnectionId,
    $activeGatewayRoute,
    $gateway,
    $organizationObservationEpoch,
    $organizationObservationSwitching,
    $activeGatewayProfile,
    $gatewaySwapTarget,
    $connection,
    $gatewayState
  ]

  const unsubscribe = stores.map(store => store.listen(changed))

  return () => {
    for (const stop of unsubscribe) {
      stop()
    }
  }
}

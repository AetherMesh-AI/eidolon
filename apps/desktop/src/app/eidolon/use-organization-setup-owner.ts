import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'

import { $connectionsRegistry, $pendingConnectionId } from '@/store/connections'
import {
  $activeGatewayRoute,
  $gateway,
  $gatewayTransportRevision,
  $localOwnerProofRevision,
  gatewayTransportGeneration
} from '@/store/gateway'
import { $gatewaySwitching } from '@/store/gateway-switch'
import { $organizationObservationEpoch, $organizationObservationSwitching } from '@/store/organization-observation-mode'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'
import { $connection, $gatewayState } from '@/store/session'
import { $sourceSwitchIntent } from '@/store/source-switch-intent'

import { isolatedWorkspaceIdentity } from './organization-creation-contract'
import { readOrganizationMetadataScope } from './use-organization-metadata'

/** Only live strict workspace proof can give its supported setup form precedence. */
export function useOrganizationSetupOwner() {
  const gateway = useStore($gateway)
  const connection = useStore($connection)
  const registry = useStore($connectionsRegistry)
  const profile = useStore($activeGatewayProfile)
  const intent = useStore($sourceSwitchIntent)
  const state = useStore($gatewayState)
  const switching = useStore($gatewaySwitching)
  const pending = useStore($pendingConnectionId)
  const swap = useStore($gatewaySwapTarget)
  const transportRevision = useStore($gatewayTransportRevision)
  const proofRevision = useStore($localOwnerProofRevision)
  const route = useStore($activeGatewayRoute)
  const observationEpoch = useStore($organizationObservationEpoch)
  const observationSwitching = useStore($organizationObservationSwitching)

  // Store reads and same-object reconnects are invisible to the React Compiler.
  // Keep every authority revision in the snapshot, even when its scalar key is unchanged.
  const scope = useMemo(readOrganizationMetadataScope, [gateway, connection, registry, profile, intent, state, switching, pending, swap, transportRevision, proofRevision, route, observationEpoch, observationSwitching])
  const eligible = scope.eligible && state === 'open' && !switching && !pending && !swap

  const source = useMemo(() => {
    const generation = gateway ? gatewayTransportGeneration(gateway) : undefined

    const current = () => {
      const live = readOrganizationMetadataScope()

      return Boolean(
        eligible &&
        typeof generation === 'number' &&
        Number.isFinite(generation) &&
        gateway &&
        gateway.connectionState === 'open' &&
        $gateway.get() === gateway &&
        $connection.get() === connection &&
        $connectionsRegistry.get() === registry &&
        $activeGatewayProfile.get() === profile &&
        $sourceSwitchIntent.get() === intent &&
        $gatewayState.get() === 'open' &&
        !$gatewaySwitching.get() &&
        !$pendingConnectionId.get() &&
        !$gatewaySwapTarget.get() &&
        gatewayTransportGeneration(gateway) === generation &&
        live.eligible &&
        live.key === scope.key &&
        live.ownerKey === scope.ownerKey &&
        connection?.mode === 'local' &&
        !connection.sharedPrimary &&
        !connection.sharedRemote &&
        connection.profile === profile &&
        registry?.connections.some(item => item.id === connection.connectionId && item.kind === 'local')
      )
    }

    return { gateway, profile, current }
  }, [gateway, connection, registry, profile, intent, eligible, scope])

  const [verified, setVerified] = useState<typeof source | null>(null)
  useEffect(() => {
    if (!source.current() || !source.gateway) {
      return
    }

    const controller = new AbortController()
    void source.gateway
      .request('organization.descriptor', {}, 15_000, controller.signal)
      .then(value => {
        if (controller.signal.aborted || !source.current()) {
          return
        }

        isolatedWorkspaceIdentity(value, source.profile)
        setVerified(source)
      })
      .catch(() => {
        /* Missing, unsupported or foreign proof leaves ordinary onboarding in charge. */
      })

    return () => controller.abort()
  }, [source])

  // A retired proof cannot suppress even one render on a replacement source.
  return verified === source && source.current()
}

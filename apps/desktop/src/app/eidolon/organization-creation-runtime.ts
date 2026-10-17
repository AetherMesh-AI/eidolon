import { saveHermesConfigRecord } from '@/eidolon'
import { $connectionsRegistry } from '@/store/connections'
import { $gateway, type GatewayActivationVerifier, gatewayTransportGeneration } from '@/store/gateway'
import { $connection } from '@/store/session'

import { createOrganizationCreationClient, createOrganizationModelSetupClient } from './organization-creation-client'
import type { OrganizationDirectoryEntry } from './organization-directory'
import type {
  createRuntimeOrganizationDirectory} from './organization-directory-runtime';
import {
  subscribeOrganizationDirectorySource
} from './organization-directory-runtime'
import type { MetadataDescriptor } from './organization-metadata-client'
import { persistedOrganizationVerifier, selectRuntimeOrganizationConnection } from './organization-selection-runtime'

/** The passed gateway is the dedicated socket that will be committed, never a routing helper. */
export function organizationActivationVerifier(verify: (value: unknown) => void): GatewayActivationVerifier {
  const originGateway = $gateway.get()
  const originConnection = $connection.get()
  const registry = $connectionsRegistry.get()
  const generation = originGateway ? gatewayTransportGeneration(originGateway) : undefined

  const current = () =>
    originGateway &&
    originGateway.connectionState === 'open' &&
    $gateway.get() === originGateway &&
    $connection.get() === originConnection &&
    $connectionsRegistry.get() === registry &&
    gatewayTransportGeneration(originGateway) === generation

  return persistedOrganizationVerifier(() => Boolean(current()), verify)
}

export function directoryActivationVerifier(entry: OrganizationDirectoryEntry): GatewayActivationVerifier {
  return organizationActivationVerifier(value => {
    const descriptor = value as MetadataDescriptor

    if (
      descriptor?.capabilityVersion !== 1 ||
      descriptor.scope?.kind !== 'served-profile' ||
      descriptor.scope.profile !== entry.profile ||
      descriptor.state !== entry.state ||
      (descriptor.metadata?.organizationId ?? null) !== entry.organizationId ||
      descriptor.state === 'unsupported'
    ) {
      throw new Error('Destination organization identity could not be verified')
    }
  })
}

function creationEnvironment(runtime: ReturnType<typeof createRuntimeOrganizationDirectory>) {
  const gateway = $gateway.get()
  const connection = $connection.get()
  const registry = $connectionsRegistry.get()
  const generation = gateway ? gatewayTransportGeneration(gateway) : undefined
  let lastConnection = connection
  let connectionRevision = 0

  return {
    readSource: () => {
      const source = runtime.readSource()
      const active = $gateway.get()

      if ($connection.get() !== lastConnection) {
        lastConnection = $connection.get()
        connectionRevision++
      }

      // Same-object reconnects and descriptor replacements revoke outstanding creation reads.
      const current = source.current
        ? {
            ...source.current,
            scope: {
              ...source.current.scope,
              key: `${source.current.scope.key}:creation-${connectionRevision}-${active ? gatewayTransportGeneration(active) : 'none'}`
            }
          }
        : null

      // v1 deliberately supports only independently verified local profile sockets.
      return {
        ...source,
        current,
        ready: source.ready !== false && $connection.get()?.mode === 'local' && active?.connectionState === 'open'
      }
    },
    canRestoreOrigin: () =>
      Boolean(
        gateway &&
        gateway.connectionState === 'open' &&
        $gateway.get() === gateway &&
        $connection.get() === connection &&
        $connectionsRegistry.get() === registry &&
        gatewayTransportGeneration(gateway) === generation
      ),
    subscribeSource: subscribeOrganizationDirectorySource,
    rememberCurrent: () => runtime.directory.refresh(),
    saveModel: (profile: string, model: string) =>
      saveHermesConfigRecord(
        { model: { provider: 'openai-api', default: model } },
        { connectionId: connection?.connectionId, profile }
      ),
    storage: {
      getItem: (key: string) => localStorage.getItem(key),
      setItem: (key: string, value: string) => localStorage.setItem(key, value),
      removeItem: (key: string) => localStorage.removeItem(key)
    },
    activate: (connectionId: string, profile: string, verify: (value: unknown) => void) =>
      selectRuntimeOrganizationConnection(connectionId, profile, organizationActivationVerifier(verify))
  }
}

export function createRuntimeOrganizationCreation(runtime: ReturnType<typeof createRuntimeOrganizationDirectory>) {
  return createOrganizationCreationClient(creationEnvironment(runtime))
}

export function createRuntimeOrganizationModelSetup(runtime: ReturnType<typeof createRuntimeOrganizationDirectory>) {
  return createOrganizationModelSetupClient(creationEnvironment(runtime))
}

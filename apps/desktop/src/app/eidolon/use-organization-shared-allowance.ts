import { useEffect, useSyncExternalStore } from 'react'

import { createSharedAllowanceClient } from './organization-shared-allowance-client'
import type { SharedAllowanceScope } from './organization-shared-allowance-types'
import {
  organizationMetadataClient,
  readOrganizationMetadataScope,
  useOrganizationMetadata
} from './use-organization-metadata'

export function readSharedAllowanceScope(): SharedAllowanceScope {
  const scope = readOrganizationMetadataScope()
  const metadata = organizationMetadataClient.getSnapshot().descriptor?.metadata

  return { ...scope, organizationId: metadata?.organizationId ?? null, ownerId: metadata?.owner.ownerId ?? null }
}

export const sharedAllowanceClient = createSharedAllowanceClient(readSharedAllowanceScope, () => crypto.randomUUID(), {
  getItem: key => sessionStorage.getItem(key),
  setItem: (key, value) => sessionStorage.setItem(key, value),
  removeItem: key => sessionStorage.removeItem(key)
})

export function useOrganizationSharedAllowance() {
  const metadata = useOrganizationMetadata()
  const view = useSyncExternalStore(sharedAllowanceClient.subscribe, sharedAllowanceClient.getSnapshot)
  const organizationId = metadata.view.descriptor?.metadata?.organizationId
  const ownerId = metadata.view.descriptor?.metadata?.owner.ownerId
  useEffect(() => {
    if (metadata.scope.eligible && organizationId && ownerId) {
      void sharedAllowanceClient.read()
    }
  }, [metadata.scope.key, metadata.scope.eligible, organizationId, ownerId])

  return {
    client: sharedAllowanceClient,
    view,
    scope: metadata.scope,
    metadata: metadata.view,
    refresh: async () => {
      await metadata.client.read()
      await sharedAllowanceClient.read()
    }
  }
}

import { useEffect, useSyncExternalStore } from 'react'

import { createOwnerPolicyClient } from './organization-owner-policy-client'
import type { OwnerPolicyScope } from './organization-owner-policy-types'
import {
  organizationMetadataClient,
  readOrganizationMetadataScope,
  useOrganizationMetadata
} from './use-organization-metadata'

function readOwnerPolicyScope(): OwnerPolicyScope {
  const scope = readOrganizationMetadataScope()
  const metadata = organizationMetadataClient.getSnapshot().descriptor?.metadata

  return { ...scope, organizationId: metadata?.organizationId ?? null, ownerId: metadata?.owner.ownerId ?? null }
}

const client = createOwnerPolicyClient(readOwnerPolicyScope, () => crypto.randomUUID(), {
  getItem: key => sessionStorage.getItem(key),
  setItem: (key, value) => sessionStorage.setItem(key, value),
  removeItem: key => sessionStorage.removeItem(key)
})

export function useOrganizationOwnerPolicy() {
  const metadata = useOrganizationMetadata()
  const view = useSyncExternalStore(client.subscribe, client.getSnapshot)
  const organizationId = metadata.view.descriptor?.metadata?.organizationId
  const ownerId = metadata.view.descriptor?.metadata?.owner.ownerId
  useEffect(() => {
    if (metadata.scope.eligible && organizationId && ownerId) {
      void client.read()
    }
  }, [metadata.scope.key, metadata.scope.eligible, organizationId, ownerId])

  return {
    client,
    view,
    scope: metadata.scope,
    metadata: metadata.view,
    refresh: async () => {
      await metadata.client.read()
      await client.read()
    }
  }
}

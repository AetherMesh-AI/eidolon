import { useEffect, useSyncExternalStore } from 'react'

import { createOrganizationCredentialsClient } from './organization-credentials-client'
import type { CredentialScope } from './organization-credentials-types'
import { organizationMetadataClient, readOrganizationMetadataScope, useOrganizationMetadata } from './use-organization-metadata'

export function readOrganizationCredentialsScope(): CredentialScope {
  const scope = readOrganizationMetadataScope()
  const metadata = organizationMetadataClient.getSnapshot().descriptor?.metadata

  return { ...scope, organizationId: metadata?.organizationId ?? null, ownerId: metadata?.owner.ownerId ?? null }
}

const client = createOrganizationCredentialsClient(readOrganizationCredentialsScope, () => crypto.randomUUID(), {
  getItem: key => sessionStorage.getItem(key),
  setItem: (key, value) => sessionStorage.setItem(key, value),
  removeItem: key => sessionStorage.removeItem(key)
})

export function useOrganizationCredentials() {
  const metadata = useOrganizationMetadata()
  const view = useSyncExternalStore(client.subscribe, client.getSnapshot)
  const organizationId = metadata.view.descriptor?.metadata?.organizationId
  const ownerId = metadata.view.descriptor?.metadata?.owner.ownerId
  const scope = metadata.scope
  useEffect(() => {
    if (scope.eligible && organizationId && ownerId) {
      void client.read()
    }
  }, [scope.key, scope.eligible, organizationId, ownerId])

  return { client, view, scope, metadata: metadata.view,
    identity: JSON.stringify([scope.key, scope.ownerKey, scope.eligible, organizationId, ownerId]),
    refresh: async () => { await metadata.client.read(); await client.read() } }
}

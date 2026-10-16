import { useEffect, useSyncExternalStore } from 'react'

import { createCredentialHandoffClient } from './organization-credential-handoff-client'
import { readOrganizationCredentialsScope } from './organization-credentials-hook'

const client = createCredentialHandoffClient(readOrganizationCredentialsScope, () => crypto.randomUUID(), {
  getItem: key => sessionStorage.getItem(key), setItem: (key, value) => sessionStorage.setItem(key, value),
  removeItem: key => sessionStorage.removeItem(key)
})

export function useCredentialHandoff(identity: string) {
  const view = useSyncExternalStore(client.subscribe, client.getSnapshot)
  useEffect(() => { client.restore() }, [identity])

  return { client, view }
}

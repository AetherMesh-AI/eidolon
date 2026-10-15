import { useEffect, useSyncExternalStore } from 'react'

import { createRequestIntakeClient } from './organization-request-intake-client'
import { readOrganizationMetadataScope, useOrganizationMetadata } from './use-organization-metadata'

const client = createRequestIntakeClient(readOrganizationMetadataScope)

export function useOrganizationRequestIntake() {
  const { scope } = useOrganizationMetadata()
  const view = useSyncExternalStore(client.subscribe, client.getSnapshot)

  useEffect(() => {
    if (scope.eligible) {
      void client.list()
    }
  }, [scope.key, scope.eligible])

  useEffect(() => {
    if (
      !scope.eligible ||
      !view.record ||
      !['pending', 'proposed'].includes(view.record.status) ||
      view.busy ||
      view.intent ||
      view.error
    ) {
      return
    }

    const timer = setTimeout(() => void client.read(), 2000)

    return () => clearTimeout(timer)
  }, [scope.key, scope.eligible, view])

  return { client, view, eligible: scope.eligible }
}

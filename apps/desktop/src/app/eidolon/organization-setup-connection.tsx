import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n/context'
import { setupConnectionCopy } from '@/i18n/organization-setup-connection'

import { subscribeOrganizationDirectorySource } from './organization-directory-runtime'
import {
  createOrganizationSetupConnectionClient,
  type OrganizationSetupConnectionClient,
  type SetupConnectionSnapshot
} from './organization-setup-connection-client'
import { readOrganizationMetadataScope, useOrganizationMetadata } from './use-organization-metadata'

function SetupConnectionPanel({ organizationId }: { organizationId: string }) {
  const { locale } = useI18n()
  const copy = setupConnectionCopy[locale]
  const client = useRef<OrganizationSetupConnectionClient | null>(null)
  const alive = useRef(false)
  const busy = useRef(false)
  const [snapshot, setSnapshot] = useState<SetupConnectionSnapshot | null>(null)
  const [pending, setPending] = useState(true)
  const [error, setError] = useState(false)

  const run = async (action: (current: OrganizationSetupConnectionClient) => Promise<SetupConnectionSnapshot>) => {
    const current = client.current

    if (!current || busy.current || !current.current()) {
      return
    }

    busy.current = true
    setPending(true)
    setError(false)

    try {
      const next = await action(current)

      if (alive.current && client.current === current && current.current()) {
        setSnapshot(next)
      }
    } catch {
      if (alive.current && client.current === current && current.current()) {
        // A write may have committed. Only an explicit Check reads again;
        // mounting, errors and retries never silently repeat a mutation.
        setSnapshot(null)
        setError(true)
      }
    } finally {
      if (alive.current && client.current === current) {
        busy.current = false
        setPending(false)
      }
    }
  }

  // eslint-disable-next-line no-restricted-syntax -- Own one captured socket for this keyed owner/organization panel.
  useEffect(() => {
    alive.current = true
    const current = createOrganizationSetupConnectionClient(readOrganizationMetadataScope, subscribeOrganizationDirectorySource)
    client.current = current
    void run(value => value.inspect(organizationId))

    return () => {
      alive.current = false
      busy.current = false
      current.dispose()
    }
  }, [organizationId])

  const status = snapshot?.status
  const disconnected = status?.connectionState === 'disconnected'
  const canDisconnect = status && ['active', 'refreshing'].includes(status.connectionState)

  return <>
    {pending && <p role="status">{copy.loading}</p>}
    {error && <p role="alert">{copy.error}</p>}
    {snapshot && <>
      <p role={status ? 'status' : 'alert'}>{status ? copy[status.connectionState] : copy.unknown}</p>
      {status?.cleanupPending && <p role="status">{copy.cleanup}</p>}
      {disconnected && <p>{copy.remoteUnknown}</p>}
      {snapshot.setup.state === 'configured'
        ? <p>{copy.setupFinished}</p>
        : <><p>{copy.freshOnly}</p><Link to="/home">{copy.openSetup}</Link></>}
    </>}
    <div className="flex flex-wrap gap-2">
      <Button disabled={pending} onClick={() => void run(value => value.inspect(organizationId))} variant="ghost">{copy.check}</Button>
      {snapshot && <Button disabled={pending} onClick={() => void run(value => value.recover(snapshot))} variant="secondary">{copy.recover}</Button>}
      {canDisconnect && <Button disabled={pending} onClick={() => void run(value => value.disconnect(snapshot!))} variant="secondary">{copy.disconnect}</Button>}
    </div>
    <p className="text-sm text-muted-foreground">{copy.disconnectNote}</p>
  </>
}

export function OrganizationSetupConnectionSettings() {
  const { locale } = useI18n()
  const copy = setupConnectionCopy[locale]
  const { scope, view, client } = useOrganizationMetadata()
  const metadata = view.descriptor?.state === 'ready' ? view.descriptor.metadata : null
  const eligible = scope.eligible && Boolean(metadata) && !view.error

  return <section aria-label={copy.title} className="grid min-w-0 gap-4">
    <h3 className="text-base font-medium">{copy.title}</h3>
    <p className="text-sm text-muted-foreground">{copy.purpose}</p>
    {eligible && metadata ? <SetupConnectionPanel
      key={JSON.stringify([scope.key, scope.ownerKey, scope.profile, metadata.organizationId, metadata.owner.ownerId])}
      organizationId={metadata.organizationId}
    /> : <>
      <p role="status">{copy.unavailable}</p>
      <Button disabled={view.busy || !scope.eligible} onClick={() => void client.read()} variant="ghost">{copy.check}</Button>
    </>}
  </section>
}

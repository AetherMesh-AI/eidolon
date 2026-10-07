import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'

import { Button } from '@/components/ui/button'
import { $connectionsRegistry } from '@/store/connection-registry-state'
import { $activeConnectionId, $pendingConnectionId, selectConnection } from '@/store/connections'
import type { OrganizationOwnerWork } from '@/store/organization-work'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'
import { $connection } from '@/store/session'

import { createOrganizationScopeReader } from '../eidolon/runtime-provider'
import { navigateToWorkspacePage } from '../routes'

import { useOrganizationShellCopy } from './organization-copy'

export function OrganizationWorkPicker({ owners, onClose }: { owners: OrganizationOwnerWork[]; onClose(): void }) {
  const registry = useStore($connectionsRegistry)
  useStore($connection)
  useStore($activeConnectionId)
  useStore($activeGatewayProfile)
  const sourceSwitch = useStore($pendingConnectionId)
  const profileSwitch = useStore($gatewaySwapTarget)
  const readScope = useMemo(createOrganizationScopeReader, [])
  const currentScope = readScope()
  const currentOwner = currentScope.connected ? currentScope.ownerKey : undefined
  const copy = useOrganizationShellCopy()
  const navigate = useNavigate()
  const location = useLocation()
  const latestLocation = useRef(location.key)
  latestLocation.current = location.key
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const active = useRef(true)
  const sending = useRef(false)

  // eslint-disable-next-line no-restricted-syntax -- fence a dismissed asynchronous switch
  useEffect(() => {
    active.current = true

    return () => { active.current = false }
  }, [])

  const open = async (owner: OrganizationOwnerWork) => {
    if (sending.current) { return }
    const submittedLocation = latestLocation.current
    sending.current = true
    setPending(true)
    setError('')

    try {
      const scope = readScope()

      if (!scope.connected || scope.ownerKey !== owner.ownerScope) {
        const route = owner.route

        if (!route || !registry?.connections.some(connection => connection.id === route.connectionId)) {
          throw new Error(copy.sourceUnavailable)
        }

        await selectConnection(route.connectionId, { profile: route.profile, preserveRoute: true })
      }

      // A superseded click or edited endpoint must not open another owner's
      // empty inbox. The scope reader also checks socket/descriptor agreement.
      const finalScope = readScope()

      if ($pendingConnectionId.get() || $gatewaySwapTarget.get() || !finalScope.connected || finalScope.ownerKey !== owner.ownerScope) {
        throw new Error(copy.sourceUnavailable)
      }

      if (active.current && latestLocation.current === submittedLocation) {
        navigateToWorkspacePage(navigate, '/requests')
        onClose()
      }
    } catch {
      if (active.current && latestLocation.current === submittedLocation) { setError(copy.sourceUnavailable) }
    } finally {
      sending.current = false

      if (active.current) { setPending(false) }
    }
  }

  return <section aria-label={copy.workOwners}>
    <h2>{copy.workOwners}</h2>
    {owners.map(owner => {
      const source = registry?.connections.find(connection => connection.id === owner.route?.connectionId)
      const label = `${source?.label || copy.unknownSource} / ${owner.profile || copy.unknownProfile}`

      return <div key={owner.ownerScope}>
        <p>{label}</p>
        <p>{owner.work.needsYou} {copy.needsYou} · {owner.work.running} {copy.running} · {owner.work.queued} {copy.queued}{owner.work.stale ? ` · ${copy.lastKnown}` : ''}</p>
        <Button aria-label={`${copy.openRequests}: ${label}`} disabled={pending || Boolean(sourceSwitch || profileSwitch) || (currentOwner !== owner.ownerScope && !source)} onClick={() => void open(owner)} size="sm" variant="secondary">{copy.openRequests}</Button>
        {currentOwner !== owner.ownerScope && !source && <p>{copy.sourceUnavailable}</p>}
      </div>
    })}
    {error && <p role="alert">{error}</p>}
  </section>
}

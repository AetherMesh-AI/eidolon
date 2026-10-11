import './workspace-header.css'

import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n/context'
import { openCommandPalettePage } from '@/store/command-palette'
import { $connectionsRegistry, $activeConnectionId, $pendingConnectionId } from '@/store/connections'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'

import { ConnectionSwitcher } from '../chat/sidebar/connection-switcher'
import { OrganizationWorkIndicator } from '../contrib/organization-shell'
import { navigateToWorkspacePage } from '../routes'

import { createOrganizationScopeReader, useRuntimeOrganization } from './runtime-provider'
import { WorkspaceSearch } from './workspace-search'

/** Stable shell chrome: page and draft owners stay mounted beneath it. */
export function WorkspaceHeader() {
  const { t } = useI18n()
  const copy = t.organizationHome.workspace
  const profile = useStore($activeGatewayProfile)
  const connectionId = useStore($activeConnectionId)
  const registry = useStore($connectionsRegistry)
  const pending = useStore($pendingConnectionId)
  const swapping = useStore($gatewaySwapTarget)
  const organization = useRuntimeOrganization()
  const navigate = useNavigate()
  const location = useLocation()
  const readScope = useMemo(createOrganizationScopeReader, [])
  const [searchScope, setSearchScope] = useState<string | null>(null)
  const snapshot = organization?.snapshot
  const current = () => Boolean(snapshot?.connection?.state === 'ready' && readScope().connected &&
    readScope().key === snapshot.connection.scope && !$pendingConnectionId.get() && !$gatewaySwapTarget.get())
  const available = Boolean(snapshot?.connection?.state === 'ready' && !pending && !swapping && current())
  const connection = registry?.connections.find(item => item.id === connectionId)
  useEffect(() => {
    if (!available || searchScope !== snapshot?.connection?.scope) setSearchScope(null)
  }, [available, searchScope, snapshot?.connection?.scope])
  const settings = location.pathname === '/settings'

  return <header aria-label={copy.heading} className="eidolon eid-workspace-header" data-slot="workspace-header">
    <div className="eid-workspace-identity">
      <Button aria-label={copy.profile(profile)} onClick={() => navigateToWorkspacePage(navigate, '/profiles')} size="micro" variant="ghost"><Codicon name="organization" /><span>{profile}</span></Button>
      {(registry?.connections.length ?? 0) > 1 ? <ConnectionSwitcher compact onConnect={() => navigateToWorkspacePage(navigate, '/settings?tab=connections')} /> : connection && <span className="eid-workspace-connection">{connection.label}</span>}
    </div>
    <Button aria-label={copy.search} disabled={!available} onClick={() => { if (current()) setSearchScope(snapshot!.connection!.scope) }} size="micro" variant="ghost"><Codicon name="search" /><span className="eid-workspace-action-label">{copy.search}</span></Button>
    <div className="eid-workspace-actions">
      <OrganizationWorkIndicator compact />
      <Button aria-label={settings ? copy.searchSettings : t.organizationHome.settings} onClick={() => settings ? openCommandPalettePage('settings') : navigateToWorkspacePage(navigate, '/settings')} size="micro" variant="ghost"><Codicon name={settings ? 'search' : 'settings-gear'} /><span className="eid-workspace-action-label">{settings ? copy.searchSettings : t.organizationHome.settings}</span></Button>
    </div>
    {snapshot && searchScope === snapshot.connection?.scope && available && <WorkspaceSearch isCurrent={current} key={searchScope} onClose={() => setSearchScope(null)} profile={profile} snapshot={snapshot} />}
  </header>
}

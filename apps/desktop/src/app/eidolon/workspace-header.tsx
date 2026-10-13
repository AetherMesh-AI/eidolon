import './workspace-header.css'

import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n/context'
import { openCommandPalettePage } from '@/store/command-palette'
import { $pendingConnectionId } from '@/store/connections'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'

import { ConnectionSwitcher } from '../chat/sidebar/connection-switcher'
import { OrganizationWorkIndicator } from '../contrib/organization-shell'
import { navigateToWorkspacePage } from '../routes'

import { OrganizationDirectoryControl } from './organization-directory-control'
import type { MetadataDescriptor } from './organization-metadata-client'
import { OrganizationOwnerEditor } from './organization-owner-editor'
import { createOrganizationScopeReader, useRuntimeOrganization } from './runtime-provider'
import { useOrganizationMetadata } from './use-organization-metadata'
import { WorkspaceSearch } from './workspace-search'

function ownerEditorScope(scope: string, descriptor: MetadataDescriptor | null) {
  return JSON.stringify([scope, descriptor?.state ?? 'unverified', descriptor?.metadata?.organizationId ?? null])
}

/** Stable shell chrome: page and draft owners stay mounted beneath it. */
export function WorkspaceHeader() {
  const { t } = useI18n()
  const copy = t.organizationHome.workspace
  const profile = useStore($activeGatewayProfile)
  const pending = useStore($pendingConnectionId)
  const swapping = useStore($gatewaySwapTarget)
  const organization = useRuntimeOrganization()
  const navigate = useNavigate()
  const location = useLocation()
  const readScope = useMemo(createOrganizationScopeReader, [])
  const searchTrigger = useRef<HTMLButtonElement>(null)
  const [searchScope, setSearchScope] = useState<string | null>(null)
  const snapshot = organization?.snapshot

  const current = () => Boolean(snapshot?.connection?.state === 'ready' && readScope().connected &&
    readScope().key === snapshot.connection.scope && !$pendingConnectionId.get() && !$gatewaySwapTarget.get())

  const available = Boolean(snapshot?.connection?.state === 'ready' && !pending && !swapping && current())
  useEffect(() => {
    if (!available || searchScope !== snapshot?.connection?.scope) {setSearchScope(null)}
  }, [available, searchScope, snapshot?.connection?.scope])
  const identity = useOrganizationMetadata()
  const saved = identity.view.descriptor?.metadata
  const ownerTrigger = useRef<HTMLButtonElement>(null)
  const [editingScope, setEditingScope] = useState<string | null>(null)
  const editorScope = ownerEditorScope(identity.scope.key, identity.view.descriptor)
  const canEdit = identity.scope.eligible && !identity.view.busy
  useEffect(() => {
    if (!identity.scope.eligible || editingScope !== editorScope) {setEditingScope(null)}
  }, [identity.scope.eligible, editorScope, editingScope])
  const settings = location.pathname === '/settings'

  return <header aria-label={copy.heading} className="eidolon eid-workspace-header" data-slot="workspace-header">
    <div className="eid-workspace-identity">
      {identity.view.descriptor && identity.view.descriptor.state !== 'unsupported' && <span className="eid-workspace-organization-name" title={saved?.displayName}>{saved?.displayName ?? t.organizationMetadata.unnamed}</span>}
      <Button aria-label={t.organizationMetadata.edit} disabled={!canEdit} onClick={() => { if (readScope().connected && readScope().key === identity.scope.key && !$pendingConnectionId.get()) { setEditingScope(editorScope) } }} ref={ownerTrigger} size="micro" title={t.organizationMetadata.edit} variant="ghost"><Codicon name="account" /><span>{saved?.owner.displayName ?? t.organizationMetadata.owner}</span></Button>
      <Button aria-label={copy.profile(profile)} onClick={() => navigateToWorkspacePage(navigate, '/profiles')} size="micro" variant="ghost"><Codicon name="organization" /><span>{profile}</span></Button>
      <OrganizationDirectoryControl />
      {/* The switcher owns registry initialization and change subscriptions, even with one source. */}
      <ConnectionSwitcher compact onConnect={() => navigateToWorkspacePage(navigate, '/settings?tab=connections')} />
    </div>
    <Button aria-label={copy.search} disabled={!available} onClick={() => { if (current()) {setSearchScope(snapshot!.connection!.scope)} }} ref={searchTrigger} size="micro" variant="ghost"><Codicon name="search" /><span className="eid-workspace-action-label">{copy.search}</span></Button>
    <div className="eid-workspace-actions">
      <OrganizationWorkIndicator compact />
      <Button aria-label={settings ? t.settings.search.pill : copy.settings} onClick={() => settings ? openCommandPalettePage('settings') : navigateToWorkspacePage(navigate, '/settings')} size="micro" variant="ghost"><Codicon name={settings ? 'search' : 'settings-gear'} /><span className="eid-workspace-action-label">{settings ? t.settings.search.pill : copy.settings}</span></Button>
    </div>
    {editingScope === editorScope && identity.scope.eligible && <OrganizationOwnerEditor client={identity.client} draftKey={JSON.stringify([identity.scope.ownerKey, saved?.organizationId ?? 'unconfigured'])} key={editingScope} onClose={() => setEditingScope(null)} onReturnFocus={receipt => {
      const scope = readScope()
      const descriptor = identity.client.getSnapshot().descriptor
      const expectedScope = receipt ? ownerEditorScope(identity.scope.key, receipt) : editingScope

      if (scope.connected && ownerEditorScope(scope.key, descriptor) === expectedScope && !$pendingConnectionId.get() && !$gatewaySwapTarget.get() && ownerTrigger.current?.isConnected && !ownerTrigger.current.disabled) {ownerTrigger.current.focus()}
    }} />}
    {snapshot && searchScope === snapshot.connection?.scope && available && <WorkspaceSearch isCurrent={current} key={searchScope} onClose={() => setSearchScope(null)} onReturnFocus={() => {
      if (current() && searchTrigger.current?.isConnected && !searchTrigger.current.disabled) {searchTrigger.current.focus()}
    }} profile={profile} snapshot={snapshot} />}
  </header>
}

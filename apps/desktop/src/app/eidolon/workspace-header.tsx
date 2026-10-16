import './workspace-header.css'

import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'

import { PANE_TOGGLE_REVEAL_EVENT } from '@/components/pane-shell'
import { $narrowOverlayReveal, narrowOverlayId } from '@/components/pane-shell/tree/narrow-overlay-state'
import { paneChrome } from '@/components/pane-shell/tree/renderer/track-model'
import { $narrowViewport } from '@/components/pane-shell/tree/store'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { useContributions } from '@/contrib/react/use-contributions'
import { useI18n } from '@/i18n/context'
import { openCommandPalettePage } from '@/store/command-palette'
import { $pendingConnectionId } from '@/store/connections'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'

import { ConnectionSwitcher } from '../chat/sidebar/connection-switcher'
import { OrganizationWorkIndicator } from '../contrib/organization-shell'
import { isWorkspacePageRoute, navigateToWorkspacePage } from '../routes'

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
  const narrow = useStore($narrowViewport)
  const navigationAvailable = useContributions('panes').some(pane => pane.id === 'sessions' && paneChrome(pane).collapsible)
  const navigationOpen = useStore($narrowOverlayReveal)?.id === 'sessions'
  const profile = useStore($activeGatewayProfile)
  const pending = useStore($pendingConnectionId)
  const swapping = useStore($gatewaySwapTarget)
  const organization = useRuntimeOrganization()
  const navigate = useNavigate()
  const location = useLocation()
  // Source switching can finish after the owner navigates to a page. Read the
  // latest route at completion so late bookkeeping cannot replace that page.
  const locationRef = useRef(location)
  locationRef.current = location
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
      {narrow && navigationAvailable && <Button aria-controls={narrowOverlayId('sessions')} aria-expanded={navigationOpen} className="eid-workspace-navigation-trigger" onClick={() => window.dispatchEvent(new CustomEvent(PANE_TOGGLE_REVEAL_EVENT, { detail: { id: 'sessions', mode: 'toggle', focus: true } }))} size="lg" variant="outline"><Codicon name="menu" /><span>{copy.navigation}</span></Button>}
      <OrganizationDirectoryControl label={identity.view.descriptor && identity.view.descriptor.state !== 'unsupported' ? saved?.displayName ?? t.organizationMetadata.unnamed : undefined} organizationName={saved?.displayName} />
      {/* The switcher owns registry initialization and change subscriptions, even with one source. */}
      <ConnectionSwitcher compact onConnect={() => navigateToWorkspacePage(navigate, '/settings?tab=connections')} preserveRoute={() => isWorkspacePageRoute(locationRef.current.pathname)} />
      <Button aria-label={copy.profile(profile)} className="eid-workspace-profile-trigger" onClick={() => navigateToWorkspacePage(navigate, '/profiles')} size="lg" variant="ghost"><Codicon name="organization" /><span>{profile}</span></Button>
      <Button aria-label={settings ? t.settings.search.pill : copy.settings} onClick={() => settings ? openCommandPalettePage('settings') : navigateToWorkspacePage(navigate, '/settings')} size="icon" variant="ghost"><Codicon name={settings ? 'search' : 'settings-gear'} /></Button>
    </div>
    <Button aria-label={copy.search} className="eid-workspace-search-trigger" disabled={!available} onClick={() => { if (current()) {setSearchScope(snapshot!.connection!.scope)} }} ref={searchTrigger} size="lg" variant="outline"><Codicon name="search" /><span className="eid-workspace-action-label">{copy.search}</span></Button>
    <div className="eid-workspace-actions">
      <OrganizationWorkIndicator compact />
      <Button aria-label={t.organizationMetadata.edit} disabled={!canEdit} onClick={() => { if (readScope().connected && readScope().key === identity.scope.key && !$pendingConnectionId.get()) { setEditingScope(editorScope) } }} ref={ownerTrigger} size="lg" variant="ghost"><Codicon name="account" /><span>{saved?.owner.displayName ?? t.organizationMetadata.owner}</span></Button>
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

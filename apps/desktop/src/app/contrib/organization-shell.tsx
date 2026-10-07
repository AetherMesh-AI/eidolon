import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'
import { Link, NavLink, useNavigate } from 'react-router'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { EmptyState } from '@/components/ui/empty-state'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Tip } from '@/components/ui/tooltip'
import { useContributions } from '@/contrib/react/use-contributions'
import { $pendingConnectionId } from '@/store/connections'
import { $organizationWork, $organizationWorkOwners, publishOrganizationWork } from '@/store/organization-work'
import { $gatewaySwapTarget } from '@/store/profile'

import { createOrganizationScopeReader, useRuntimeOrganization } from '../eidolon/runtime-provider'
import { navigateToWorkspacePage, SIDEBAR_NAV_AREA, type SidebarNavContribution } from '../routes'

import { useOrganizationShellCopy } from './organization-copy'
import { OrganizationWorkPicker } from './organization-work-picker'

/** The shell owns this subscription even while every organization page is
 * closed. All pages consume the provider's one adapter and poll lifecycle. */
export function OrganizationActiveWorkBridge() {
  const organization = useRuntimeOrganization()

  useEffect(() => {
    if (organization) {
      publishOrganizationWork(organization.snapshot)
    }
  }, [organization])

  return null
}

export function OrganizationWorkIndicator() {
  const work = useStore($organizationWork)
  const owners = useStore($organizationWorkOwners)
  const [open, setOpen] = useState(false)
  const readScope = useMemo(createOrganizationScopeReader, [])
  const copy = useOrganizationShellCopy()
  const navigate = useNavigate()

  if (!work.count && !work.needsYou) {
    return null
  }

  const label = `${copy.work}: ${work.running} ${copy.running}, ${work.queued} ${copy.queued}, ${work.needsYou} ${copy.needsYou}${work.stale ? ` · ${copy.stale}` : ''}`

  return (
    <Popover onOpenChange={setOpen} open={open}><Tip label={label}><PopoverTrigger asChild>
      <Button
        aria-label={label}
        onClick={event => {
          const scope = readScope()

          if (owners.length === 1 && scope.connected && owners[0].ownerScope === scope.ownerKey && !$pendingConnectionId.get() && !$gatewaySwapTarget.get()) {
            event.preventDefault()
            navigateToWorkspacePage(navigate, '/requests')
          }
        }}
        size="micro"
        variant="ghost"
      >
        <Codicon name={work.needsYou ? 'bell' : 'sync'} />
        <span>
          {work.needsYou
            ? `${copy.needsYou} ${work.needsYou}`
            : `${work.running} ${copy.running} · ${work.queued} ${copy.queued}`}
          {work.stale ? ` · ${copy.lastKnown}` : ''}
        </span>
      </Button>
    </PopoverTrigger></Tip><PopoverContent>{open && <OrganizationWorkPicker onClose={() => setOpen(false)} owners={owners} />}</PopoverContent></Popover>
  )
}

/** Installed plugins remain reachable after moving page navigation out of the
 * Sessions tree. Register/unregister is live, including plugin disable. */
export function PluginNavigation() {
  const contributions = useContributions(SIDEBAR_NAV_AREA)
  const copy = useOrganizationShellCopy()
  const navigate = useNavigate()

  const items = contributions.flatMap(contribution => {
    const data = contribution.data as Partial<SidebarNavContribution> | undefined

    return typeof data?.path === 'string' &&
      data.path.startsWith('/') &&
      !data.path.startsWith('//') &&
      typeof data.label === 'string' &&
      data.label.trim()
      ? [{ id: contribution.id, ...data, path: data.path, label: data.label }]
      : []
  })

  return items.length ? (
    <nav aria-label={copy.plugins}>
      {items.map(item => (
        <NavLink
          key={item.id}
          onClick={event => {
            if (!event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
              event.preventDefault()
              navigateToWorkspacePage(navigate, item.path)
            }
          }}
          to={item.path}
        >
          <Codicon name={item.codicon || 'plug'} />
          <span>{item.label}</span>
        </NavLink>
      ))}
    </nav>
  ) : null
}

/** A disabled legacy board never becomes a made-up chat session. */
export function LegacyKanbanUnavailable() {
  const copy = useOrganizationShellCopy()

  return (
    <section className="flex h-full flex-col items-center justify-center p-4">
      <EmptyState description={copy.legacyKanbanHelp} title={copy.legacyKanbanDisabled} />
      <Button asChild variant="secondary">
        <Link to="/settings?tab=plugins&plugin=kanban">{copy.pluginSettings}</Link>
      </Button>
    </section>
  )
}

import './eidolon.css'

import { type ReactNode, useEffect, useRef } from 'react'
import { NavLink, useLocation } from 'react-router'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n/context'
import { Command, FolderOpen, LayoutDashboard, MessageCircle, Network, Settings, Users } from '@/lib/icons'

import { appViewForPath } from '../routes'

import { OrganizationMark, OrganizationScenery } from './scenery'

/** Session history and optional plugin destinations keep their original owners. */
export function OrganizationRail({
  sessions,
  pluginNav,
  needsYouCount = 0,
  unreadOutcomes = 0,
  onNavigate,
  onOpenProfileChats
}: {
  sessions: ReactNode
  pluginNav?: ReactNode
  needsYouCount?: number
  unreadOutcomes?: number
  onNavigate?: (to: string) => void
  onOpenProfileChats?: () => void
}) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const home = t.organizationHome
  const { pathname } = useLocation()
  const history = useRef<HTMLDetailsElement>(null)
  const more = useRef<HTMLDetailsElement>(null)
  const moreTrigger = useRef<HTMLElement>(null)
  const chat = appViewForPath(pathname) === 'chat'
  useEffect(() => {
    if (chat && history.current && more.current) { history.current.open = true; more.current.open = true }
  }, [chat, pathname])

  const destinations = [
    { to: '/home', label: home.home, icon: Command },
    { to: '/messages', label: home.messages, icon: MessageCircle },
    { to: '/work', label: home.work, icon: LayoutDashboard },
    { to: '/objectives', label: home.objectives, icon: Network },
    { to: '/organization', label: home.organization, icon: Users },
    { to: '/artifacts', label: home.deliverables, icon: FolderOpen },
    { to: '/settings', label: home.settings, icon: Settings }
  ]

  const advanced = [
    { to: '/requests', label: copy.needsYou },
    { to: '/activity', label: copy.activity },
    { to: '/skills', label: copy.skills },
    { to: '/messaging', label: copy.messaging },
    { to: '/profiles', label: copy.executionConfig },
    { to: '/session-import', label: copy.importHistory },
    { to: '/legacy-organization', label: copy.legacyTitle }
  ]

  const linkAction = (event: React.MouseEvent, to: string) => {
    if (onNavigate && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
      event.preventDefault()
      onNavigate(to)
    }
  }

  return (
    <aside aria-label="Eidolon navigation" className="eidolon eid-rail" tabIndex={0}>
      <div className="eid-brand">
        <OrganizationMark />
        Eidolon
      </div>
      <nav aria-label="Primary">
        {destinations.map(({ to, label, icon: Icon }) => (
          <NavLink
            aria-label={label}
            key={to}
            onClick={event => linkAction(event, to)}
            to={to}
          >
            <Icon aria-hidden="true" size={18} />
            <span>{label}</span>
            {to === '/objectives' && unreadOutcomes > 0 && (
              <Badge aria-label={`${copy.outcomeUnread}: ${unreadOutcomes}`} size="xs" variant="warn">
                {unreadOutcomes}
              </Badge>
            )}
            {to === '/home' && needsYouCount > 0 && (
              <Badge size="xs" variant="warn">
                {needsYouCount}
              </Badge>
            )}
          </NavLink>
        ))}
      </nav>
      <div aria-hidden="true" className="eid-rail-decoration"><OrganizationScenery /></div>
      <details className="eid-rail-more" onKeyDown={event => {
        // React portal events still bubble through the session tree. Their
        // dialogs, and controls that consumed Escape, own that key press.
        if (event.defaultPrevented || !event.currentTarget.contains(event.target as Node)) {return}

        if (event.key === 'Escape' && more.current?.open) {
          event.preventDefault()
          event.stopPropagation()
          more.current.open = false
          moreTrigger.current?.focus()
        }
      }} ref={more}>
        <summary aria-label={home.workspace.more} ref={moreTrigger} role="button"><Codicon name="ellipsis" /></summary>
        {onOpenProfileChats && <Button onClick={onOpenProfileChats} size="sm" variant="ghost">{home.workspace.profileChats}</Button>}
        <nav aria-label={copy.advanced}>
          {advanced.map(({ to, label }) => (
            <NavLink key={to} onClick={event => linkAction(event, to)} to={to}>
              {label}
            </NavLink>
          ))}
        </nav>
        {pluginNav}
        <details className="eid-rail-history" ref={history}>
          <summary>{copy.navigationHistory}</summary>
          <div className="eid-session-tree">{sessions}</div>
        </details>
      </details>
    </aside>
  )
}

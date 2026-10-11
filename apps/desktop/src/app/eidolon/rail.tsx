import './eidolon.css'

import { type ReactNode, useEffect, useRef } from 'react'
import { NavLink, useLocation } from 'react-router'

import { BrandMark } from '@/components/brand-mark'
import { Badge } from '@/components/ui/badge'
import { useI18n } from '@/i18n/context'
import { Command, FolderOpen, LayoutDashboard, MessageCircle, Network, Settings, Users } from '@/lib/icons'

import { appViewForPath } from '../routes'

/** Session history and optional plugin destinations keep their original owners. */
export function OrganizationRail({
  sessions,
  pluginNav,
  needsYouCount = 0,
  unreadOutcomes = 0,
  onNavigate,
  mode = 'runtime'
}: {
  sessions: ReactNode
  pluginNav?: ReactNode
  needsYouCount?: number
  unreadOutcomes?: number
  onNavigate?: (to: string) => void
  mode?: 'prototype' | 'runtime'
}) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const home = t.organizationHome
  const { pathname } = useLocation()
  const history = useRef<HTMLDetailsElement>(null)
  const chat = appViewForPath(pathname) === 'chat'
  useEffect(() => {
    if (chat && history.current) { history.current.open = true }
  }, [chat, pathname])

  const destinations = [
    { to: '/home', label: home.home, icon: Command },
    { to: '/messages', label: home.messages, icon: MessageCircle },
    { to: '/work', label: home.work, icon: LayoutDashboard },
    { to: '/objectives', label: copy.objectives, icon: Network },
    { to: '/organization', label: copy.organization, icon: Users },
    { to: '/artifacts', label: home.deliverables, icon: FolderOpen },
    { to: '/settings', label: copy.settings, icon: Settings }
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
        <BrandMark aria-hidden="true" className="size-7" />
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
            <Icon aria-hidden="true" size={16} />
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
      <details>
        <summary>{copy.advanced}</summary>
        <nav aria-label={copy.advanced}>
          {advanced.map(({ to, label }) => (
            <NavLink key={to} onClick={event => linkAction(event, to)} to={to}>
              {label}
            </NavLink>
          ))}
        </nav>
        {pluginNav}
      </details>
      <details className="eid-rail-history" ref={history}>
        <summary>{copy.navigationHistory}</summary>
        <p>{copy.historyNote}</p>
        <div className="eid-session-tree">{sessions}</div>
      </details>
      <div className="eid-rail-footer">
        <small>{mode === 'prototype' ? copy.legacyTitle : copy.evidenceSource}</small>
      </div>
    </aside>
  )
}

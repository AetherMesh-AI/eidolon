import './eidolon.css'

import { type ReactNode, useEffect, useRef } from 'react'
import { NavLink, useLocation } from 'react-router'

import { BrandMark } from '@/components/brand-mark'
import { Badge } from '@/components/ui/badge'
import { useI18n } from '@/i18n/context'
import { Activity, Bell, Command, FolderOpen, Network, Settings, Users } from '@/lib/icons'

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
  const { pathname } = useLocation()
  const history = useRef<HTMLDetailsElement>(null)
  const chat = appViewForPath(pathname) === 'chat'
  useEffect(() => {
    if (chat && history.current) { history.current.open = true }
  }, [chat, pathname])

  const destinations = [
    { to: '/home', label: copy.command, icon: Command },
    { to: '/requests', label: copy.needsYou, icon: Bell },
    { to: '/objectives', label: copy.objectives, icon: Network },
    { to: '/organization', label: copy.organization, icon: Users },
    { to: '/artifacts', label: copy.artifacts, icon: FolderOpen },
    { to: '/activity', label: copy.activity, icon: Activity }
  ]

  const advanced = [
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
            aria-label={to === '/objectives' ? label : undefined}
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
            {to === '/requests' && needsYouCount > 0 && (
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
        <nav aria-label={copy.settings}><NavLink onClick={event => linkAction(event, '/settings')} to="/settings"><Settings aria-hidden="true" size={16} /><span>{copy.settings}</span></NavLink></nav>
        <small>{mode === 'prototype' ? copy.legacyTitle : copy.evidenceSource}</small>
      </div>
    </aside>
  )
}

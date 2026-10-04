import './eidolon.css'

import type { ReactNode } from 'react'
import { NavLink } from 'react-router'

import { Badge } from '@/components/ui/badge'
import { useI18n } from '@/i18n/context'
import { Activity, Bell, Command, FolderOpen, Network, Settings, Users } from '@/lib/icons'

/** Session history and optional plugin destinations keep their original owners. */
export function OrganizationRail({
  sessions,
  pluginNav,
  needsYouCount = 0,
  onNavigate,
  mode = 'runtime'
}: {
  sessions: ReactNode
  pluginNav?: ReactNode
  needsYouCount?: number
  onNavigate?: (to: string) => void
  mode?: 'prototype' | 'runtime'
}) {
  const { t } = useI18n()
  const copy = t.organizationWork

  const destinations = [
    { to: '/home', label: copy.command, icon: Command },
    { to: '/requests', label: copy.needsYou, icon: Bell },
    { to: '/objectives', label: copy.objectives, icon: Network },
    { to: '/organization', label: copy.organization, icon: Users },
    { to: '/artifacts', label: copy.artifacts, icon: FolderOpen },
    { to: '/activity', label: copy.activity, icon: Activity },
    { to: '/settings', label: copy.settings, icon: Settings }
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
    <aside aria-label="Eidolon navigation" className="eidolon eid-rail">
      <div className="eid-brand">
        <span aria-hidden="true">◈</span>EIDOLON
      </div>
      <nav aria-label="Primary">
        {destinations.map(({ to, label, icon: Icon }) => (
          <NavLink key={to} onClick={event => linkAction(event, to)} to={to}>
            <Icon aria-hidden="true" size={16} />
            <span>{label}</span>
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
      <section aria-label={copy.navigationHistory}>
        <h2>{copy.navigationHistory}</h2>
        <p>{copy.historyNote}</p>
        <div className="eid-session-tree">{sessions}</div>
      </section>
      <div className="eid-rail-footer">{mode === 'prototype' ? copy.legacyTitle : copy.evidenceSource}</div>
    </aside>
  )
}

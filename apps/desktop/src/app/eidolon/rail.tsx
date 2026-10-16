import './eidolon.css'

import { Fragment, type ReactNode, useEffect, useRef } from 'react'
import { Link, NavLink, useLocation } from 'react-router'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n/context'
import { Bell, Command, FolderOpen, LayoutDashboard, MessageCircle, Settings, Users } from '@/lib/icons'

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
  const historyTrigger = useRef<HTMLElement>(null)
  const chat = appViewForPath(pathname) === 'chat'
  useEffect(() => {
    if (chat && history.current) { history.current.open = true }
  }, [chat, pathname])

  const destinations = [
    { to: '/home', label: home.home, icon: Command },
    { to: '/messages', label: home.messages, icon: MessageCircle },
    { to: '/work', label: home.work, icon: LayoutDashboard },
    { to: '/requests', label: copy.needsYou, icon: Bell },
    { to: '/organization', label: home.organization, icon: Users },
    { to: '/artifacts?source=organization', label: home.deliverables, icon: FolderOpen },
    { to: '/settings', label: home.settings, icon: Settings }
  ]

  const workRoutes = ['/work', '/objectives', '/activity']
  const settingsRoutes = ['/settings', '/skills', '/messaging', '/profiles']

  const contextual = workRoutes.includes(pathname) || pathname.startsWith('/objectives/') ? [
    { to: '/objectives', label: home.objectives },
    { to: '/activity', label: copy.activity }
  ] : settingsRoutes.includes(pathname) ? [
    { to: '/skills', label: copy.skills },
    { to: '/messaging', label: copy.messaging },
    { to: '/profiles', label: copy.executionConfig }
  ] : []

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
          <Fragment key={to}><Link
            aria-current={(to === '/work' && (workRoutes.includes(pathname) || pathname.startsWith('/objectives/'))) || (to === '/settings' && settingsRoutes.includes(pathname)) || pathname === to.split('?')[0] ? 'page' : undefined}
            aria-label={label}
            onClick={event => linkAction(event, to)}
            to={to}
          >
            <Icon aria-hidden="true" size={18} />
            <span>{label}</span>
            {to === '/work' && unreadOutcomes > 0 && (
              <Badge aria-label={`${copy.outcomeUnread}: ${unreadOutcomes}`} size="xs" variant="warn">
                {unreadOutcomes}
              </Badge>
            )}
            {to === '/requests' && needsYouCount > 0 && (
              <Badge size="xs" variant="warn">
                {needsYouCount}
              </Badge>
            )}
          </Link>
          {contextual.length > 0 && ((to === '/work' && (workRoutes.includes(pathname) || pathname.startsWith('/objectives/'))) || (to === '/settings' && settingsRoutes.includes(pathname))) && <div aria-label={label} className="eid-rail-context" role="group">
            {contextual.map(item => <NavLink key={item.to} onClick={event => linkAction(event, item.to)} to={item.to}>{item.label}</NavLink>)}
          </div>}</Fragment>
        ))}
      </nav>
      <div aria-hidden="true" className="eid-rail-decoration"><OrganizationScenery /></div>
      {pluginNav}
      <details className="eid-rail-history" onKeyDown={event => {
        if (event.defaultPrevented || !event.currentTarget.contains(event.target as Node)) {return}

        if (event.key === 'Escape' && history.current?.open) {
          event.preventDefault()
          event.stopPropagation()
          history.current.open = false
          historyTrigger.current?.focus()
        }
      }} ref={history}>
        <summary ref={historyTrigger}>{copy.navigationHistory}</summary>
        {onOpenProfileChats && <Button onClick={onOpenProfileChats} size="sm" variant="ghost">{home.workspace.profileChats}</Button>}
        <nav aria-label={copy.navigationHistory}>
          <NavLink onClick={event => linkAction(event, '/session-import')} to="/session-import">{copy.importHistory}</NavLink>
          <NavLink onClick={event => linkAction(event, '/legacy-organization')} to="/legacy-organization">{copy.legacyTitle}</NavLink>
        </nav>
        <div className="eid-session-tree">{sessions}</div>
      </details>
    </aside>
  )
}

import './eidolon.css'

import { useStore } from '@nanostores/react'
import { type ReactNode, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useI18n } from '@/i18n/context'
import type { RuntimeReadinessResult } from '@/lib/runtime-readiness'
import { useStoreSelector } from '@/lib/use-session-slice'
import { $organizationWork } from '@/store/organization-work'
import { $approvalRequest } from '@/store/prompts'
import { $busy } from '@/store/session'
import { $subagentsBySession, activeSubagentCount, failedSubagentCount } from '@/store/subagents'

import { OrganizationWorkIndicator } from '../contrib/organization-shell'
import { useTitlebarToolContributions } from '../contrib/panes'
import { TitlebarControls } from '../shell/titlebar-controls'

import { RuntimeStatus } from './runtime-detail'
import { useRuntimeOrganization } from './runtime-provider'
import { systemHealth } from './system-health'

export function SystemHealthBar({
  gateway,
  readiness,
  fresh,
  controls,
  onOpenAgents
}: {
  gateway: string
  readiness: RuntimeReadinessResult | null
  fresh: boolean
  controls: ReactNode
  onOpenAgents?: () => void
}) {
  const { t, locale } = useI18n()
  const copy = t.organizationHome.health
  const organization = useRuntimeOrganization()
  const work = useStore($organizationWork)
  const approval = useStore($approvalRequest)
  const chatBusy = useStore($busy)

  const otherRunning = useStoreSelector($subagentsBySession, groups =>
    Object.values(groups).reduce((sum, group) => sum + activeSubagentCount(group), 0)
  )

  const otherFailed = useStoreSelector($subagentsBySession, groups =>
    Object.values(groups).reduce((sum, group) => sum + failedSubagentCount(group), 0)
  )

  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const left = useTitlebarToolContributions('left')
  const right = useTitlebarToolContributions('right')

  const state = systemHealth(
    gateway,
    organization?.snapshot,
    readiness,
    fresh,
    work.needsYou + Number(Boolean(approval)),
    { staleWork: work.stale, failedAgents: otherFailed }
  )

  const number = new Intl.NumberFormat(locale)
  const label = `${copy.heading}: ${copy.states[state]}`

  return (
    <footer aria-label={copy.heading} className="eidolon eid-system-health" data-slot="system-health">
      <Popover onOpenChange={setOpen} open={open}>
        <PopoverTrigger asChild>
          <Button aria-label={label} size="micro" variant="ghost">
            <span aria-hidden="true" className={`eid-health-dot eid-health-${state}`} />
            {label}
          </Button>
        </PopoverTrigger>
        <PopoverContent aria-label={copy.details} className="eidolon eid-health-details" side="top">
          <h2>{copy.details}</h2>
          <p>{copy.states[state]}</p>
          <dl>
            <dt>{copy.connection}</dt>
            <dd>{gateway}</dd>
            <dt>{copy.provider}</dt>
            <dd>
              {!fresh
                ? copy.unverified
                : readiness?.reason ||
                  (readiness?.ready && readiness.source === 'runtime_check' ? copy.checked : copy.unverified)}
            </dd>
            <dt>{copy.runtime}</dt>
            <dd>{organization?.snapshot.runtime?.state || copy.unverified}</dd>
          </dl>
          <p>
            {copy.activity(number.format(work.running), number.format(work.queued))}
            {work.stale ? ` · ${copy.lastKnown}` : ''}
          </p>
          <p>{copy.otherAgents(number.format(otherRunning), number.format(otherFailed))}</p>
          {chatBusy && <p>{copy.chatWorking}</p>}
          {onOpenAgents && (
            <Button
              onClick={() => {
                setOpen(false)
                onOpenAgents()
              }}
              variant="secondary"
            >
              {copy.inspectAgents}
            </Button>
          )}
          {approval && <p role="status">{copy.approval}</p>}
          <Link onClick={() => setOpen(false)} to="/settings?tab=gateway">
            {copy.gatewaySettings}
          </Link>
          {organization && <RuntimeStatus adapter={organization.adapter} snapshot={organization.snapshot} />}
          <details>
            <summary>{copy.controls}</summary>
            <TitlebarControls
              inline
              leftTools={left}
              onOpenSettings={() => {
                setOpen(false)
                navigate('/settings')
              }}
              tools={right}
            />
            {controls}
          </details>
        </PopoverContent>
      </Popover>
      <span className="eid-health-activity">
        {copy.activity(number.format(work.running), number.format(work.queued))}
        {work.stale ? ` · ${copy.lastKnown}` : ''}
      </span>
      {chatBusy && <span>{copy.chatWorking}</span>}
      <OrganizationWorkIndicator />
      {approval && <span role="status">{copy.approval}</span>}
    </footer>
  )
}

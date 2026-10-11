import { Link } from 'react-router'

import { useI18n } from '@/i18n/context'

import { Activity } from './activity'
import { HomeObjective } from './home'
import { homeData } from './home-data'
import { TaskCounts } from './objective-progress'
import { objectiveWorkData } from './objective-work-data'
import type { OrganizationSnapshot } from './types'

export function WorkOverview({ snapshot }: { snapshot: OrganizationSnapshot }) {
  const { t, locale } = useI18n()
  const copy = t.organizationHome
  const objectives = homeData(snapshot).objectives
  const number = new Intl.NumberFormat(locale)

  return <>
    <header className="eid-home-hero"><div><h1>{copy.work}</h1><p>{copy.workNote}</p></div></header>
    {snapshot.connection && snapshot.connection.state !== 'ready' && <p role="status">{snapshot.connection.state === 'connecting' ? copy.connecting : copy.lastKnown}</p>}
    <div className="eid-work-overview">
      {objectives.map(objective => {
        const data = objectiveWorkData(objective, snapshot)

        return <section aria-label={objective.title} className="eid-home-panel" key={objective.id}>
          <HomeObjective objective={objective} snapshot={snapshot} />
          <TaskCounts objective={objective} snapshot={snapshot} />
          <p>{copy.detail.delegationCount(number.format(data.groups.length))}</p>
          <p>{snapshot.requests ? copy.detail.ownerRequestCount(number.format(data.requests.length)) : copy.requestsUnavailable}</p>
        </section>
      })}
    </div>
    {!objectives.length && <p>{copy.noWork}</p>}
    <p className="eid-note">{copy.snapshotNote}</p>
    <Link to="/objectives">{copy.viewObjectives} →</Link>
    <Activity key={snapshot.connection?.scope} snapshot={snapshot} />
  </>
}

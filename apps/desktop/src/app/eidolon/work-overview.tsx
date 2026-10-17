import { Link } from 'react-router'

import { useI18n } from '@/i18n/context'
import { observationCopy } from '@/i18n/organization-observation'

import { Activity } from './activity'
import { HomeObjective } from './home'
import { homeData } from './home-data'
import { objectiveWorkData } from './objective-work-data'
import { observationState } from './observation-state'
import type { OrganizationSnapshot } from './types'

export function WorkOverview({ snapshot }: { snapshot: OrganizationSnapshot }) {
  const { t, locale } = useI18n()
  const copy = t.organizationHome
  const objectives = homeData(snapshot).objectives
  const number = new Intl.NumberFormat(locale)
  const observation = observationState(snapshot, snapshot.objectives.length)
  const stateCopy = observationCopy[locale]

  return <>
    <header className="eid-home-hero"><div><h1>{copy.work}</h1><p>{copy.workNote}</p></div></header>
    {observation !== 'current' && <p role="status">{observation === 'retained' ? stateCopy.stale : stateCopy[observation]}</p>}
    <div className="eid-work-overview">
      {objectives.map(objective => {
        const data = objectiveWorkData(objective, snapshot)

        return <section aria-label={objective.title} className="eid-home-panel" key={objective.id}>
          <HomeObjective objective={objective} snapshot={snapshot} />
          <p>{copy.detail.delegationCount(data.groups.length, number.format(data.groups.length))}</p>
          <p>{snapshot.requests ? copy.detail.ownerRequestCount(data.requests.length, number.format(data.requests.length)) : copy.requestsUnavailable}</p>
        </section>
      })}
    </div>
    {!objectives.length && <p>{observation === 'current' ? copy.noWork : observation === 'retained' ? stateCopy.empty : stateCopy.unknown}</p>}
    <p className="eid-note">{copy.snapshotNote}</p>
    <Link to="/objectives">{copy.viewObjectives} →</Link>
    <Activity key={snapshot.connection?.ownerScope ?? snapshot.connection?.scope} snapshot={snapshot} />
  </>
}

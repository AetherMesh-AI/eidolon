import { useI18n } from '@/i18n/context'
import { modelRoutingCopy } from '@/i18n/organization-model-routing'

import type { OrganizationAgent, OrganizationSnapshot } from './types'

export function ModelAssignmentSummary({ agent }: { agent: OrganizationAgent }) {
  const { locale } = useI18n()
  const assignment = agent.modelAssignment
  const copy = modelRoutingCopy[locale]

  return assignment ? <small className="block">{copy.assignment}: {copy[assignment.mode]}</small> : null
}

export function AgentModelAssignment({ agent }: { agent: OrganizationAgent }) {
  const { t, locale } = useI18n()
  const copy = modelRoutingCopy[locale]
  const assignment = agent.modelAssignment

  if (!assignment) {return null}

  return (
    <section aria-label={copy.assignment}>
      <h3>{copy.assignment}</h3>
      <p>{copy[assignment.mode]}</p>
      <dl className="eid-runtime-facts">
        {assignment.mode !== 'legacy' && <>
          <dt>{copy.routeIds}</dt><dd><bdi>{assignment.routeIds.join(assignment.mode === 'fixed' ? ' → ' : ', ')}</bdi></dd>
          <dt>{copy.minimumEvidence}</dt><dd>{assignment.minimumEvidence ?? t.organizationWork.unknownUsage}</dd>
          <dt>{copy.maxFallbacks}</dt><dd>{assignment.maxFallbacks}</dd>
        </>}
      </dl>
      <p className="eid-note">{copy.assignmentNote}</p>
      {agent.id === 'executive' && assignment.mode === 'fixed' && <p className="eid-note">{copy.executiveNote}</p>}
    </section>
  )
}

export function RuntimeModelRouting({ snapshot }: { snapshot: OrganizationSnapshot }) {
  const { t, locale } = useI18n()
  const copy = modelRoutingCopy[locale]
  const routing = snapshot.runtime?.modelRouting

  if (!routing) {return null}

  const number = (value: number) => new Intl.NumberFormat(locale).format(value)

  return (
    <details className="eid-roster-details">
      <summary>{copy.heading}</summary>
      {snapshot.connection?.state !== 'ready' && <p role="status">{copy.stale}</p>}
      <p>{routing.enabled ? copy.enabled : copy.disabled}</p>
      <p>{copy.generation}: {routing.policyGeneration}</p>
      <p className="eid-note">{copy.autoNote}</p>
      <p className="eid-note">{copy.transportNote}</p>
      <p className="eid-note">{copy.fallbackNote}</p>
      <p className="eid-note">{copy.quotaNote}</p>
      <p className="eid-note">{copy.ownerChatNote}</p>
      <section aria-label={copy.routes}>
        <h3>{copy.routes}</h3>
        {!routing.routes.length ? <p>{copy.emptyRoutes}</p> : <ul className="eid-list">
          {routing.routes.map(route => <li key={route.id}>
            <strong><bdi>{route.id}</bdi></strong>
            <dl className="eid-runtime-facts">
              <dt>{t.organizationRuntime.provider}</dt><dd dir="auto">{route.provider}</dd>
              <dt>{t.organizationRuntime.model}</dt><dd dir="auto">{route.model}</dd>
              <dt>{copy.reasoning}</dt><dd>{route.reasoning}</dd>
              <dt>{t.organizationRoster.acceptedTypes}</dt><dd className="eid-result-text"><bdi>{route.requestTypes.join(', ')}</bdi></dd>
              <dt>{copy.evidence}</dt><dd>{route.evidenceLevel}</dd>
              <dt>{copy.contextOutput}</dt><dd>{number(route.maxContextTokens)} / {number(route.maxOutputTokens)}</dd>
            </dl>
            <p>{route.priced ? copy.priced : copy.unpriced}</p>
          </li>)}
        </ul>}
      </section>
      <section aria-label={copy.configuration}>
        <h3>{copy.configuration}</h3>
        <p className="eid-note">{copy.configurationNote}</p>
        <p className="eid-note">{copy.profileNote}</p>
        <p className="eid-result-text" dir="ltr"><code>python -m eidolon_cli.organization_model_routing_cli show</code></p>
        <p className="eid-result-text" dir="ltr"><code>python -m eidolon_cli.organization_model_routing_cli configure --file FILE.json --expected-generation {routing.policyGeneration} --idempotency-key CHANGE_ID</code></p>
      </section>
    </details>
  )
}

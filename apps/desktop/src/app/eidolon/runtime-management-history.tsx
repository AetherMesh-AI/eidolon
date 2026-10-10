import { useI18n } from '@/i18n/context'

import { LocalizedTime } from './localized-time'
import type { OrganizationSnapshot } from './types'

export function OrganizationManagementHistory({ snapshot }: { snapshot: OrganizationSnapshot }) {
  const { t } = useI18n()
  const copy = t.organizationRoster
  const changes = snapshot.runtime?.management?.recentChanges ?? []

  if (!changes.length) {
    return null
  }

  return (
    <section aria-label={copy.changes} className="eid-owner-history">
      <h3>{copy.changes}</h3>
      <ol className="eid-list">
        {changes.map(change => (
          <li key={change.id}>
            <details>
              <summary>
                {change.kind} · {snapshot.agents.find(agent => agent.id === change.subjectId)?.name || change.subjectId}
              </summary>
              <p>
                {copy.actor}: {snapshot.agents.find(agent => agent.id === change.actorId)?.name || change.actorId}
              </p>
              <LocalizedTime value={change.createdAt} />
              {change.requestId && (
                <p>
                  {t.organizationWork.requests}: {change.requestId}
                </p>
              )}
              <h4>{copy.before}</h4>
              <pre className="eid-result-text">{JSON.stringify(change.before, null, 2)}</pre>
              <h4>{copy.after}</h4>
              <pre className="eid-result-text">{JSON.stringify(change.after, null, 2)}</pre>
            </details>
          </li>
        ))}
      </ol>
    </section>
  )
}

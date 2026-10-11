import { useStore } from '@nanostores/react'
import { Link, useSearchParams } from 'react-router'

import { useOrganizationShellCopy } from '@/app/contrib/organization-copy'
import { LocalizedTime } from '@/app/eidolon/localized-time'
import { RuntimeCapabilities } from '@/app/eidolon/runtime-capabilities'
import { RuntimeStatus } from '@/app/eidolon/runtime-detail'
import { useRuntimeOrganization } from '@/app/eidolon/runtime-provider'
import { Knowledge } from '@/app/eidolon/workspace'
import { PAGE_INSET_X } from '@/app/layout-constants'
import type { SetStatusbarItemGroup } from '@/app/shell/statusbar-controls'
import { useI18n } from '@/i18n'
import { $activeConnectionId } from '@/store/connections'
import { $activeGatewayProfile } from '@/store/profile'

import { ArtifactsView } from './index'

/** One destination, two explicitly separate authorities. Session artifacts
 * keep session navigation; runtime evidence keeps immutable ledger provenance.
 * No record is copied, re-keyed, or promoted between those stores. */
export function ArtifactWorkspace({ setStatusbarItemGroup }: { setStatusbarItemGroup?: SetStatusbarItemGroup }) {
  const copy = useOrganizationShellCopy()
  const { t } = useI18n()
  const surface = t.organizationSurfaces
  const [params] = useSearchParams()
  const organization = useRuntimeOrganization()
  const connectionId = useStore($activeConnectionId)
  const profile = useStore($activeGatewayProfile)
  const evidence = params.get('source') === 'organization'

  const sourceLink = (source: string) => {
    const next = new URLSearchParams(params)
    next.set('source', source)

    return `/artifacts?${next}`
  }

  return (
    <section aria-label={copy.artifacts} className="eidolon eid-deliverables flex h-full min-h-0 flex-col">
      <header className="eid-deliverables-heading">
        <h1>{surface.deliverables}</h1>
        <p>{surface.deliverablesIntroduction}</p>
        <nav aria-label={copy.sources} className="flex items-center gap-4">
          <Link
            aria-current={!evidence ? 'page' : undefined}
            className={!evidence ? 'font-semibold underline' : ''}
            to={sourceLink('sessions')}
          >
            {copy.sessions}
          </Link>
          <Link
            aria-current={evidence ? 'page' : undefined}
            className={evidence ? 'font-semibold underline' : ''}
            to={sourceLink('organization')}
          >
            {copy.evidence}
          </Link>
        </nav>
        <p className="mt-2 text-xs text-(--ui-text-tertiary)">{evidence ? copy.evidenceOrigin : copy.sessionOrigin}</p>
      </header>
      {evidence ? (
        <div className={`eidolon eid-workspace ${PAGE_INSET_X} min-h-0 flex-1 overflow-auto`}>
          {organization ? (
            <>
              <RuntimeStatus adapter={organization.adapter} snapshot={organization.snapshot} warningsOnly />
              <section aria-label={surface.acceptedOutcomes} className="eid-home-panel eid-delivery-outcomes">
                <header>
                  <h2>{surface.acceptedOutcomes}</h2>
                  <p>{surface.acceptedNote}</p>
                </header>
                {organization.snapshot.outcomes?.items
                  .filter(item => item.status === 'accepted' && item.deliverableId)
                  .map(item => (
                    <article className="eid-home-record" key={item.objectiveId}>
                      <small>{t.organizationHome.accepted}</small>
                      <h3>{item.title}</h3>
                      <p>{item.summary}</p>
                      <LocalizedTime value={item.updatedAt} />
                      <Link to={`/objectives/${encodeURIComponent(item.objectiveId)}`}>
                        {t.organizationHome.viewObjective} →
                      </Link>
                    </article>
                  ))}
                {!organization.snapshot.outcomes?.items.some(
                  item => item.status === 'accepted' && item.deliverableId
                ) && (
                  <p>
                    {organization.snapshot.outcomes
                      ? t.organizationHome.noReady
                      : t.organizationHome.outcomesUnavailable}
                  </p>
                )}
                <Link to="/objectives">{t.organizationHome.viewObjectives} →</Link>
              </section>
              {organization.snapshot.runtime && (
                <details className="eid-delivery-runtime">
                  <summary>
                    {t.organizationRuntime.configuration}
                    {organization.snapshot.runtime.profile && ` · ${organization.snapshot.runtime.profile}`}
                  </summary>
                  <RuntimeCapabilities runtime={organization.snapshot.runtime} />
                  <p className="eid-note">{organization.snapshot.runtime.scope}</p>
                </details>
              )}
              <h2>{surface.evidenceHeading}</h2>
              <Knowledge
                adapter={organization.adapter}
                key={organization.snapshot.connection?.ownerScope}
                snapshot={organization.snapshot}
              />
            </>
          ) : (
            <p>{copy.unavailable}</p>
          )}
        </div>
      ) : (
        <div className="min-h-0 flex-1">
          <ArtifactsView embedded key={`${connectionId}:${profile}`} setStatusbarItemGroup={setStatusbarItemGroup} />
        </div>
      )}
    </section>
  )
}

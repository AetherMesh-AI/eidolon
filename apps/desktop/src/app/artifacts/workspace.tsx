import { useStore } from '@nanostores/react'
import { Link, useSearchParams } from 'react-router'

import { useOrganizationShellCopy } from '@/app/contrib/organization-copy'
import { RuntimeStatus } from '@/app/eidolon/runtime-detail'
import { useRuntimeOrganization } from '@/app/eidolon/runtime-provider'
import { Knowledge } from '@/app/eidolon/workspace'
import { PAGE_INSET_X } from '@/app/layout-constants'
import type { SetStatusbarItemGroup } from '@/app/shell/statusbar-controls'
import { $activeConnectionId } from '@/store/connections'
import { $activeGatewayProfile } from '@/store/profile'

import { ArtifactsView } from './index'

/** One destination, two explicitly separate authorities. Session artifacts
 * keep session navigation; runtime evidence keeps immutable ledger provenance.
 * No record is copied, re-keyed, or promoted between those stores. */
export function ArtifactWorkspace({ setStatusbarItemGroup }: { setStatusbarItemGroup?: SetStatusbarItemGroup }) {
  const copy = useOrganizationShellCopy()
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
    <section aria-label={copy.artifacts} className="flex h-full min-h-0 flex-col">
      <header className={`${PAGE_INSET_X} shrink-0 pt-[calc(var(--titlebar-height)+0.375rem)] pb-2`}>
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
              <RuntimeStatus adapter={organization.adapter} snapshot={organization.snapshot} />
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

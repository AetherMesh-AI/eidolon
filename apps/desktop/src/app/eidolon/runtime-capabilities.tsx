import { useI18n } from '@/i18n/context'

import type { OrganizationRuntime } from './types'

export function RuntimeCapabilities({ runtime }: { runtime: OrganizationRuntime }) {
  const { t } = useI18n()
  const copy = t.organizationRuntime

  const grant =
    runtime.readFileEnabled === true
      ? copy.readGranted
      : runtime.readFileEnabled === false
        ? copy.readDisabled
        : copy.readUnknown

  return (
    <section aria-label={copy.configuration} className="eid-runtime-capabilities">
      <dl className="eid-runtime-facts">
        <dt>{copy.capabilities}</dt>
        <dd>{runtime.capabilities.join(', ') || copy.noCapabilities}</dd>
        <dt>{copy.readGrant}</dt>
        <dd>{grant}</dd>
        <dt>{copy.edits.support}</dt>
        <dd>
          {runtime.supportsWorkspaceEdits === true
            ? copy.edits.supported
            : runtime.supportsWorkspaceEdits === false
              ? copy.edits.unsupported
              : copy.edits.supportUnknown}
        </dd>
        <dt>{copy.edits.grant}</dt>
        <dd>
          {runtime.workspaceApplyEnabled === true
            ? copy.edits.granted
            : runtime.workspaceApplyEnabled === false
              ? copy.edits.disabled
              : copy.edits.grantUnknown}
        </dd>
        {runtime.maxToolCalls !== undefined && (
          <>
            <dt>{copy.maxToolCalls}</dt>
            <dd>{runtime.maxToolCalls}</dd>
          </>
        )}
      </dl>
      {runtime.readFileEnabled === true && (
        <details>
          <summary>
            {copy.roots} ({runtime.readRoots?.length ?? 0})
          </summary>
          {runtime.readRoots?.length ? (
            <ul>
              {runtime.readRoots.map(root => (
                <li className="eid-result-text" key={root}>
                  {root}
                </li>
              ))}
            </ul>
          ) : (
            <p>{copy.noRoots}</p>
          )}
        </details>
      )}
      <p className="eid-note">{copy.scopeNote}</p>
      {runtime.supportsWorkspaceEdits === true && <p className="eid-note">{copy.edits.scopeNote}</p>}
    </section>
  )
}

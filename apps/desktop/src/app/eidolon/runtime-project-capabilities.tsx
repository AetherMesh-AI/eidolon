import { useI18n } from '@/i18n/context'

import type { OrganizationRuntime } from './types'

export function RuntimeProjectCapabilities({ runtime }: { runtime: OrganizationRuntime }) {
  const { t } = useI18n()
  const copy = t.organizationWork

  const grant = (enabled?: boolean) =>
    enabled === true ? copy.projectGrantEnabled : enabled === false ? copy.projectGrantDisabled : copy.notRecorded

  // Older runtimes do not advertise this runner. Do not infer support from
  // managed validation, workspace edit support or a read-file grant.
  if (runtime.projectExecutionEnabled === undefined && runtime.sourceIntegrationEnabled === undefined) {
    return null
  }

  return (
    <>
      <dl className="eid-runtime-facts">
        <dt>{copy.projectRunnerSupport}</dt>
        <dd>{copy.projectPlatformNote}</dd>
        <dt>{copy.projectExecutionGrant}</dt>
        <dd>{grant(runtime.projectExecutionEnabled)}</dd>
        <dt>{copy.sourceIntegrationGrant}</dt>
        <dd>{grant(runtime.sourceIntegrationEnabled)}</dd>
      </dl>
      <p className="eid-note">{copy.projectRunnerScope}</p>
      <details>
        <summary>
          {copy.projectRecipes} ({runtime.projectRecipes?.length ?? 0})
        </summary>
        {runtime.projectRecipes?.length ? (
          <ul>
            {runtime.projectRecipes.map(recipe => (
              <li key={recipe.id}>
                <strong>{recipe.id}</strong>
                <dl>
                  <dt>{copy.projectRecipe}</dt>
                  <dd>{recipe.recipe}</dd>
                  <dt>{copy.projectRoot}</dt>
                  <dd>{recipe.root}</dd>
                </dl>
                <ul>
                  {recipe.files.map(file => (
                    <li className="eid-result-text" key={file}>
                      {file}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        ) : (
          <p>{copy.noProjectRecipes}</p>
        )}
      </details>
    </>
  )
}

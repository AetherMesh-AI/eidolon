import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n/context'

import { RuntimeProjectExecution } from './runtime-project-execution'
import type { Objective } from './types'

export function RuntimeAcceptance({
  objective,
  onOpenEvidence
}: {
  objective: Objective
  onOpenEvidence(id: string): void
}) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const acceptance = objective.acceptance
  const usage = objective.usage

  return (
    <section aria-label={copy.acceptance}>
      <h2>{copy.acceptance}</h2>
      {acceptance && (
        <>
          <p role="status">{copy[acceptance.status]}</p>
          <p>
            {copy.round}: {acceptance.round} · {copy.maxReplans}: {acceptance.maxReplans}
          </p>
          {acceptance.criteria.length > 0 && (
            <>
              <h3>{copy.acceptanceCriteria}</h3>
              <ul>
                {acceptance.criteria.map((criterion, index) => (
                  <li key={index}>{criterion}</li>
                ))}
              </ul>
            </>
          )}
          {acceptance.summary && <p className="eid-result-text">{acceptance.summary}</p>}
        </>
      )}
      {objective.requiredChecks?.length ? (
        <>
          <h3>{copy.requiredChecks}</h3>
          <ul>
            {objective.requiredChecks.map(check => (
              <li key={check}>{copy[check]}</li>
            ))}
          </ul>
        </>
      ) : null}
      <p className="eid-note">{copy.acceptanceNote}</p>
      <h3>{copy.result}</h3>
      <p className="eid-result-text">{objective.result || copy.noResult}</p>
      {acceptance?.deliverableId && (
        <Button onClick={() => onOpenEvidence(acceptance.deliverableId!)} size="sm" variant="secondary">
          {copy.openDeliverable}
        </Button>
      )}
      {objective.projectValidation && (
        <section aria-label={copy.finalManagedValidation}>
          <h3>{copy.finalManagedValidation}</h3>
          <p>{copy[objective.projectValidation.status]}</p>
          <p className="eid-note">{copy.validationNote}</p>
          <p>
            {copy.notExecuted}: {objective.projectValidation.notExecuted.join(', ')}
          </p>
          <Button onClick={() => onOpenEvidence(objective.projectValidation!.id)} size="sm" variant="secondary">
            {copy.finalManagedValidation}
          </Button>
        </section>
      )}
      {(objective.projectExecution || objective.requiredChecks?.includes('project_tests')) && (
        <RuntimeProjectExecution execution={objective.projectExecution} onOpenEvidence={onOpenEvidence} />
      )}
      {usage && (
        <details>
          <summary>{copy.usage}</summary>
          <dl>
            <dt>{copy.stages}</dt>
            <dd>
              {usage.stages} / {usage.stageLimit}
            </dd>
            <dt>{copy.tokens}</dt>
            <dd>
              {usage.inputTokens} / {usage.outputTokens}
            </dd>
            {usage.modelCalls !== undefined && usage.modelCallLimit !== undefined && (
              <>
                <dt>{copy.modelCalls}</dt>
                <dd>
                  {usage.modelCalls} / {usage.modelCallLimit}
                </dd>
              </>
            )}
            {usage.reservedTokens !== undefined && usage.tokenLimit !== undefined && (
              <>
                <dt>{copy.reservedTokens}</dt>
                <dd>
                  {usage.reservedTokens} / {usage.tokenLimit}
                </dd>
              </>
            )}
            {usage.deadlineAt && (
              <>
                <dt>{copy.objectiveDeadline}</dt>
                <dd>
                  <time dateTime={usage.deadlineAt}>{new Date(usage.deadlineAt).toLocaleString()}</time>
                </dd>
              </>
            )}
            {usage.configuredCostLimitUsd != null && (
              <>
                <dt>{copy.configuredCost}</dt>
                <dd>
                  {usage.configuredCostReservedUsd ?? copy.unknownUsage} / {usage.configuredCostLimitUsd}
                </dd>
              </>
            )}
          </dl>
          <p className="eid-note">{usage.usageComplete ? copy.usageNote : copy.incompleteUsage}</p>
          {usage.budgetScope && <p className="eid-note">{copy.reservationNote}</p>}
          {usage.configuredCostLimitUsd != null && <p className="eid-note">{copy.configuredCostNote}</p>}
          {usage.legacyUsageUnknown && <p className="eid-note">{copy.legacyUsageUnknown}</p>}
        </details>
      )}
    </section>
  )
}

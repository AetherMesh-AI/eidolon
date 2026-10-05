import { controlVariants } from '@/components/ui/control'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n/context'

import type { OrganizationRequiredCheck, OrganizationScopeAmendment } from './types'

const checks: OrganizationRequiredCheck[] = ['project_tests', 'managed_validation', 'source_integration']

export interface ScopeAmendmentDraft {
  criteria: string
  requiredChecks?: OrganizationRequiredCheck[]
}

export function amendmentCriteria(draft: ScopeAmendmentDraft, response: string) {
  const criteria = draft.criteria
    .split('\n')
    .map(value => value.trim())
    .filter(Boolean)

  const effective = criteria.length ? criteria : [response.trim()]

  return {
    criteria: criteria.length ? criteria : undefined,
    invalid: effective.length > 12 || effective.some(value => value.length > 2000)
  }
}

export function ScopeAmendmentFields({
  currentChecks,
  draft,
  disabled,
  onChange
}: {
  currentChecks: OrganizationRequiredCheck[]
  draft: ScopeAmendmentDraft
  disabled: boolean
  onChange(draft: ScopeAmendmentDraft): void
}) {
  const { t } = useI18n()
  const copy = t.organizationWork

  return (
    <>
      <p className="eid-note">{copy.amendScopeNote}</p>
      <section aria-label={copy.currentRequiredChecks}>
        <h4>{copy.currentRequiredChecks}</h4>
        {currentChecks.length ? (
          <ul>
            {currentChecks.map(check => (
              <li key={check}>{copy[check]}</li>
            ))}
          </ul>
        ) : (
          <p>{copy.noRequiredChecks}</p>
        )}
      </section>
      <label>
        {copy.acceptanceCriteria}
        <Textarea
          disabled={disabled}
          maxLength={24011}
          onChange={event => onChange({ ...draft, criteria: event.target.value })}
          rows={3}
          value={draft.criteria}
        />
      </label>
      <p className="eid-note">{copy.amendedCriteriaHint}</p>
      <label>
        {copy.amendmentCheckChoice}
        <select
          className={controlVariants()}
          disabled={disabled}
          onChange={event =>
            onChange({ ...draft, requiredChecks: event.target.value === 'replace' ? [...currentChecks] : undefined })
          }
          value={draft.requiredChecks === undefined ? 'keep' : 'replace'}
        >
          <option value="keep">{copy.keepRequiredChecks}</option>
          <option value="replace">{copy.replaceRequiredChecks}</option>
        </select>
      </label>
      {draft.requiredChecks !== undefined && (
        <fieldset disabled={disabled}>
          <legend>{copy.requiredChecks}</legend>
          <p className="eid-note">{copy.amendmentCheckWarning}</p>
          {checks.map(check => (
            <label className="eid-inline" key={check}>
              <input
                checked={draft.requiredChecks?.includes(check) ?? false}
                onChange={event =>
                  onChange({
                    ...draft,
                    requiredChecks: event.target.checked
                      ? checks.filter(item => item === check || draft.requiredChecks?.includes(item))
                      : draft.requiredChecks?.filter(item => item !== check)
                  })
                }
                type="checkbox"
              />
              <span>{copy[check]}</span>
            </label>
          ))}
        </fieldset>
      )}
    </>
  )
}

export function ScopeAmendmentAudit({ amendment }: { amendment: OrganizationScopeAmendment }) {
  const { t } = useI18n()
  const copy = t.organizationWork

  return (
    <>
      {(['before', 'after'] as const).map(side => (
        <section aria-label={side === 'before' ? copy.beforeAmendment : copy.afterAmendment} key={side}>
          <h4>{side === 'before' ? copy.beforeAmendment : copy.afterAmendment}</h4>
          <p>
            {copy.round}: {amendment[side].round}
          </p>
          <p>
            {copy.effectiveScope}: {amendment[side].scope}
          </p>
          <h5>{copy.acceptanceCriteria}</h5>
          <ul>
            {amendment[side].acceptanceCriteria.map((criterion, index) => (
              <li key={index}>{criterion}</li>
            ))}
          </ul>
          <h5>{copy.requiredChecks}</h5>
          {amendment[side].requiredChecks.length ? (
            <ul>
              {amendment[side].requiredChecks.map(check => (
                <li key={check}>{copy[check]}</li>
              ))}
            </ul>
          ) : (
            <p>{copy.noRequiredChecks}</p>
          )}
        </section>
      ))}
      <p className="eid-result-text">
        {copy.receiptDigest}: {amendment.sha256}
      </p>
    </>
  )
}

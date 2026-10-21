import { useId, useRef } from 'react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import type { TeamOnboardingCopy } from '@/i18n/organization-team-onboarding'

export interface TeamSetupInputs {
  jobs: string
  goals: string
  namingStyle: string
}

export interface TeamInputReview {
  field: 'jobs' | 'goals'
  original: string
  suggestion: string
  assumptions: string[]
}

interface TeamOnboardingInputsProps {
  copy: TeamOnboardingCopy
  inputs: TeamSetupInputs
  disabled: boolean
  canGenerate: boolean
  canImprove: Record<'jobs' | 'goals', boolean>
  modelBlocked: boolean
  improvementSupported: boolean
  readinessId: string
  review: TeamInputReview | null
  onChange: (field: keyof TeamSetupInputs, value: string) => void
  onImprove: (field: 'jobs' | 'goals') => void
  onGenerate: () => void
  onSuggestionChange: (value: string) => void
  onApply: () => boolean
  onDiscard: () => void
}

export function TeamOnboardingInputs({
  copy,
  inputs,
  disabled,
  canGenerate,
  canImprove,
  modelBlocked,
  improvementSupported,
  readinessId,
  review,
  onChange,
  onImprove,
  onGenerate,
  onSuggestionChange,
  onApply,
  onDiscard
}: TeamOnboardingInputsProps) {
  const id = useId()
  const fields = useRef<Partial<Record<'jobs' | 'goals', HTMLTextAreaElement | null>>>({})

  return (
    <fieldset className="eid-team-fields" disabled={disabled}>
      <span aria-live="polite" className="sr-only">
        {review ? `${copy.reviewSuggestion}: ${copy[review.field]}` : ''}
      </span>
      {(['jobs', 'goals'] as const).map(field => {
        const help = field === 'jobs' ? copy.jobsHelp : copy.goalsHelp
        const example = field === 'jobs' ? copy.jobsExample : copy.goalsExample
        const improveName = field === 'jobs' ? copy.improveJobs : copy.improveGoals

        return (
          <div className="eid-team-input" key={field}>
            <div className="eid-team-input-heading">
              <label htmlFor={`${id}-${field}`}>{copy[field]}</label>
              <Button
                aria-describedby={`${id}-improve-help`}
                aria-label={improveName}
                disabled={disabled || !improvementSupported || !canImprove[field] || !inputs[field].trim()}
                onClick={() => onImprove(field)}
                size="sm"
                variant="secondary"
              >
                {copy.improve}
              </Button>
            </div>
            <p className="eid-team-note" id={`${id}-${field}-help`}>
              {help}
            </p>
            <Textarea
              aria-describedby={`${id}-${field}-help`}
              dir="auto"
              id={`${id}-${field}`}
              maxLength={4000}
              onChange={event => onChange(field, event.target.value)}
              placeholder={example}
              ref={node => {
                fields.current[field] = node
              }}
              value={inputs[field]}
            />
            {review?.field === field && (
              <section aria-label={copy.reviewSuggestion} className="eid-team-suggestion">
                <strong>{copy.reviewSuggestion}</strong>
                <p className="eid-team-note">{copy.suggestionReviewHelp}</p>
                <strong>{copy.original}</strong>
                <p className="eid-team-original" dir="auto">
                  {review.original}
                </p>
                <label htmlFor={`${id}-suggestion`}>{copy.suggestion}</label>
                <Textarea
                  dir="auto"
                  id={`${id}-suggestion`}
                  maxLength={4000}
                  onChange={event => onSuggestionChange(event.target.value)}
                  value={review.suggestion}
                />
                <strong>{copy.assumptions}</strong>
                {review.assumptions.length ? (
                  <ul>
                    {review.assumptions.map((assumption, index) => (
                      <li dir="auto" key={index}>
                        {assumption}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="eid-team-note">{copy.noAssumptions}</p>
                )}
                <div className="eid-team-actions">
                  <Button
                    disabled={disabled || !review.suggestion.trim()}
                    onClick={() => {
                      if (onApply()) {
                        fields.current[field]?.focus({ preventScroll: true })
                      }
                    }}
                    size="sm"
                  >
                    {copy.applySuggestion}
                  </Button>
                  <Button
                    disabled={disabled}
                    onClick={() => {
                      onDiscard()
                      fields.current[field]?.focus({ preventScroll: true })
                    }}
                    size="sm"
                    variant="text"
                  >
                    {copy.discardSuggestion}
                  </Button>
                </div>
              </section>
            )}
          </div>
        )
      })}
      <div className="eid-team-input">
        <label htmlFor={`${id}-naming`}>{copy.namingStyle}</label>
        <p className="eid-team-note" id={`${id}-naming-help`}>
          {copy.namingHelp}
        </p>
        <Textarea
          aria-describedby={`${id}-naming-help`}
          dir="auto"
          id={`${id}-naming`}
          maxLength={500}
          onChange={event => onChange('namingStyle', event.target.value)}
          placeholder={copy.namingExample}
          value={inputs.namingStyle}
        />
      </div>
      <p className="eid-team-note" id={`${id}-improve-help`}>
        {improvementSupported ? copy.improveHelp : copy.improveUnavailable}
      </p>
      <p className="eid-team-note">
        {copy.generationCost} {copy.generationCancel}
      </p>
      <Button
        aria-describedby={modelBlocked ? readinessId : undefined}
        disabled={disabled || modelBlocked || !canGenerate || !inputs.jobs.trim() || !inputs.goals.trim() || !!review}
        onClick={onGenerate}
      >
        {copy.generate}
      </Button>
    </fieldset>
  )
}

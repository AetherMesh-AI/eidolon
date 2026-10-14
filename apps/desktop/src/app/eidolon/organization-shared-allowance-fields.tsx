import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { useI18n } from '@/i18n/context'
import { ownerPolicyCopy } from '@/i18n/organization-owner-policy'
import { sharedAllowanceCopy } from '@/i18n/organization-shared-allowance'

import type {
  AllowanceAmounts,
  SharedAllowanceConfiguration,
  SharedAllowanceDescriptor,
  SharedAllowanceObservation
} from './organization-shared-allowance-types'

export const emptyAllowanceAmounts = (): AllowanceAmounts => ({ modelCalls: 0, tokens: 0, configuredCostUsd: null })
export const emptySharedAllowance = (): SharedAllowanceConfiguration => ({
  periodId: '',
  startsAt: '',
  endsAt: '',
  limits: emptyAllowanceAmounts(),
  protected: emptyAllowanceAmounts(),
  allocations: []
})
const numberValue = (value: number) => (Number.isFinite(value) ? value : '')

function AmountFields({
  label,
  value,
  update,
  positive = false
}: {
  label: string
  value: AllowanceAmounts
  update: (value: AllowanceAmounts) => void
  positive?: boolean
}) {
  const { locale } = useI18n()
  const copy = sharedAllowanceCopy[locale]

  return (
    <fieldset>
      <legend>{label}</legend>
      <div className="eid-policy-fields">
        {(['modelCalls', 'tokens'] as const).map(field => (
          <label key={field}>
            {field === 'modelCalls' ? copy.calls : copy.tokens}
            <Input
              min={positive ? 1 : 0}
              onChange={event => update({ ...value, [field]: event.target.valueAsNumber })}
              required
              step={1}
              type="number"
              value={numberValue(value[field])}
            />
          </label>
        ))}
        <label>
          {copy.cost}
          <Input
            inputMode="decimal"
            onChange={event => update({ ...value, configuredCostUsd: event.target.value || null })}
            value={value.configuredCostUsd ?? ''}
          />
        </label>
      </div>
    </fieldset>
  )
}

export function SharedAllowanceFields({
  value,
  update,
  descriptor
}: {
  value: SharedAllowanceConfiguration
  update: (value: SharedAllowanceConfiguration) => void
  descriptor: SharedAllowanceDescriptor
}) {
  const { locale } = useI18n()
  const copy = sharedAllowanceCopy[locale]
  const common = ownerPolicyCopy[locale]
  const lockedPeriod = Boolean(descriptor.configuration && Date.parse(descriptor.configuration.endsAt) > Date.now())

  const orphaned = value.allocations.filter(
    item =>
      !descriptor.candidates.some(
        candidate =>
          candidate.profile === item.profile &&
          candidate.organizationId === item.organizationId &&
          candidate.ownerId === item.ownerId
      )
  )

  return (
    <>
      <p>{copy.noRollover}</p>
      {lockedPeriod && <p>{copy.periodLocked}</p>}
      <fieldset disabled={lockedPeriod}>
        <legend>{copy.period}</legend>
        <label>
          {copy.label}
          <Input
            maxLength={128}
            onChange={event => update({ ...value, periodId: event.target.value })}
            required
            value={value.periodId}
          />
        </label>
        <div className="eid-policy-fields">
          {(['startsAt', 'endsAt'] as const).map(field => (
            <label key={field}>
              {copy[field]}
              <Input
                onChange={event => update({ ...value, [field]: event.target.value })}
                placeholder={new Date().toISOString()}
                required
                value={value[field]}
              />
            </label>
          ))}
        </div>
      </fieldset>
      <AmountFields label={copy.limits} positive update={limits => update({ ...value, limits })} value={value.limits} />
      <AmountFields
        label={copy.protected}
        update={protectedAmounts => update({ ...value, protected: protectedAmounts })}
        value={value.protected}
      />
      <section aria-label={copy.allocations}>
        <h3>{copy.allocations}</h3>
        <p>{copy.allocationNote}</p>
        {!descriptor.candidates.length && <p>{copy.noCandidates}</p>}
        {descriptor.candidates.map(candidate => {
          const saved = descriptor.configuration?.allocations.find(item => item.profile === candidate.profile)

          const enrolled = value.allocations.find(
            item =>
              item.profile === candidate.profile &&
              item.organizationId === candidate.organizationId &&
              item.ownerId === candidate.ownerId
          )

          const change = (fields: { limits?: AllowanceAmounts; protected?: AllowanceAmounts }) =>
            update({
              ...value,
              allocations: value.allocations.map(item => (item === enrolled ? { ...item, ...fields } : item))
            })

          return (
            <section className="eid-allowance-allocation" key={candidate.profile}>
              <label className="eid-policy-check">
                <Checkbox
                  aria-label={`${copy.enroll}: ${candidate.displayName} (${candidate.profile})`}
                  checked={Boolean(enrolled)}
                  disabled={Boolean(saved)}
                  onCheckedChange={checked =>
                    update({
                      ...value,
                      allocations: checked
                        ? [
                            ...value.allocations.filter(item => item.profile !== candidate.profile),
                            {
                              profile: candidate.profile,
                              organizationId: candidate.organizationId,
                              ownerId: candidate.ownerId,
                              limits: emptyAllowanceAmounts(),
                              protected: emptyAllowanceAmounts()
                            }
                          ]
                        : value.allocations.filter(item => item !== enrolled)
                    })
                  }
                />
                <span>
                  <bdi>{candidate.displayName}</bdi> · <bdi>{candidate.profile}</bdi> ·{' '}
                  <bdi>{candidate.ownerDisplayName}</bdi>
                </span>
              </label>
              {enrolled && (
                <>
                  <p>
                    {common.organizationId}: <bdi>{candidate.organizationId}</bdi>
                    <br />
                    {common.ownerId}: <bdi>{candidate.ownerId}</bdi>
                  </p>
                  <AmountFields
                    label={copy.allocation}
                    positive
                    update={limits => change({ limits })}
                    value={enrolled.limits}
                  />
                  <AmountFields
                    label={copy.protected}
                    update={protectedAmounts => change({ protected: protectedAmounts })}
                    value={enrolled.protected}
                  />
                </>
              )}
            </section>
          )
        })}
        {orphaned.map(item => (
          <section key={`${item.profile}:${item.organizationId}`}>
            <p role="alert">
              {copy.enrollmentConflict} <bdi>{item.profile}</bdi>
            </p>
            <Button
              disabled={descriptor.configuration?.allocations.some(saved => saved.profile === item.profile)}
              onClick={() => update({ ...value, allocations: value.allocations.filter(row => row !== item) })}
              type="button"
              variant="secondary"
            >
              {common.remove} <bdi>{item.profile}</bdi>
            </Button>
          </section>
        ))}
      </section>
    </>
  )
}

export function SharedObservationFields({
  value,
  update
}: {
  value: SharedAllowanceObservation
  update: (value: SharedAllowanceObservation) => void
}) {
  const { locale } = useI18n()
  const copy = ownerPolicyCopy[locale]

  return (
    <>
      <p>{sharedAllowanceCopy[locale].observationNote}</p>
      <label>
        {copy.remainingPercent}
        <Input
          max={100}
          min={0}
          onChange={event => update({ ...value, remainingPercent: event.target.valueAsNumber })}
          required
          step="any"
          type="number"
          value={numberValue(value.remainingPercent)}
        />
      </label>
      <div className="eid-policy-fields">
        {(['asOf', 'expiresAt', 'resetAt'] as const).map(field => (
          <label key={field}>
            {copy[field]}
            <Input
              onChange={event => update({ ...value, [field]: event.target.value || (field === 'resetAt' ? null : '') })}
              placeholder={new Date().toISOString()}
              required={field !== 'resetAt'}
              value={value[field] ?? ''}
            />
          </label>
        ))}
      </div>
    </>
  )
}

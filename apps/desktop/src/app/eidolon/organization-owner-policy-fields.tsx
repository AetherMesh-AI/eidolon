import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { controlVariants } from '@/components/ui/control'
import { Input } from '@/components/ui/input'
import { useI18n } from '@/i18n/context'
import { ownerPolicyCopy } from '@/i18n/organization-owner-policy'

import {
  type BudgetNumber,
  budgetNumbers,
  type OwnerBudgetConfiguration,
  type OwnerBudgetObservation,
  type OwnerModelAssignment,
  ownerModelRequestTypes,
  type OwnerModelRoute,
  type OwnerPolicyDescriptor,
  type OwnerRoutingConfiguration
} from './organization-owner-policy-types'

export interface PolicyFieldsProps<T> {
  value: T
  update: (value: T) => void
  descriptor: OwnerPolicyDescriptor
}
const numberValue = (value: number) => (Number.isFinite(value) ? value : '')

export function RoutingFields({ value, update, descriptor }: PolicyFieldsProps<OwnerRoutingConfiguration>) {
  const { locale } = useI18n()
  const copy = ownerPolicyCopy[locale]
  const managed = descriptor.managedFields.includes('model_routes')

  const change = (index: number, next: Partial<OwnerModelRoute>) =>
    update({
      ...value,
      model_routes: value.model_routes.map((route, i) => (i === index ? { ...route, ...next } : route))
    })

  return (
    <>
      <p>{copy.routingNote}</p>
      <p>{copy.allAssignments}</p>
      {managed && <p>{copy.managed}</p>}
      <fieldset disabled={managed}>
        {value.model_routes.map((route, index) => (
          <details className="eid-policy-row" key={index} open>
            <summary>
              {copy.routes} {index + 1}: <bdi>{route.id}</bdi>
            </summary>
            <div className="eid-policy-fields">
              <label>
                {copy.routeId}
                <Input
                  maxLength={64}
                  onChange={event => change(index, { id: event.target.value })}
                  required
                  value={route.id}
                />
              </label>
              <label>
                {copy.provider}
                <Input
                  maxLength={300}
                  onChange={event => change(index, { provider: event.target.value })}
                  required
                  value={route.provider}
                />
              </label>
              <label>
                {copy.model}
                <Input
                  maxLength={300}
                  onChange={event => change(index, { model: event.target.value })}
                  required
                  value={route.model}
                />
              </label>
              <label>
                {copy.reasoning}
                <select
                  aria-label={copy.reasoning}
                  className={controlVariants()}
                  onChange={event => change(index, { reasoning: event.target.value })}
                  value={route.reasoning}
                >
                  {['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'].map(item => (
                    <option key={item}>{item}</option>
                  ))}
                </select>
              </label>
              <fieldset className="eid-policy-choices">
                <legend>{copy.requestTypes}</legend>
                {ownerModelRequestTypes.map(kind => (
                  <label className="eid-policy-check" key={kind}>
                    <Checkbox
                      checked={route.request_types.includes(kind)}
                      onCheckedChange={checked =>
                        change(index, {
                          request_types: checked
                            ? [...route.request_types, kind]
                            : route.request_types.filter(item => item !== kind)
                        })
                      }
                    />
                    <bdi>{kind}</bdi>
                  </label>
                ))}
              </fieldset>
              <label>
                {copy.evidence}
                <Input
                  max={3}
                  min={1}
                  onChange={event => change(index, { evidence_level: event.target.valueAsNumber })}
                  required
                  step={1}
                  type="number"
                  value={numberValue(route.evidence_level)}
                />
              </label>
              <label>
                {copy.context}
                <Input
                  max={2000000}
                  min={4096}
                  onChange={event => change(index, { max_context_tokens: event.target.valueAsNumber })}
                  required
                  step={1}
                  type="number"
                  value={numberValue(route.max_context_tokens)}
                />
              </label>
              <label>
                {copy.output}
                <Input
                  max={16000}
                  min={256}
                  onChange={event => change(index, { max_output_tokens: event.target.valueAsNumber })}
                  required
                  step={1}
                  type="number"
                  value={numberValue(route.max_output_tokens)}
                />
              </label>
            </div>
            <Button
              onClick={() => update({ ...value, model_routes: value.model_routes.filter((_, i) => i !== index) })}
              type="button"
              variant="ghost"
            >
              {copy.remove} {route.id || index + 1}
            </Button>
          </details>
        ))}
        <Button
          disabled={value.model_routes.length >= 64}
          onClick={() =>
            update({
              ...value,
              model_routes: [
                ...value.model_routes,
                {
                  id: '',
                  provider: '',
                  model: '',
                  reasoning: 'none',
                  request_types: [],
                  evidence_level: 1,
                  max_context_tokens: 32768,
                  max_output_tokens: 8000
                }
              ]
            })
          }
          type="button"
          variant="secondary"
        >
          {copy.addRoute}
        </Button>
      </fieldset>
    </>
  )
}

export function AssignmentFields({
  value,
  update,
  descriptor,
  agentId
}: PolicyFieldsProps<OwnerRoutingConfiguration> & { agentId: string }) {
  const { locale } = useI18n()
  const copy = ownerPolicyCopy[locale]
  const assignment = value.model_assignments.find(item => item.agent_id === agentId)
  const managed = descriptor.managedFields.includes('model_assignments')

  const change = (next: Partial<OwnerModelAssignment>) => {
    const item: OwnerModelAssignment = {
      agent_id: agentId,
      mode: 'fixed',
      route_ids: [],
      minimum_evidence: 1,
      max_fallbacks: 0,
      ...assignment,
      ...next
    }

    update({
      ...value,
      model_assignments: assignment
        ? value.model_assignments.map(row => (row.agent_id === agentId ? item : row))
        : [...value.model_assignments, item]
    })
  }

  return (
    <>
      <p>{copy.assignmentNote}</p>
      <p>{copy.rootNote}</p>
      <p>
        {copy.agent}: <bdi>{agentId}</bdi>
      </p>
      {managed && <p>{copy.managed}</p>}
      <fieldset disabled={managed}>
        <label>
          {copy.mode}
          <select
            aria-label={copy.mode}
            className={controlVariants()}
            onChange={event => {
              if (event.target.value === 'legacy') {
                update({
                  ...value,
                  model_assignments: value.model_assignments.filter(item => item.agent_id !== agentId)
                })
              } else {
                change({ mode: event.target.value as 'fixed' | 'auto' })
              }
            }}
            value={assignment?.mode ?? 'legacy'}
          >
            {agentId !== 'executive' && <option value="legacy">{copy.legacy}</option>}
            <option value="fixed">{copy.fixed}</option>
            {agentId !== 'executive' && <option value="auto">{copy.auto}</option>}
          </select>
        </label>
        {assignment && (
          <>
            <fieldset className="eid-policy-choices">
              <legend>{copy.selectRoutes}</legend>
              {!descriptor.configuration.model_routes.length && <p>{copy.noRoutes}</p>}
              {descriptor.configuration.model_routes.map(route => (
                <label className="eid-policy-check" key={route.id}>
                  <Checkbox
                    checked={assignment.route_ids.includes(route.id)}
                    disabled={!assignment.route_ids.includes(route.id) && assignment.route_ids.length >= 16}
                    onCheckedChange={checked =>
                      change({
                        route_ids: checked
                          ? [...assignment.route_ids, route.id]
                          : assignment.route_ids.filter(id => id !== route.id)
                      })
                    }
                  />
                  <span>
                    <bdi>{route.id}</bdi> ·{' '}
                    <bdi>
                      {route.provider} / {route.model}
                    </bdi>
                  </span>
                </label>
              ))}
            </fieldset>
            <p>{copy.routeIds}</p>
            <ol className="eid-policy-route-order">
              {assignment.route_ids.map((id, index) => (
                <li key={id}>
                  <bdi>{id}</bdi>
                  <span>
                    <Button
                      aria-label={`${copy.moveUp} ${id}`}
                      disabled={index === 0}
                      onClick={() => {
                        const next = [...assignment.route_ids]

                        ;[next[index - 1], next[index]] = [next[index], next[index - 1]]
                        change({ route_ids: next })
                      }}
                      size="sm"
                      type="button"
                      variant="ghost"
                    >
                      ↑ {copy.moveUp}
                    </Button>
                    <Button
                      aria-label={`${copy.moveDown} ${id}`}
                      disabled={index === assignment.route_ids.length - 1}
                      onClick={() => {
                        const next = [...assignment.route_ids]

                        ;[next[index], next[index + 1]] = [next[index + 1], next[index]]
                        change({ route_ids: next })
                      }}
                      size="sm"
                      type="button"
                      variant="ghost"
                    >
                      ↓ {copy.moveDown}
                    </Button>
                  </span>
                </li>
              ))}
            </ol>
            <label>
              {copy.minimumEvidence}
              <Input
                max={3}
                min={1}
                onChange={event => change({ minimum_evidence: event.target.valueAsNumber })}
                required
                step={1}
                type="number"
                value={numberValue(assignment.minimum_evidence)}
              />
            </label>
            <label>
              {copy.fallbacks}
              <Input
                max={Math.min(2, Math.max(0, assignment.route_ids.length - 1))}
                min={0}
                onChange={event => change({ max_fallbacks: event.target.valueAsNumber })}
                required
                step={1}
                type="number"
                value={numberValue(assignment.max_fallbacks)}
              />
            </label>
          </>
        )}
      </fieldset>
    </>
  )
}

export function BudgetFields({ value, update, descriptor }: PolicyFieldsProps<OwnerBudgetConfiguration>) {
  const { locale } = useI18n()
  const copy = ownerPolicyCopy[locale]
  const managed = (field: string) => descriptor.managedFields.includes(field)

  const numeric = (field: BudgetNumber) => (
    <label key={field}>
      {copy[field]}
      {managed(field) && <small>{copy.managed}</small>}
      <Input
        disabled={managed(field)}
        max={budgetNumbers[field][1]}
        min={budgetNumbers[field][0]}
        onChange={event => update({ ...value, [field]: event.target.valueAsNumber })}
        required
        step={1}
        type="number"
        value={numberValue(value[field])}
      />
    </label>
  )

  const money = (field: 'max_cost_usd' | 'completion_reserve_cost_usd') => (
    <label>
      {copy[field]}
      {managed(field) && <small>{copy.managed}</small>}
      <Input
        disabled={managed(field)}
        inputMode="decimal"
        onChange={event => update({ ...value, [field]: event.target.value || null })}
        pattern="(?:(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,9})?|-0(?:\.0{1,9})?)"
        value={value[field] ?? ''}
      />
    </label>
  )

  return (
    <>
      <p>{copy.budgetNote}</p>
      <p>{copy.separateNote}</p>
      <section aria-label={copy.allowance}>
        <h3>{copy.allowance}</h3>
        <div className="eid-policy-fields">
          {(['max_model_calls', 'max_total_tokens', 'objective_timeout_seconds'] as const).map(numeric)}
          {money('max_cost_usd')}
        </div>
      </section>
      <details>
        <summary>
          {copy.context} / {copy.output}
        </summary>
        <div className="eid-policy-fields">{(['max_context_tokens', 'max_output_tokens'] as const).map(numeric)}</div>
      </details>
      <section aria-label={copy.reserve}>
        <h3>{copy.reserve}</h3>
        <div className="eid-policy-fields">
          {(['completion_reserve_model_calls', 'completion_reserve_tokens'] as const).map(numeric)}
          {money('completion_reserve_cost_usd')}
        </div>
      </section>
      <section aria-label={copy.pacing}>
        <h3>{copy.pacing}</h3>
        <label>
          {copy.adaptive_pacing_enabled}
          <select
            aria-label={copy.adaptive_pacing_enabled}
            className={controlVariants()}
            disabled={managed('adaptive_pacing_enabled')}
            onChange={event => update({ ...value, adaptive_pacing_enabled: event.target.value === 'true' })}
            value={String(value.adaptive_pacing_enabled)}
          >
            <option value="false">{copy.disabled}</option>
            <option value="true">{copy.enabled}</option>
          </select>
        </label>
        <div className="eid-policy-fields">
          {(
            [
              'pacing_conserve_below_percent',
              'pacing_critical_below_percent',
              'pacing_conserve_interval_seconds',
              'pacing_critical_interval_seconds',
              'pacing_conserve_max_inflight',
              'pacing_critical_max_inflight'
            ] as const
          ).map(numeric)}
        </div>
      </section>
      <section aria-label={copy.prices}>
        <h3>{copy.prices}</h3>
        {managed('model_costs') && <p>{copy.managed}</p>}
        <fieldset disabled={managed('model_costs')}>
          {value.model_costs.map((price, index) => (
            <div className="eid-policy-row" key={index}>
              <div className="eid-policy-fields">
                {(['provider', 'model', 'input_usd_per_million', 'output_usd_per_million'] as const).map(field => (
                  <label key={field}>
                    {
                      copy[
                        field === 'input_usd_per_million'
                          ? 'inputPrice'
                          : field === 'output_usd_per_million'
                            ? 'outputPrice'
                            : field
                      ]
                    }
                    <Input
                      inputMode={field.includes('usd') ? 'decimal' : 'text'}
                      maxLength={300}
                      onChange={event =>
                        update({
                          ...value,
                          model_costs: value.model_costs.map((item, i) =>
                            i === index ? { ...item, [field]: event.target.value } : item
                          )
                        })
                      }
                      required
                      value={price[field]}
                    />
                  </label>
                ))}
              </div>
              <Button
                onClick={() => update({ ...value, model_costs: value.model_costs.filter((_, i) => i !== index) })}
                type="button"
                variant="ghost"
              >
                {copy.remove} {index + 1}
              </Button>
            </div>
          ))}
          <Button
            disabled={value.model_costs.length >= 64}
            onClick={() =>
              update({
                ...value,
                model_costs: [
                  ...value.model_costs,
                  { provider: '', model: '', input_usd_per_million: '', output_usd_per_million: '' }
                ]
              })
            }
            type="button"
            variant="secondary"
          >
            {copy.addPrice}
          </Button>
        </fieldset>
      </section>
    </>
  )
}

export function ObservationFields({
  value,
  update
}: {
  value: OwnerBudgetObservation
  update: (value: OwnerBudgetObservation) => void
}) {
  const { locale } = useI18n()
  const copy = ownerPolicyCopy[locale]

  return (
    <>
      <p>{copy.observationNote}</p>
      <p>
        {copy.objective}: <bdi>{value.objectiveId}</bdi> · {copy.observationRevision}: {value.expectedRevision}
      </p>
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
    </>
  )
}

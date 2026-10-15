import { controlVariants } from '@/components/ui/control'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n/context'
import { outcomeContractCopy } from '@/i18n/organization-outcome-contracts'

import type { OutcomeCriterionInput, OutcomeKind } from './runtime-outcome-contract-types'

export function outcomeCriteria(lines: string[], definitions?: (string | OutcomeCriterionInput)[]): (string | OutcomeCriterionInput)[] {
  // Typed rows own their text and identity; bulk line edits cannot remap obligations.
  if (definitions?.some(c => typeof c !== 'string')) {return definitions.map(c => typeof c === 'string' ? c : { ...c })}

  return lines.map((text, i) => {
    const existing = definitions?.[i]

    return existing && typeof existing !== 'string' ? { ...existing, text } : text
  })
}

export function OutcomeCriteriaEditor({ text, definitions, onChange, disabled }: {
  text: string
  definitions?: (string | OutcomeCriterionInput)[]
  onChange(value: (string | OutcomeCriterionInput)[]): void
  disabled: boolean
}) {
  const { locale } = useI18n()
  const copy = outcomeContractCopy[locale]
  const values = outcomeCriteria(text.split('\n').map(s => s.trim()).filter(Boolean), definitions)

  if (!values.length) {return null}

  const update = (index: number, kind: OutcomeKind, checkKind?: OutcomeCriterionInput['checkKind']) => {
    const old = values[index]
    const projectId = typeof old !== 'string' ? old.projectId : undefined

    const next: OutcomeCriterionInput = { id: typeof old === 'string' ? `criterion-${index + 1}` : old.id,
      text: typeof old === 'string' ? old : old.text, kind, ...(kind === 'authorized_check' ? { checkKind: checkKind ?? 'managed_validation' } : {}),
      ...(projectId && (kind === 'source_integration' || (kind === 'authorized_check' && checkKind && checkKind !== 'managed_validation')) ? { projectId } : {}) }

    onChange(values.map((value, i) => i === index ? next : value))
  }

  const typed = values.some(v => typeof v !== 'string')

  return <fieldset disabled={disabled}><legend>{copy.heading}</legend>{values.map((value, i) => {
    const item = typeof value === 'string' ? { text: value, kind: 'deliverable' as const } : value

    return <div className="eid-form-field" key={i}>{typed && <label>{copy.heading} {i + 1}<Textarea maxLength={2000} onChange={e => onChange(values.map((v, j) => j !== i ? v : typeof v === 'string' ? e.target.value : { ...v, text: e.target.value }))} value={item.text} /></label>}<label>{item.text}<select className={controlVariants()} onChange={e => update(i, e.target.value as OutcomeKind)} value={item.kind}>
      {(['deliverable', 'authorized_check', 'source_integration', 'human_judgment'] as const).map(kind => <option key={kind} value={kind}>{copy[kind]}</option>)}
    </select></label>{item.kind === 'authorized_check' && <label>{copy.checks}<select className={controlVariants()} onChange={e => update(i, item.kind, e.target.value as OutcomeCriterionInput['checkKind'])} value={item.checkKind}>
      <option value="managed_validation">{copy.managed_validation}</option><option value="project_tests">{copy.project_tests}</option>
    </select></label>}</div>
  })}<p className="eid-note">{copy.note}</p></fieldset>
}

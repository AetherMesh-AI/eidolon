import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

import type { Objective, ObjectiveMetadata } from './types'

const priorities = { low: 'Low', normal: 'Normal', high: 'High', P5: 'Lowest', P4: 'Low', P3: 'Normal', P2: 'High', P1: 'Highest' }
export const autonomyNote = 'Local planning intent only; this does not enforce permissions, grant approval, or dispatch work. Retained locally in this desktop.'

export function MetadataFields({ value, onChange }: { value: ObjectiveMetadata; onChange: (value: ObjectiveMetadata) => void }) {
  return <fieldset className="eid-metadata-fields"><legend>Local planning metadata</legend>
    {([['description', 'Summary'], ['phase', 'Current phase'], ['milestone', 'Recent milestone'], ['autonomyIntent', 'Autonomy intent']] as const).map(([key, label]) => <label key={key}>{label}<Input maxLength={4000} onChange={event => onChange({ ...value, [key]: event.target.value })} value={value[key] ?? ''} /></label>)}
    <label className="eid-filter">Priority<select onChange={event => onChange({ ...value, priority: event.target.value as ObjectiveMetadata['priority'] || undefined })} value={value.priority ?? ''}><option value="">Not set</option>{Object.entries(priorities).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
    <label>Local progress (%)<Input max={100} min={0} onChange={event => onChange({ ...value, progress: event.target.value === '' ? undefined : Number(event.target.value) })} step="any" type="number" value={value.progress ?? ''} /></label>
    <p className="eid-note">Progress is a user-entered local estimate, not measured completion. Leave blank if unknown.</p><p className="eid-note">{autonomyNote}</p>
  </fieldset>
}

export function MetadataSummary({ objective }: { objective: Objective }) {
  return <><dl><dt>Priority</dt><dd>{objective.priority ? priorities[objective.priority] : 'Not set'}</dd><dt>Progress</dt><dd>{objective.progress === undefined ? 'Unknown' : `${objective.progress}% · local estimate`}</dd><dt>Current phase</dt><dd>{objective.phase || 'Not recorded'}</dd><dt>Recent milestone</dt><dd>{objective.milestone || 'Not recorded'}</dd><dt>Autonomy intent</dt><dd>{objective.autonomyIntent || 'Not specified'}</dd><dt>Created locally</dt><dd><time dateTime={objective.createdAt}>{new Date(objective.createdAt).toLocaleString()}</time></dd></dl><p className="eid-note">{autonomyNote}</p></>
}

export function MetadataEditor({ objective, onSave }: { objective: Objective; onSave: (value: ObjectiveMetadata) => void }) {
  const [value, setValue] = useState<ObjectiveMetadata>({ description: objective.description, priority: objective.priority, progress: objective.progress, phase: objective.phase, milestone: objective.milestone, autonomyIntent: objective.autonomyIntent })
  const [message, setMessage] = useState('')

  return <form onSubmit={event => { event.preventDefault();

 try { onSave(value); setMessage('Saved locally. No runtime permissions changed.') } catch (error) { setMessage(error instanceof Error ? error.message : 'Could not save metadata.') } }}><MetadataFields onChange={next => { setValue(next); setMessage('') }} value={value} /><Button type="submit" variant="secondary">Save local metadata</Button>{message && <p role="status">{message}</p>}</form>
}

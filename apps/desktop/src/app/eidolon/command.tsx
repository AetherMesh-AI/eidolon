import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { Button } from '@/components/ui/button'

import type { ObjectiveMetadata, OrganizationAdapter, OrganizationSnapshot } from './types'

export function Command({ adapter, snapshot }: { adapter: OrganizationAdapter; snapshot: OrganizationSnapshot }) {
  const [goal, setGoal] = useState('')
  const [metadata, setMetadata] = useState<ObjectiveMetadata>({})
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const active = useRef(true)
  const sending = useRef(false)
  const intent = useRef({ key: crypto.randomUUID(), signature: '' })
  const navigate = useNavigate()
  const runtime = adapter.mode === 'runtime'
  const unavailable = runtime && snapshot.connection && snapshot.connection.state !== 'ready'

  // eslint-disable-next-line no-restricted-syntax -- component lifetime guard, not a mirrored atom
  useEffect(() => { active.current = true;

 return () => { active.current = false } }, [])

  const submit = () => {
    if (sending.current) {return}
    setError('')

    if (adapter.mode === 'prototype') {
      try { const objective = adapter.createObjective(goal, metadata); navigate(`/objectives/${objective.id}`) }
      catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not create objective.') }

      return
    }

    const signature = JSON.stringify([goal.trim(), metadata])

    if (intent.current.signature !== signature) {intent.current = { key: crypto.randomUUID(), signature }}
    sending.current = true
    setSubmitting(true)
    void adapter.createObjective(goal, metadata, intent.current.key).then(objective => {
      if (active.current) {navigate(`/objectives/${objective.id}`)}
    }).catch(reason => {
      if (active.current) {setError(reason instanceof Error ? reason.message : 'Could not submit objective. Retry to check the same request safely.')}
    }).finally(() => {
      sending.current = false

      if (active.current) {setSubmitting(false)}
    })
  }

  return <div className="eid-command">
    <div aria-hidden="true" className="eid-mark">◈</div><p className="eid-eyebrow">Eidolon</p>
    <h1>What should the organization do?</h1>
    <p className="eid-subtitle">{runtime ? 'Draft or analyze the context you provide. Follow planning, work and independent review.' : 'Set the direction. Keep the work in view.'}</p>
    <form aria-busy={submitting} className="eid-composer" onSubmit={event => { event.preventDefault(); submit() }}>
      <label className="sr-only" htmlFor="eid-objective">Objective</label>
      <textarea disabled={submitting} id="eid-objective" maxLength={runtime ? 500 : 4000} onChange={event => { setGoal(event.target.value); setError('') }} placeholder={runtime ? 'Describe a writing or analysis goal and include its context…' : 'Describe an objective…'} rows={3} value={goal} />
      {runtime && <><label htmlFor="eid-context">Submitted context (optional)</label><textarea disabled={submitting} id="eid-context" maxLength={12000} onChange={event => setMetadata({ ...metadata, description: event.target.value })} placeholder="Paste the material to use. Files, websites and external tools are not accessed by this workflow." rows={3} value={metadata.description ?? ''} /></>}
      <div className="eid-composer-tools"><label><input checked={metadata.priority !== undefined} disabled={submitting} onChange={event => setMetadata({ ...metadata, priority: event.target.checked ? 'P3' : undefined })} type="checkbox" /> Priority</label>{!runtime && <label>Agent <select aria-label="Agent" onChange={event => setMetadata({ ...metadata, agentId: event.target.value || undefined })} value={metadata.agentId ?? ''}><option value="">Optional</option>{snapshot.agents.map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>}</div>
      {metadata.priority !== undefined && <label className="eid-priority">Priority level <input aria-label="Priority level" aria-valuetext={['Lowest', 'Low', 'Normal', 'High', 'Highest'][['P5', 'P4', 'P3', 'P2', 'P1'].indexOf(metadata.priority)]} disabled={submitting} max={4} min={0} onChange={event => setMetadata({ ...metadata, priority: (['P5', 'P4', 'P3', 'P2', 'P1'] as const)[Number(event.target.value)] })} step={1} type="range" value={['P5', 'P4', 'P3', 'P2', 'P1'].indexOf(metadata.priority)} /><span aria-hidden="true" className="eid-priority-endpoints"><span>Lowest</span><span>Highest</span></span></label>}
      <div className="eid-composer-tools"><span>{runtime ? 'Current-profile provider · Submitted context only' : 'Objective · Local prototype'}</span><Button disabled={!goal.trim() || submitting || Boolean(unavailable)} type="submit">{submitting ? 'Submitting objective…' : 'Create objective'} <span aria-hidden="true">↑</span></Button></div>
    </form>
    {error && <p role="alert">{error}</p>}
    <div className="eid-inline"><Link to="/">Ask a question</Link><span>Uses the connected Eidolon session</span></div>
    <p className="eid-note">{runtime ? 'Uses your current profile’s configured model provider. Unsupported work, missing setup and failed review remain visible as pending intervention. No external tools are executed.' : 'Objectives are retained locally in this desktop. No autonomous work is dispatched.'}</p>
  </div>
}

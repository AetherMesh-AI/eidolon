import { useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useI18n } from '@/i18n/context'
import { goalCopy } from '@/i18n/organization-goals'

import { createGoalClient, type GoalAction, type GoalLimits, type GoalTemplate, type GoalView } from './organization-goal-client'
import { organizationMetadataClient, readOrganizationMetadataScope, useOrganizationMetadata } from './use-organization-metadata'

function source() {
  const scope = readOrganizationMetadataScope()
  const metadata = organizationMetadataClient.getSnapshot().descriptor?.metadata

  return { ...scope, organizationId: metadata?.organizationId ?? null, ownerId: metadata?.owner.ownerId ?? null }
}

const defaults = (): GoalLimits => ({ modelCalls: 20, tokens: 100000, costUsd: null, stages: 40, projectRuns: 5, deadlineTimestamp: Math.floor(Date.now() / 1000) + 86400, completionReserve: { modelCalls: 2, tokens: 10000, costUsd: null } })

export function OrganizationGoals() {
  const { locale } = useI18n(); const copy = goalCopy[locale]
  const metadata = useOrganizationMetadata()
  const client = useRef(createGoalClient(source)).current
  const [view, setView] = useState<GoalView | null>(null)
  const [goalId, setGoalId] = useState('')
  const [objectives, setObjectives] = useState<string[]>([])
  const [limits, setLimits] = useState(defaults)
  const [templates, setTemplates] = useState<GoalTemplate[]>([])
  const [title, setTitle] = useState(''); const [description, setDescription] = useState(''); const [criteria, setCriteria] = useState('')
  const [kind, setKind] = useState('deliverable')
  const [projects, setProjects] = useState<string[]>([])
  const [executive, setExecutive] = useState('executive'); const [manager, setManager] = useState('manager')
  const [previewHash, setPreviewHash] = useState<string | null>(null)
  const [confirmed, setConfirmed] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState('')
  const [, renderPending] = useState(0)
  const action = useRef<{ method: GoalAction; args: Record<string, unknown> } | null>(null)
  const inFlight = useRef(false)
  const current = !view || client.current()
  const locked = busy || client.pending() || !current || !metadata.scope.eligible
  const goal = view?.goals.find(g => g.id === goalId)
  const plan = view?.plans.find(p => p.goalId === goalId)
  const draft = () => ({ goalId, templates, deadlineTimestamp: limits.deadlineTimestamp })

  const changed = () => { setConfirmed(false); setPreviewHash(null) }

  const run = async (fn: () => Promise<void>) => {
    if (inFlight.current) { return }
    inFlight.current = true; setBusy(true); setError('')

    try { await fn() } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { inFlight.current = false; setBusy(false); renderPending(n => n + 1) }
  }

  const write = async (method: GoalAction, args: Record<string, unknown>) => {
    action.current = { method, args: structuredClone(args) }
    await client.write(method, args, confirmed)
    action.current = null; setConfirmed(false); setPreviewHash(null); setView(await client.read())
  }

  const select = (id: string) => {
    const found = view?.goals.find(g => g.id === id)
    setGoalId(id); setObjectives(found?.objectiveIds ?? []); setLimits(found ? structuredClone(found.limits) : defaults()); setTemplates([]); changed()
  }

  const toggle = (values: string[], id: string) => values.includes(id) ? values.filter(v => v !== id) : [...values, id]

  const add = () => {
    if (!title.trim() || !description.trim() || !criteria.trim() || templates.length >= 32) { return }
    const id = `step-${templates.length + 1}`
    setTemplates([...templates, { id, title: title.trim(), description: description.trim(), priority: 'normal', acceptanceCriteria: criteria.split('\n').map(s => s.trim()).filter(Boolean).map((text,i) => ({ id: `criterion-${i + 1}`, text, kind })), deliveryMode: projects.length ? 'source_project' : 'managed_artifact', requiredChecks: [], projectIds: [...projects], executiveId: executive, managerId: manager, dependencyIds: [], templateDependencyIds: templates.length ? [templates[templates.length - 1].id] : [] }])
    setTitle(''); setDescription(''); setCriteria(''); changed()
  }

  return <details className="eid-roster-details">
    <summary>{copy.heading}</summary>
    <p>{copy.note}</p>
    <Button disabled={busy || client.pending() || !metadata.scope.eligible} onClick={() => void run(async () => { const next = await client.read(); setView(next); setGoalId(''); setObjectives([]); setLimits(defaults()); setTemplates([]); setTitle(''); setDescription(''); setCriteria(''); setKind('deliverable'); setProjects([]); setExecutive('executive'); setManager('manager'); changed() })}>{copy.load}</Button>
    {error && <p role="alert">{error}</p>}
    {client.pending() && <div>
      <Button disabled={busy} onClick={() => void run(async () => { if (await client.reconcile()) { action.current = null; setView(await client.read()); setConfirmed(false) } })}>{copy.reconcile}</Button>
      <Button disabled={busy || !client.current() || !action.current} onClick={() => void run(async () => { const pending = action.current!; await write(pending.method, pending.args) })}>{copy.retry}</Button>
    </div>}
    {view && <fieldset className="grid gap-3" disabled={locked}>
      <label>{copy.goal}<Input list="organization-goal-ids" onChange={e => select(e.target.value)} value={goalId} /></label>
      <datalist id="organization-goal-ids">{view.goals.map(g => <option key={g.id} value={g.id} />)}</datalist>
      <fieldset><legend>{copy.objectives}</legend><div className="max-h-40 overflow-auto">{view.objectives.map(o => <label className="block" key={o.id}><input checked={objectives.includes(o.id)} disabled={goal?.objectiveIds.includes(o.id)} onChange={() => { setObjectives(toggle(objectives, o.id)); changed() }} type="checkbox" />{o.title}</label>)}</div></fieldset>
      <div className="grid grid-cols-2 gap-2">{(['modelCalls','tokens','stages','projectRuns'] as const).map(key => <label key={key}>{copy[{ modelCalls: 'calls', tokens: 'tokens', stages: 'stages', projectRuns: 'runs' }[key] as 'calls']}<Input min={key === 'projectRuns' ? 0 : 1} onChange={e => { setLimits({ ...limits, [key]: Number(e.target.value) }); changed() }} type="number" value={limits[key]} /></label>)}</div>
      <label>{copy.cost}<Input onChange={e => { const costUsd = e.target.value || null; setLimits({ ...limits, costUsd, completionReserve: { ...limits.completionReserve, costUsd: costUsd ? limits.completionReserve.costUsd ?? '0' : null } }); changed() }} value={limits.costUsd ?? ''} /></label>
      <fieldset><legend>{copy.reserve}</legend>{(['modelCalls','tokens'] as const).map(key => <label key={key}>{key === 'modelCalls' ? copy.calls : copy.tokens}<Input max={limits[key]} min={0} onChange={e => { setLimits({ ...limits, completionReserve: { ...limits.completionReserve, [key]: Number(e.target.value) } }); changed() }} type="number" value={limits.completionReserve[key]} /></label>)}{limits.costUsd !== null && <label>{copy.cost}<Input onChange={e => { setLimits({ ...limits, completionReserve: { ...limits.completionReserve, costUsd: e.target.value } }); changed() }} value={limits.completionReserve.costUsd ?? '0'} /></label>}</fieldset>
      <label>{copy.deadline}<Input onChange={e => { const value = new Date(e.target.value).getTime();

 if (Number.isFinite(value)) { setLimits({ ...limits, deadlineTimestamp: Math.floor(value / 1000) }); changed() } }} type="datetime-local" value={new Date(limits.deadlineTimestamp * 1000 - new Date(limits.deadlineTimestamp * 1000).getTimezoneOffset() * 60000).toISOString().slice(0,16)} /></label>
      {goal && <p>{copy.reserved}: {goal.reserved.modelCalls ?? '?'} / {goal.limits.modelCalls} · {goal.reserved.tokens ?? '?'} / {goal.limits.tokens} · USD {goal.reserved.costUsd ?? '?'}</p>}
      <label><input checked={confirmed} onChange={e => setConfirmed(e.target.checked)} type="checkbox" />{copy.confirm}</label>
      <Button disabled={!confirmed || !goalId || !objectives.length} onClick={() => void run(() => write('organization.configureGoal', { goalId, objectiveIds: objectives, limits, paused: goal?.paused ?? false, expectedRevision: goal?.revision ?? 0 }))}>{copy.save}</Button>
      {goal && !plan && <div className="grid gap-2">
        <p>{copy.sequential}</p>
        <label>{copy.title}<Input onChange={e => { setTitle(e.target.value); changed() }} value={title} /></label>
        <label>{copy.scope}<textarea onChange={e => { setDescription(e.target.value); changed() }} value={description} /></label>
        <label>{copy.criteria}<textarea onChange={e => { setCriteria(e.target.value); changed() }} value={criteria} /></label>
        <label>{copy.kind}<select onChange={e => { setKind(e.target.value); changed() }} value={kind}><option value="deliverable">deliverable</option><option value="human_judgment">human_judgment</option><option value="source_integration">source_integration</option></select></label>
        <fieldset><legend>{copy.projects}</legend>{view.projectIds.map(id => <label key={id}><input checked={projects.includes(id)} onChange={() => { setProjects(toggle(projects, id)); changed() }} type="checkbox" />{id}</label>)}</fieldset>
        <label>{copy.executive}<select onChange={e => { setExecutive(e.target.value); changed() }} value={executive}>{view.leaders.filter(l => l.role === 'Executive').map(l => <option key={l.id} value={l.id}>{l.name}</option>)}</select></label>
        <label>{copy.manager}<select onChange={e => { setManager(e.target.value); changed() }} value={manager}>{view.leaders.filter(l => l.role === 'Manager' && l.manager_id === executive).map(l => <option key={l.id} value={l.id}>{l.name}</option>)}</select></label>
        <Button disabled={templates.length >= 32 || !title.trim() || !description.trim() || !criteria.trim()} onClick={add}>{copy.add}</Button>
        <Button disabled={!templates.length} onClick={() => { setTemplates(templates.slice(0,-1)); changed() }}>{copy.remove}</Button>
        <ol>{templates.map(t => <li key={t.id}><strong>{t.title}</strong><p>{t.description}</p><ul>{t.acceptanceCriteria.map((c,i) => <li key={i}>{typeof c === 'object' && c !== null ? String((c as { text?: string }).text) : String(c)}</li>)}</ul><p>{t.projectIds.join(', ')} · {t.executiveId} / {t.managerId}</p></li>)}</ol>
        <Button disabled={!templates.length} onClick={() => void run(async () => { const preview = await client.preview(draft()); setPreviewHash(preview.sha256 as string); setConfirmed(false) })}>{copy.preview}</Button>
        {previewHash && <Button disabled={!confirmed} onClick={() => void run(() => write('organization.approveGoalContinuation', { ...draft(), previewSha256: previewHash }))}>{copy.approve}</Button>}
      </div>}
      {plan && <div><p>{copy.launched}: {plan.launches.length} / {plan.templates.length}</p><ul>{plan.templateStates.map(s => <li key={s.id}>{s.id}: {s.blockers.join(', ')}</li>)}</ul>{plan.waitingReason && <p role="status">{plan.waitingReason}</p>}
        <Button disabled={!confirmed || (!plan.enabled && !plan.canEnable)} onClick={() => void run(() => write('organization.setGoalContinuationEnabled', { goalId, enabled: !plan.enabled, expectedRevision: plan.controlRevision }))}>{plan.enabled ? copy.disable : copy.enable}</Button>
      </div>}
    </fieldset>}
  </details>
}

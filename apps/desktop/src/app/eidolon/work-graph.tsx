import { useState } from 'react'

import { AgentAvatar } from './avatar'
import { Inspector } from './inspector'
import type { OrganizationSnapshot } from './types'

export function WorkGraph({ snapshot, objectiveId }: { snapshot: OrganizationSnapshot; objectiveId: string }) {
 const tasks = snapshot.tasks.filter(item => item.objectiveId === objectiveId)
 const [selected, setSelected] = useState<string | null>(null)
 const [view, setView] = useState('Graph')
 const [zoom, setZoom] = useState(1)

 const depth = (id: string, seen = new Set<string>()): number => {
  if (seen.has(id)) {return 0}
  const item = tasks.find(candidate => candidate.id === id)

  if (!item?.dependsOn.length) {return 0}

  return 1 + Math.max(...item.dependsOn.map(dependency => depth(dependency, new Set([...seen, id]))))
 }

 const levels = tasks.map(item => depth(item.id))
 const position = (index: number) => ({ x: levels[index] * 290 + 24, y: tasks.slice(0, index).filter(item => depth(item.id) === levels[index]).length * 140 + 24 })
 const width = (Math.max(0, ...levels) + 1) * 290 + 24
 const height = Math.max(1, ...levels.map(level => levels.filter(item => item === level).length)) * 140 + 24
 const task = tasks.find(item => item.id === selected)
 const agentName = (id: string) => snapshot.agents.find(item => item.id === id)?.name || id

 return <><div className="eid-toolbar"><h2>Work graph</h2><div className="eid-tabs">{['Graph', 'List'].map(mode => <button aria-pressed={view === mode} key={mode} onClick={() => setView(mode)}>{mode}</button>)}</div></div>
 {tasks.length ? <><div className="eid-toolbar"><span>Scroll to pan · Select a task to inspect</span><button aria-label="Zoom out" onClick={() => setZoom(value => Math.max(0.5, value - 0.1))}>−</button><button onClick={() => setZoom(1)}>Reset view</button><button aria-label="Zoom in" onClick={() => setZoom(value => Math.min(1.5, value + 0.1))}>+</button></div><div className="eid-graph-scroll"><div style={view === 'Graph' ? { position: 'relative', width: width * zoom, height: height * zoom } : undefined}><div style={view === 'Graph' ? { position: 'relative', width, height, transform: `scale(${zoom})`, transformOrigin: 'top left' } : undefined}>{view === 'Graph' && <svg aria-label="Task dependency connections" height={height} style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }} width={width}><defs><marker id="eid-arrow" markerHeight="6" markerWidth="6" orient="auto" refX="9" refY="5" viewBox="0 0 10 10"><path d="M 0 0 L 10 5 L 0 10 z" fill="var(--eidolon-border-strong)" /></marker></defs>{tasks.flatMap((item, index) => item.dependsOn.map(id => { const source = tasks.findIndex(candidate => candidate.id === id);

 if (source < 0) {return null;} const a = position(source); const b = position(index);

 return <path d={`M ${a.x + 248} ${a.y + 48} C ${a.x + 270} ${a.y + 48}, ${b.x - 22} ${b.y + 48}, ${b.x} ${b.y + 48}`} fill="none" key={`${id}-${item.id}`} markerEnd="url(#eid-arrow)" stroke="var(--eidolon-border-strong)" strokeWidth="1.5" /> }))}</svg>}<ol className="eid-list" style={view === 'Graph' ? { margin: 0, padding: 0, listStyle: 'none' } : undefined}>{tasks.map((item, index) => <li key={item.id} style={view === 'Graph' ? { position: 'absolute', left: position(index).x, top: position(index).y, width: 248 } : undefined}><button aria-label={`Inspect task: ${item.title}`} className={`eid-row eid-task-${item.status}`} onClick={() => setSelected(item.id)} style={{ width: '100%', minHeight: 96, textAlign: 'left' }}><AgentAvatar name={agentName(item.ownerId)} /><span><small>{agentName(item.ownerId)}</small><strong>{item.title}</strong><small>Depends on: {item.dependsOn.map(id => tasks.find(dependency => dependency.id === id)?.title || id).join(', ') || 'No prerequisites'}</small></span><span className="eid-status">{item.status}</span></button></li>)}</ol></div></div></div></> : <p>No tasks dispatched. A real execution adapter must provide task state before it is shown as working.</p>}
 {task && <Inspector kind="task" onClose={() => setSelected(null)} title={task.title}><p className="eid-eyebrow">Prototype task · Not dispatched</p><dl><dt>Owner</dt><dd>{agentName(task.ownerId)}</dd><dt>Assigned by</dt><dd>{task.assignedById ? agentName(task.assignedById) : 'Unknown · Not recorded'}</dd><dt>Status</dt><dd>{task.status}</dd><dt>Priority</dt><dd>{task.priority || 'Unknown · Not recorded'}</dd><dt>Review</dt><dd>{task.review || 'Unknown · Not recorded'}</dd><dt>Dependency completion</dt><dd>{task.dependsOn.filter(id => tasks.find(item => item.id === id)?.status === 'completed').length} / {task.dependsOn.length} complete</dd><dt>Inputs</dt><dd>{task.inputs?.join(', ') || 'Unavailable · No execution data'}</dd><dt>Results</dt><dd>{task.results?.join(', ') || 'Unavailable · No execution data'}</dd><dt>Dependencies</dt><dd>{task.dependsOn.map(id => tasks.find(item => item.id === id)?.title || id).join(', ') || 'None'}</dd></dl><h3>Runtime identity</h3><p>No runtime session linked</p><p>This graph illustrates fictional planning dependencies, not dispatched work. Listed example inputs or results are not runtime artifacts.</p></Inspector>}
 </>
}

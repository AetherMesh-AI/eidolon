import { useState } from 'react'

import type { KnowledgeItem } from './types'

/** Spatial layout is illustrative. Edges only represent recorded shared objectives. */
export function MemoryWeb({ items }: { items: KnowledgeItem[] }) {
  const [angle, setAngle] = useState(0)
  const [zoom, setZoom] = useState(1)
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<string>()
  const visible = items.filter(item => `${item.title} ${item.body}`.toLowerCase().includes(query.toLowerCase()))

  const nodes = visible.slice(0, 150).map((item, index) => {
    const phase = index * 2.4
    const y = visible.length > 1 ? 1 - 2 * index / Math.min(visible.length - 1, 149) : 0
    const radius = Math.sqrt(Math.max(0, 1 - y * y))
    const x = Math.cos(phase + angle) * radius
    const z = Math.sin(phase + angle) * radius
    const perspective = 3 / (3 - z)

    return { item, x: 320 + x * 165 * zoom * perspective, y: 210 + y * 135 * zoom * perspective, z, radius: 5 + (z + 1) * 3 }
  })

  const detail = visible.find(item => item.id === selected)

  return <section aria-label="3D memory web">
    <p className="eid-note">Local retained context, not runtime-verified memory. Agent ownership unknown. Timestamps unknown; growth over time is not inferred. Spatial positions are illustrative, not semantic distance. Lines indicate recorded shared objectives only; cross-agent relationships are unknown.</p>
    <label className="eid-filter">Search memories<input onChange={event => setQuery(event.target.value)} type="search" value={query} /></label>
    <div className="eid-toolbar"><button className="eid-button" onClick={() => setAngle(angle - 0.3)}>Rotate left</button><button className="eid-button" onClick={() => setAngle(angle + 0.3)}>Rotate right</button><label>Zoom<input max="1.5" min="0.5" onChange={event => setZoom(Number(event.target.value))} step="0.1" type="range" value={zoom} /></label></div>
    <svg aria-label="Memory relationship web" onPointerDown={event => event.currentTarget.setPointerCapture(event.pointerId)} onPointerMove={event => { if (event.buttons === 1) {setAngle(value => value + event.movementX / 100)} }} role="img" style={{ width: '100%', maxHeight: 420, touchAction: 'none', background: 'var(--eid-bg, #111827)', borderRadius: 16 }} viewBox="0 0 640 420">
      {nodes.flatMap((node, index) => nodes.slice(index + 1).filter(other => node.item.objectiveId && other.item.objectiveId === node.item.objectiveId).map(other => <line key={`${node.item.id}-${other.item.id}`} opacity="0.35" stroke="#738da0" x1={node.x} x2={other.x} y1={node.y} y2={other.y} />))}
      {[...nodes].sort((a, b) => a.z - b.z).map(node => <circle cx={node.x} cy={node.y} fill={selected === node.item.id ? '#ffd580' : '#84c8cb'} key={node.item.id} onClick={() => setSelected(node.item.id)} opacity={0.6 + (node.z + 1) / 5} r={node.radius}><title>{node.item.title}</title></circle>)}
    </svg>
    <p>{visible.length} retained items · Showing up to 150 spatial nodes. Accessible list below.</p>
    <div className="eid-list" style={{ maxHeight: 260, overflow: 'auto' }}>{visible.slice(0, 150).map(item => <button className="eid-row" key={item.id} onClick={() => setSelected(item.id)}>{item.title}</button>)}</div>
    {!visible.length && <p>No matching retained memories. Add local outcomes or explicitly load fictional example data.</p>}
    {detail && <article className="eid-card"><h2>{detail.title}</h2><p>{detail.body}</p><p>Kind: {detail.kind} · Agent unknown · Timestamp unknown</p><button className="eid-button" onClick={() => setSelected(undefined)}>Close memory</button></article>}
  </section>
}

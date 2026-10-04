import type { Objective } from './types'

const priorities = { low: 'Low', normal: 'Normal', high: 'High', P5: 'Lowest', P4: 'Low', P3: 'Normal', P2: 'High', P1: 'Highest' }
const autonomyNote = 'Historical local planning intent only. This read-only record does not enforce permissions, grant approval, or dispatch work.'

export function MetadataSummary({ objective }: { objective: Objective }) {
  return <><dl><dt>Priority</dt><dd>{objective.priority ? priorities[objective.priority] : 'Not set'}</dd><dt>Progress</dt><dd>{objective.progress === undefined ? 'Unknown' : `${objective.progress}%${objective.source === 'runtime' ? ' · completed tasks' : ' · local estimate'}`}</dd><dt>Current phase</dt><dd>{objective.phase || 'Not recorded'}</dd><dt>Recent milestone</dt><dd>{objective.milestone || 'Not recorded'}</dd>{objective.source === 'prototype' && <><dt>Autonomy intent</dt><dd>{objective.autonomyIntent || 'Not specified'}</dd></>}<dt>{objective.source === 'runtime' ? 'Created' : 'Created locally'}</dt><dd><time dateTime={objective.createdAt}>{new Date(objective.createdAt).toLocaleString()}</time></dd></dl><p className="eid-note">{objective.source === 'runtime' ? 'Progress counts runtime-completed tasks. Completion requires the recorded review.' : autonomyNote}</p></>
}

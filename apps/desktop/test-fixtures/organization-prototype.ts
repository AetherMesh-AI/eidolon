import { demoSnapshot } from '../src/app/eidolon/demo'
import type { Objective, ObjectiveMetadata, ObjectiveStatus, OrganizationSnapshot, StaticOrganizationAdapter } from '../src/app/eidolon/types'

// Historical writable simulator retained solely as an isolated test fixture.
// No production module imports this file.
interface PrototypeOrganizationAdapter extends StaticOrganizationAdapter {
  createObjective(title: string, metadata?: ObjectiveMetadata): Objective
  updateObjectiveMetadata(id: string, metadata: ObjectiveMetadata): void
  setObjectiveStatus(id: string, status: ObjectiveStatus): void
  recordOutcome(id: string, summary: string): void
  resolveDecision(id: string, status: 'approved' | 'rejected'): void
  loadDemo(): void
  reset(): void
}

function validateMetadata(metadata: ObjectiveMetadata) {
  const { progress } = metadata

  if (progress !== undefined && (!Number.isFinite(progress) || progress < 0 || progress > 100)) {throw new Error('Progress must be between 0 and 100, or left unknown.')}
}

/** Deliberately has no gateway or profile-store dependency. Local UI intent is
 * not evidence of autonomous execution, and must not create canonical sessions. */
export function createPrototypeAdapter(storage?: Pick<Storage, 'getItem' | 'setItem'>): PrototypeOrganizationAdapter {
  const key = 'eidolon.organization.v1'
  let snapshot: OrganizationSnapshot = { objectives: [], agents: [], tasks: [], activity: [], knowledge: [] }

  try {
    const saved = storage?.getItem(key)

    if (saved) {
      const parsed = JSON.parse(saved) as OrganizationSnapshot

      if (!parsed || !['objectives', 'agents', 'tasks', 'activity', 'knowledge'].every(field => Array.isArray(parsed[field as keyof OrganizationSnapshot])) || (parsed.decisions !== undefined && !Array.isArray(parsed.decisions))) {throw new Error('Invalid local organization data')}
      snapshot = parsed
    }
  } catch {
    // Keep the original bytes until an explicit user mutation/reset. A corrupt
    // local prototype must not prevent the desktop or canonical chats opening.
    console.warn('Unable to load local organization data; opening an empty prototype. Saved data has not been overwritten.')
  }

  const listeners = new Set<() => void>()

  const publish = (next: OrganizationSnapshot) => {
    storage?.setItem(key, JSON.stringify(next))
    snapshot = next
    listeners.forEach(listener => listener())
  }

  return {
    mode: 'prototype',
    loadDemo() {
      const example = demoSnapshot()
      const merge = <T extends { id: string }>(current: T[], demo: T[]): T[] => [...current.filter(item => !demo.some(entry => entry.id === item.id)), ...demo]
      publish({ decisions: merge(snapshot.decisions || [], example.decisions || []), objectives: merge(snapshot.objectives, example.objectives), agents: merge(snapshot.agents, example.agents), tasks: merge(snapshot.tasks, example.tasks), activity: merge(snapshot.activity, example.activity), knowledge: merge(snapshot.knowledge, example.knowledge) })
    },
    resolveDecision(id, status) {
      const decision = snapshot.decisions?.find(item => item.id === id)

      if (!decision || decision.status !== 'pending') {return}
      publish({ ...snapshot, decisions: snapshot.decisions?.map(item => item.id === id ? { ...item, status } : item), activity: [{ id: crypto.randomUUID(), objectiveId: decision.objectiveId, kind: 'approval', text: `${decision.title}: ${status} in local prototype only. No runtime permission changed.`, timestamp: new Date().toISOString(), source: 'prototype' }, ...snapshot.activity] })
    },
    reset() { publish({ objectives: [], agents: [], tasks: [], activity: [], knowledge: [] }) },
    getSnapshot: () => snapshot,
    subscribe(listener) {
      listeners.add(listener)

      return () => { listeners.delete(listener) }
    },
    createObjective(input, metadata = {}) {
      validateMetadata(metadata)
      const title = input.trim()

      if (!title) {throw new Error('Describe an objective before submitting.')}
      const createdAt = new Date().toISOString()

      const objective: Objective = {
        id: crypto.randomUUID(), title, status: 'planning', source: 'prototype',
        createdAt, ownerId: 'lead', ...metadata, description: metadata.description?.trim() || title
      }

      const team = [
        { id: `${objective.id}:lead`, name: 'Alex Morgan', role: 'Executive', responsibilities: ['Coordinate the objective'], capabilities: ['Planning'], status: 'waiting' as const, summary: 'Simulated planning assignment', objectiveId: objective.id },
        { id: `${objective.id}:research`, name: 'Jordan Vale', role: 'Research', managerId: `${objective.id}:lead`, responsibilities: ['Clarify requirements'], capabilities: ['Research'], status: 'waiting' as const, summary: 'Simulated discovery assignment', objectiveId: objective.id },
        { id: `${objective.id}:delivery`, name: 'Casey Rowan', role: 'Engineering', managerId: `${objective.id}:lead`, responsibilities: ['Prepare and review a deliverable'], capabilities: ['Implementation', 'Review'], status: 'waiting' as const, summary: 'Simulated delivery assignment', objectiveId: objective.id },
      ]

      objective.ownerId = team[0].id

      const tasks = ['Clarify requirements', 'Prepare deliverable', 'Review outcome'].map((title, index) => ({
        id: `${objective.id}:task:${index}`, objectiveId: objective.id, title, ownerId: team[index === 0 ? 1 : 2].id,
        status: 'queued' as const, dependsOn: index ? [`${objective.id}:task:${index - 1}`] : [],
      }))

      publish({ ...snapshot, objectives: [objective, ...snapshot.objectives], agents: [...snapshot.agents, ...team], tasks: [...snapshot.tasks, ...tasks], activity: [{
        id: crypto.randomUUID(), objectiveId: objective.id, kind: 'planning',
        text: 'Objective captured locally. A live planning adapter is not connected.', timestamp: createdAt, source: 'prototype'
      }, ...tasks.map(task => ({ id: crypto.randomUUID(), objectiveId: objective.id, agentId: task.ownerId, kind: 'delegation' as const, text: `Simulated assignment: ${task.title}. No work dispatched.`, timestamp: createdAt, source: 'prototype' as const })), ...snapshot.activity] })

      return objective
    },
    updateObjectiveMetadata(id, metadata) {
      validateMetadata(metadata)

      if (!snapshot.objectives.some(item => item.id === id)) {throw new Error('Objective not found.')}
      publish({ ...snapshot, objectives: snapshot.objectives.map(item => item.id === id ? { ...item, ...metadata, description: metadata.description ?? item.description } : item) })
    },
    recordOutcome(id, summary) {
      const objective = snapshot.objectives.find(item => item.id === id)

      if (!objective) {throw new Error('Objective not found')}
      const result = summary.trim()

      if (!result) {throw new Error('An outcome summary is required')}
      const timestamp = new Date().toISOString()
      publish({ ...snapshot,
        objectives: snapshot.objectives.map(item => item.id === id ? { ...item, status: 'completed', result } : item),
        knowledge: [{ id: crypto.randomUUID(), title: `${objective.title} — recorded outcome`, body: `User-recorded outcome · Local prototype\n\n${result}`, kind: 'artifact', objectiveId: id }, ...snapshot.knowledge],
        activity: [{ id: crypto.randomUUID(), objectiveId: id, kind: 'completion', text: 'User recorded a local outcome. No live execution was performed or verified.', timestamp, source: 'prototype' }, ...snapshot.activity]
      })
    },
    setObjectiveStatus(id, status) {
      if (!snapshot.objectives.some(objective => objective.id === id)) {throw new Error('Objective not found.')}
      publish({ ...snapshot,
        objectives: snapshot.objectives.map(objective => objective.id === id ? { ...objective, status } : objective),
        activity: [{ id: crypto.randomUUID(), objectiveId: id, kind: 'planning',
          text: `Local objective state changed to ${status}. No live execution was changed.`,
          timestamp: new Date().toISOString(), source: 'prototype'
        }, ...snapshot.activity]
      })
    }
  }
}

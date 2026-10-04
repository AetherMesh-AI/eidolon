import type { OrganizationSnapshot } from './types'

/** Fictional scenario. Never merged with gateway sessions or presented as live. */
export function demoSnapshot(): OrganizationSnapshot {
  const timestamp = new Date().toISOString()

  return {
    decisions: [{ id: 'demo-decision', objectiveId: 'demo-identity', title: 'Review identity migration plan', summary: 'Fictional review gate before rollout. Approval affects this prototype only.', status: 'pending' }],
    objectives: [{ id: 'demo-identity', title: 'Unified identity architecture', description: 'Example scenario: unify account identity across game platforms.', status: 'active', ownerId: 'lead', createdAt: timestamp, source: 'prototype' },
      { id: 'demo-workspace', title: 'Workspace navigation review', description: 'Fictional review of compact workspace navigation; awaiting user direction.', status: 'waiting', ownerId: 'engineering', createdAt: timestamp, source: 'prototype', phase: 'Design review', milestone: 'Example navigation proposal prepared' },
      { id: 'demo-providers', title: 'Provider architecture research', description: 'Fictional completed comparison of provider integration boundaries.', status: 'completed', ownerId: 'research', createdAt: timestamp, source: 'prototype', result: 'Illustrative provider boundary report. No research was executed.', phase: 'Complete', milestone: 'Example comparison recorded' }
    ],
    agents: [
      { id: 'lead', name: 'Lead', role: 'Executive coordinator', status: 'reviewing', responsibilities: ['Example organizational role'], summary: 'Illustrative assignment; not a connected live agent.', capabilities: ['Planning', 'Delegation', 'Approval review'], objectiveId: 'demo-identity' },
      { id: 'engineering', name: 'Engineering', team: 'Engineering', role: 'Architecture & implementation', managerId: 'lead', status: 'executing', responsibilities: ['Example organizational role'], summary: 'Illustrative assignment; not a connected live agent.', capabilities: ['System design', 'Implementation', 'Code review'], objectiveId: 'demo-identity' },
      { id: 'research', name: 'Research', role: 'Evidence & discovery', managerId: 'lead', status: 'idle', responsibilities: ['Example organizational role'], summary: 'Illustrative assignment; not a connected live agent.', capabilities: ['Source verification', 'Compatibility research'], objectiveId: 'demo-identity' },
      { id: 'security', name: 'Security', role: 'Risk & assurance', managerId: 'engineering', status: 'needs_input', responsibilities: ['Example organizational role'], summary: 'Illustrative assignment; not a connected live agent.', capabilities: ['Threat modeling', 'Security review'], objectiveId: 'demo-identity' }
    ],
    tasks: [
      { id: 'architecture', objectiveId: 'demo-identity', title: 'Define account architecture', ownerId: 'engineering', status: 'completed', dependsOn: [] },
      { id: 'research', objectiveId: 'demo-identity', title: 'Review identity providers', ownerId: 'research', status: 'completed', dependsOn: ['architecture'] },
      { id: 'implementation', objectiveId: 'demo-identity', title: 'Implement account adapter', ownerId: 'engineering', status: 'working', dependsOn: ['architecture', 'research'] },
      { id: 'security', objectiveId: 'demo-identity', title: 'Review token policy', ownerId: 'security', status: 'blocked', dependsOn: ['implementation'] },
      { id: 'navigation-review', objectiveId: 'demo-workspace', title: 'Review compact navigation proposal', ownerId: 'engineering', assignedById: 'lead', priority: 'normal', review: 'required', status: 'review', dependsOn: [] },
      { id: 'provider-report', objectiveId: 'demo-providers', title: 'Summarize provider boundaries', ownerId: 'research', assignedById: 'lead', status: 'completed', dependsOn: [], results: ['Fictional comparison summary; no runtime research executed.'] }
    ],
    activity: [
      { id: 'demo-event-review', kind: 'approval', timestamp, text: 'Example: token retention policy requires user approval. No actual policy has changed.', objectiveId: 'demo-identity', agentId: 'security', source: 'prototype', provenance: 'fictional', description: 'Illustrative review request: assess refresh-token retention before rollout. No permission or security policy was changed.' },
      { id: 'demo-event-work', kind: 'delegation', timestamp, text: 'Example: Lead assigned account adapter implementation to Engineering.', objectiveId: 'demo-identity', agentId: 'lead', source: 'prototype', provenance: 'fictional', description: 'Illustrative assignment from Lead to Engineering for the account adapter. No task was dispatched.' },
      { id: 'demo-event-research', kind: 'completion', timestamp, text: 'Example: Research completed provider compatibility review.', objectiveId: 'demo-identity', agentId: 'research', source: 'prototype', provenance: 'fictional', description: 'Illustrative research completion, not verified work.' },
      { id: 'demo-event-decision', kind: 'decision', timestamp, text: 'Example: Lead accepted the staged migration recommendation.', description: 'Illustrative recorded decision to evaluate staged migration. No runtime configuration changed.', objectiveId: 'demo-identity', agentId: 'lead', source: 'prototype', provenance: 'fictional' },
      { id: 'demo-event-tool', kind: 'tool', timestamp, text: 'Example: Engineering checked adapter compatibility.', description: 'Illustrative tool action: run a compatibility check. No command was executed and no output or exit status is claimed.', objectiveId: 'demo-identity', agentId: 'engineering', source: 'prototype', provenance: 'fictional' },
      { id: 'demo-event-file', kind: 'file', timestamp, text: 'Example: Engineering updated the identity adapter design.', description: 'Illustrative file change: service.identity design notes. No file was read or modified; no diff is attached.', objectiveId: 'demo-identity', agentId: 'engineering', source: 'prototype', provenance: 'fictional' },
      { id: 'demo-event-system', kind: 'system', timestamp, text: 'Example: organization workspace initialized.', description: 'Illustrative system lifecycle event. No backend process started or connection was established.', objectiveId: 'demo-identity', source: 'prototype', provenance: 'fictional' }
    ],
    knowledge: [
      { id: 'demo-report', title: 'Identity compatibility report', kind: 'artifact', objectiveId: 'demo-identity', body: 'Example artifact, not generated research. A real integration would attach verified provider compatibility, citations, and source files here.' },
      { id: 'demo-decision', title: 'Token retention policy', kind: 'document', objectiveId: 'demo-identity', body: 'Example decision awaiting review: use short-lived access tokens and explicit session revocation. This is illustrative, not an approved security policy.' },
      { id: 'demo-memory', title: 'Project identity boundaries', kind: 'memory', body: 'Example organizational context. Runtime profile identity and canonical session IDs remain backend-owned and are never derived from display names.' }
    ]
  }
}

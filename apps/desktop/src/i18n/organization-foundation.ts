/** English source messages. Other locales inherit these until translations are reviewed.
 * Keep content supplied by owners, agents and the runtime out of this catalog. */
export const organizationFoundationEn = {
  priority: 'Priority', progress: 'Progress', currentPhase: 'Current phase', milestone: 'Recent milestone',
  autonomyIntent: 'Autonomy intent', created: 'Created', createdLocally: 'Created locally',
  notSet: 'Not set', unknown: 'Unknown', notRecorded: 'Not recorded', notSpecified: 'Not specified',
  priorities: { low: 'Low', normal: 'Normal', high: 'High', P5: 'Lowest', P4: 'Low', P3: 'Normal', P2: 'High', P1: 'Highest' },
  completedProgress: (progress: string) => `${progress} · completed tasks`,
  estimatedProgress: (progress: string) => `${progress} · local estimate`,
  runtimeProgressNote: 'Progress counts runtime-completed tasks. Completion requires the recorded review.',
  localProgressNote: 'Historical local planning intent only. This read-only record does not enforce permissions, grant approval, or dispatch work.',
  timeline: 'Coordination timeline',
  runtimeTimeline: 'Runtime coordination, decisions and outcomes recorded by the connected gateway.',
  localTimeline: 'Human-readable coordination, decisions and outcomes. Prototype records are not live execution logs.',
  searchActivity: 'Search activity', searchEvents: 'Search events', eventType: 'Event type', all: 'All',
  activityObjective: 'Activity objective', allObjectives: 'All objectives', activityAgent: 'Activity agent', allAgents: 'All agents',
  categories: { assignment: 'Assignments', decision: 'Decisions', tool: 'Tools', file: 'Files', review: 'Reviews', system: 'System' },
  inspectEvent: (text: string) => `Inspect event: ${text}`,
  // English fallback keeps English plural rules; translated messages own their grammar.
  eventCount: (count: number, formatted: string) => `${formatted} ${count === 1 ? 'event' : 'events'}`,
  organization: 'Organization', noMatches: 'No matching events', noEvents: 'No organization events yet',
  eventsNote: 'Events appear when the gateway records organization activity.', logs: 'Inspect live process logs →',
  runtimeKind: 'Runtime event', fictionalKind: 'Fictional example', localKind: 'Local prototype',
  runtimeEvent: 'Runtime event · Gateway record', fictionalEvent: 'Fictional example · Not live', localEvent: 'Local prototype event · Not live',
  category: 'Category', recorded: 'Recorded', agent: 'Agent', noAgent: 'No agent linked', eventId: 'Event ID', source: 'Source',
  gatewaySource: 'Connected gateway organization runtime', details: 'Observable details', openObjective: 'Open objective', diagnostic: 'Diagnostic context',
  noScope: 'Execution scope not reported by runtime.',
  evidenceNote: 'Coordination events do not by themselves verify external tool execution. Inspect task evidence for recorded results and session identifiers.',
  localEvidenceNote: 'No runtime log or process identifier is attached. This record comes from the local prototype adapter.'
}

export type OrganizationFoundationCopy = typeof organizationFoundationEn

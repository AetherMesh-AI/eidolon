import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'

import { Activity } from './activity'
import { Organization } from './organization'
import { OrganizationRail } from './rail'
import type { OrganizationSnapshot } from './types'
import { WorkGraph } from './work-graph'

function runtimeSnapshot(): OrganizationSnapshot {
  return {
    source: 'runtime',
    runtime: { capabilities: ['writing', 'analysis'], maxWorkers: 2, state: 'running', scope: 'Writing and analysis of submitted context. External tools and unsupported requests require intervention.' },
    objectives: [{ id: 'objective-1', title: 'Draft a release brief', description: 'Use the submitted release context.', source: 'runtime', status: 'active', createdAt: '2026-10-04T00:00:00Z', ownerId: 'director' }],
    agents: [
      { id: 'director', name: 'Release Director', role: 'Director', team: 'Communications', responsibilities: ['Route objectives'], capabilities: [], requestTypes: ['route'], status: 'idle', summary: 'Routes submitted work.' },
      { id: 'worker', name: 'Brief Writer', role: 'Employee', team: 'Communications', managerId: 'director', responsibilities: ['Draft briefs'], capabilities: ['writing'], requestTypes: ['writing'], tools: [], status: 'idle', summary: 'Drafts from supplied context.' },
      { id: 'reviewer', name: 'Brief Reviewer', role: 'Manager', team: 'Communications', managerId: 'director', responsibilities: ['Review evidence'], capabilities: ['analysis'], requestTypes: ['review'], status: 'reviewing', summary: 'Reviews the draft and its evidence.' },
    ],
    tasks: [
      { id: 'write-1', objectiveId: 'objective-1', ownerId: 'worker', assignedById: 'director', title: 'Write the brief', requestType: 'writing', team: 'Communications', priority: 'P2', status: 'completed', review: 'required', dependsOn: [], evidence: [{ id: 'evidence-1', kind: 'worker_output', content: 'The release brief covers the submitted changes.', sessionId: 'worker-session-1', createdAt: '2026-10-04T00:02:00Z', sha256: 'abc123' }] },
      { id: 'review-1', objectiveId: 'objective-1', ownerId: 'reviewer', assignedById: 'director', title: 'Review the brief', requestType: 'review', team: 'Communications', priority: 'P2', status: 'working', dependsOn: ['write-1'] },
    ],
    activity: [{ id: 'event-1', source: 'runtime', objectiveId: 'objective-1', agentId: 'worker', kind: 'completion', text: 'Brief output recorded', description: 'Worker output was persisted for manager review.', timestamp: '2026-10-04T00:02:00Z' }],
    knowledge: [],
  }
}

function inspectTask(title: string) {
  fireEvent.click(screen.getByRole('button', { name: `Inspect task: ${title}` }))

  return within(screen.getByRole('complementary', { name: 'Task details' }))
}

describe('runtime organization rendering', () => {
  it('renders task provenance, dispatch tags, evidence and recorded review relationships', () => {
    const snapshot = runtimeSnapshot()
    render(<WorkGraph objectiveId="objective-1" snapshot={snapshot} />)
    const task = snapshot.tasks[0]
    const row = within(screen.getByRole('button', { name: `Inspect task: ${task.title}` }))
    expect(row.getByText(`Team: ${task.team}`)).toBeTruthy()
    expect(row.getByText(`Type: ${task.requestType} · Priority: ${task.priority}`)).toBeTruthy()
    const panel = inspectTask(task.title)
    expect(panel.getByText('Runtime task · Gateway record')).toBeTruthy()
    expect(panel.getByText('Brief Reviewer · Review the brief (working)')).toBeTruthy()
    expect(panel.getByText(task.evidence![0].content)).toBeTruthy()
    expect(panel.getByText(`Session: ${task.evidence![0].sessionId}`)).toBeTruthy()
    expect(panel.getByText(`Evidence ID: ${task.evidence![0].id}`)).toBeTruthy()
    expect(panel.getByText(`SHA-256: ${task.evidence![0].sha256}`)).toBeTruthy()
    expect(panel.getByText(snapshot.runtime!.scope)).toBeTruthy()
    expect(panel.getByText(/do not by themselves verify external tool execution/)).toBeTruthy()
    expect(panel.queryByText(/Prototype|fictional|Not dispatched|No runtime session linked/i)).toBeNull()
  })

  it('shows reviewers’ actual dependencies and distinguishes missing evidence from completed outputs', () => {
    const snapshot = runtimeSnapshot()
    render(<WorkGraph objectiveId="objective-1" snapshot={snapshot} />)
    const panel = inspectTask(snapshot.tasks[1].title)
    expect(panel.getByText('Brief Writer · Write the brief')).toBeTruthy()
    expect(panel.getByText('1 / 1 complete')).toBeTruthy()
    expect(panel.getByText('No evidence recorded yet. Task status alone is not proof of an output.')).toBeTruthy()
    expect(panel.getByText('No runtime session recorded in task evidence')).toBeTruthy()
    expect(panel.queryByText('worker-session-1')).toBeNull()
  })

  it('keeps the assigned reviewer separate from the worker who owns the task', () => {
    const snapshot = runtimeSnapshot()
    snapshot.tasks[0].reviewerId = 'reviewer'
    snapshot.tasks[0].status = 'review'
    snapshot.tasks = [snapshot.tasks[0]]
    const { unmount } = render(<WorkGraph objectiveId="objective-1" snapshot={snapshot} />)
    const panel = inspectTask(snapshot.tasks[0].title)
    expect(panel.getByText('Brief Writer')).toBeTruthy()
    expect(panel.getByText('Brief Reviewer')).toBeTruthy()
    expect(panel.getByText('Assigned reviewer')).toBeTruthy()
    unmount()
    render(<MemoryRouter><Organization snapshot={snapshot} /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Brief Reviewer' }))
    const agentPanel = within(screen.getByRole('complementary', { name: 'Agent details' }))
    expect(agentPanel.getByText('Review: Write the brief (review)')).toBeTruthy()
    expect(agentPanel.getByText(/Review assignment/)).toBeTruthy()
  })

  it('labels truncated evidence and opens only the selected full artifact on request', () => {
    const snapshot = runtimeSnapshot()
    snapshot.tasks[0].evidence![0].truncated = true
    const onOpenEvidence = vi.fn()
    render(<WorkGraph objectiveId="objective-1" onOpenEvidence={onOpenEvidence} snapshot={snapshot} />)
    const panel = inspectTask(snapshot.tasks[0].title)
    expect(panel.getByText('Evidence preview truncated. The complete artifact contains additional content.')).toBeTruthy()
    expect(onOpenEvidence).not.toHaveBeenCalled()
    fireEvent.click(panel.getByRole('button', { name: 'Read full evidence' }))
    expect(onOpenEvidence).toHaveBeenCalledExactlyOnceWith(snapshot.tasks[0].evidence![0].id)
    expect(screen.queryByRole('complementary', { name: 'Task details' })).toBeNull()
  })

  it('keeps task inspectors scoped when the selected objective changes', () => {
    const snapshot = runtimeSnapshot()
    const { rerender } = render(<WorkGraph objectiveId="objective-1" snapshot={snapshot} />)
    inspectTask(snapshot.tasks[0].title)
    rerender(<WorkGraph objectiveId="another-objective" snapshot={snapshot} />)
    expect(screen.queryByRole('complementary', { name: 'Task details' })).toBeNull()
    expect(screen.getByText('No tasks recorded for this objective yet.')).toBeTruthy()
  })

  it('shows runtime agent roles, team, request types and real assignments without claiming a missing connection', () => {
    const snapshot = runtimeSnapshot()
    snapshot.tasks.push({ ...snapshot.tasks[0], id: 'cancelled-task', title: 'Cancelled draft', status: 'cancelled', evidence: [] })
    render(<MemoryRouter><Organization snapshot={snapshot} /></MemoryRouter>)
    screen.getByRole('button', { name: 'Inspect Brief Writer' }).focus()
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Brief Writer' }))
    const panel = within(screen.getByRole('complementary', { name: 'Agent details' }))
    expect(panel.getByText('Runtime agent · Gateway record')).toBeTruthy()
    expect(panel.getByText('Communications')).toBeTruthy()
    expect(panel.getByText('Request types')).toBeTruthy()
    expect(panel.getByText('Release Director')).toBeTruthy()
    expect(panel.getByText('Text-only · no tools enabled')).toBeTruthy()
    expect(panel.getByText('No current task assignment recorded')).toBeTruthy()
    expect(panel.queryByText('Unavailable · No runtime connection')).toBeNull()
    expect(panel.queryByText('Prototype agent · Not live')).toBeNull()
    fireEvent.keyDown(globalThis.document, { key: 'Escape' })
    expect(screen.getByRole('button', { name: 'Inspect Brief Writer' })).toBe(globalThis.document.activeElement)
  })

  it('finds agents by request type and shows the work a reviewer is assigned to review', () => {
    const snapshot = runtimeSnapshot()
    render(<MemoryRouter><Organization snapshot={snapshot} /></MemoryRouter>)
    fireEvent.change(screen.getByPlaceholderText('Name or responsibility…'), { target: { value: 'review' } })
    expect(screen.queryByRole('button', { name: 'Inspect Brief Writer' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Brief Reviewer' }))
    const panel = within(screen.getByRole('complementary', { name: 'Agent details' }))
    expect(panel.getByText(/Reviews: Write the brief/)).toBeTruthy()
    expect(panel.getByRole('link', { name: 'Review the brief' }).getAttribute('href')).toBe('/objectives/objective-1')
  })

  it('shows runtime activity with gateway identity and evidence context', () => {
    const snapshot = runtimeSnapshot()
    render(<MemoryRouter><Activity snapshot={snapshot} /></MemoryRouter>)
    expect(screen.getByText('Assignments · Runtime event')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: `Inspect event: ${snapshot.activity[0].text}` }))
    const panel = within(screen.getByRole('complementary', { name: 'Event details' }))
    expect(panel.getByText('Runtime event · Gateway record')).toBeTruthy()
    expect(panel.getByText(snapshot.activity[0].id)).toBeTruthy()
    expect(panel.getByText(snapshot.activity[0].description!)).toBeTruthy()
    expect(panel.getByText(snapshot.runtime!.scope)).toBeTruthy()
    expect(panel.queryByText(/local prototype|fictional|Not live/i)).toBeNull()
    expect(panel.getByRole('link', { name: 'Open objective' }).getAttribute('href')).toBe('/objectives/objective-1')
  })

  it('does not offer prototype planning as the source of empty runtime activity', () => {
    const snapshot = runtimeSnapshot()
    snapshot.activity = []
    render(<MemoryRouter><Activity snapshot={snapshot} /></MemoryRouter>)
    expect(screen.getByText('Events appear when the gateway records organization activity.')).toBeTruthy()
    expect(screen.queryByText(/local planning event/)).toBeNull()
  })

  it('labels the rail runtime by default and identifies explicitly selected prototype mode', () => {
    const { rerender } = render(<MemoryRouter><OrganizationRail sessions={null} /></MemoryRouter>)
    expect(screen.getByText(/Organization evidence · current connection and profile/)).toBeTruthy()
    expect(screen.queryByText(/Organization · Local prototype/)).toBeNull()
    rerender(<MemoryRouter><OrganizationRail mode="prototype" sessions={null} /></MemoryRouter>)
    expect(screen.getAllByText('Legacy prototype history').some(element => element.className === 'eid-rail-footer')).toBe(true)
  })
})

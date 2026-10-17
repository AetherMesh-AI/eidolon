import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useRef } from 'react'
import { Link, MemoryRouter, useLocation } from 'react-router'
import { afterEach, expect, it } from 'vitest'

import { Activity } from './activity'
import { demoSnapshot } from './demo'
import { Organization } from './organization'
import type { OrganizationSnapshot } from './types'
import { useWorkspaceScroll } from './workspace'
import { objectiveReturnTarget, type WorkspaceFocusRestoration, WorkspaceFocusRestoreContext } from './workspace-navigation'

afterEach(cleanup)

function snapshot(): OrganizationSnapshot {
  const value = demoSnapshot()

  return { ...value, connection: { scope: 'socket', ownerScope: 'owner-a', state: 'ready' }, agents: value.agents.map(item => ({ ...item, identityId: `identity-${item.id}` })) }
}

function Harness({ value, embedded = false }: { value: OrganizationSnapshot; embedded?: boolean }) {
  const location = useLocation()
  const detail = location.pathname.startsWith('/objectives/') && !embedded
  const restoration = useRef<WorkspaceFocusRestoration | null>(null)
  const root = useWorkspaceScroll(location, value.connection?.ownerScope, restoration)

  return <WorkspaceFocusRestoreContext.Provider value={restoration}><input aria-label="Outside search" /><main data-testid="workspace" ref={root}><output data-testid="url">{location.pathname + location.search}</output>{detail
    ? <Link state={{ workspaceReturnOwner: value.connection?.ownerScope }} to={objectiveReturnTarget(new URLSearchParams(location.search), value.connection?.ownerScope)}>Return</Link>
    : location.pathname === '/organization' ? <Organization snapshot={value} /> : <Activity objectiveId={embedded ? 'demo-identity' : undefined} snapshot={value} />}</main></WorkspaceFocusRestoreContext.Provider>
}

function mount(value: OrganizationSnapshot, route = '/activity', embedded = false) {
  return render(<MemoryRouter initialEntries={[route]}><Harness embedded={embedded} value={value} /></MemoryRouter>)
}

it('restores filtered Activity and exact event after objective return without replacing owner context', () => {
  const value = snapshot()
  mount(value)
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'token retention' } })
  fireEvent.click(screen.getByRole('button', { name: /Inspect event:.*token retention/ }))
  const origin = screen.getByTestId('url').textContent
  fireEvent.click(screen.getByRole('link', { name: 'Open objective' }))
  fireEvent.click(screen.getByRole('link', { name: 'Return' }))
  expect(screen.getByTestId('url').textContent).toBe(origin)
  expect(screen.getByRole('searchbox')).toHaveProperty('value', 'token retention')
  expect(screen.getByRole('complementary', { name: 'Event details' })).toBeTruthy()
})

it('does not reopen events for another owner or a replaced/deleted/filtered event', () => {
  const value = snapshot()
  const event = value.activity[0]
  const params = new URLSearchParams({ activityOwner: 'owner-a', activityEvent: event.id, activityIdentity: JSON.stringify([event.timestamp, event.objectiveId, event.agentId]) })
  const view = mount(value, '/activity?' + params)
  expect(screen.getByRole('complementary', { name: 'Event details' })).toBeTruthy()
  const update = (next: OrganizationSnapshot) => view.rerender(<MemoryRouter><Harness value={next} /></MemoryRouter>)
  update({ ...value, activity: [{ ...event, timestamp: 'new-record' }] })
  expect(screen.queryByRole('complementary', { name: 'Event details' })).toBeNull()
  update({ ...value, activity: [] })
  expect(screen.queryByRole('complementary', { name: 'Event details' })).toBeNull()
  update({ ...value, connection: { ...value.connection!, ownerScope: 'owner-b' } })
  expect(screen.queryByRole('complementary', { name: 'Event details' })).toBeNull()
})

it('preserves original objective origin on an embedded activity self-link', () => {
  const params = new URLSearchParams({ from: '/work?activityOwner=owner-a', originOwner: 'owner-a' })
  mount(snapshot(), '/objectives/demo-identity?' + params, true)
  fireEvent.click(screen.getByRole('button', { name: /Inspect event:.*token retention/ }))
  expect(screen.getByRole('link', { name: 'Open objective' }).getAttribute('href')).toBe(screen.getByTestId('url').textContent)
  expect(objectiveReturnTarget(new URLSearchParams(screen.getByTestId('url').textContent!.split('?')[1]), 'owner-a')).toBe('/work?activityOwner=owner-a')
})

it('restores Organization query, layout and exact member with an assignment link', () => {
  const value = snapshot()
  mount(value, '/organization')
  fireEvent.change(screen.getByRole('textbox', { name: 'Find an agent' }), { target: { value: 'Engineering' } })
  fireEvent.click(screen.getByRole('button', { name: 'Grid' }))
  fireEvent.click(screen.getByRole('button', { name: 'Inspect Engineering' }))
  const origin = screen.getByTestId('url').textContent
  fireEvent.click(screen.getAllByRole('link').find(link => link.getAttribute('href')?.startsWith('/objectives/'))!)
  fireEvent.click(screen.getByRole('link', { name: 'Return' }))
  expect(screen.getByTestId('url').textContent).toBe(origin)
  expect(screen.getByRole('textbox', { name: 'Find an agent' })).toHaveProperty('value', 'Engineering')
  expect(screen.getByRole('button', { name: 'Grid' }).getAttribute('aria-pressed')).toBe('true')
  expect(screen.getByRole('complementary', { name: 'Agent details' })).toBeTruthy()
})

it('rejects stale member identities and closes a filtered or deleted member without opening another', () => {
  const value = snapshot()
  const member = value.agents[0]
  const params = new URLSearchParams({ rosterOwner: 'owner-a', rosterMember: member.id, rosterIdentity: member.identityId! })
  const view = mount(value, '/organization?' + params)
  expect(screen.getByRole('complementary', { name: 'Agent details' })).toBeTruthy()
  fireEvent.change(screen.getByRole('textbox', { name: 'Find an agent' }), { target: { value: 'no-such-person' } })
  expect(screen.queryByRole('complementary', { name: 'Agent details' })).toBeNull()
  fireEvent.change(screen.getByRole('textbox', { name: 'Find an agent' }), { target: { value: '' } })
  view.rerender(<MemoryRouter><Harness value={{ ...value, agents: [{ ...member, identityId: 'replacement' }] }} /></MemoryRouter>)
  expect(screen.queryByRole('complementary', { name: 'Agent details' })).toBeNull()
  view.rerender(<MemoryRouter><Harness value={{ ...value, agents: [] }} /></MemoryRouter>)
  expect(screen.queryByRole('complementary', { name: 'Agent details' })).toBeNull()
  view.rerender(<MemoryRouter><Harness value={{ ...value, connection: { ...value.connection!, ownerScope: 'owner-b' } }} /></MemoryRouter>)
  expect(screen.queryByRole('complementary', { name: 'Agent details' })).toBeNull()
})

it('restored event details preserve shared workspace scroll and objective-link focus', () => {
  mount(snapshot())
  fireEvent.click(screen.getByRole('button', { name: /Inspect event:.*token retention/ }))
  const root = screen.getByTestId('workspace')
  root.scrollTop = 320
  fireEvent.scroll(root)
  fireEvent.click(screen.getByRole('link', { name: 'Open objective' }))
  fireEvent.click(screen.getByRole('link', { name: 'Return' }))
  expect(root.scrollTop).toBe(320)
  expect(globalThis.document.activeElement).toBe(screen.getByRole('link', { name: 'Open objective' }))
})

it('normal inspection and direct same-owner event URLs still autofocus Close', () => {
  const value = snapshot()
  const view = mount(value)
  fireEvent.click(screen.getByRole('button', { name: /Inspect event:.*token retention/ }))
  expect(globalThis.document.activeElement).toBe(screen.getByRole('button', { name: 'Close event details' }))
  const route = screen.getByTestId('url').textContent!
  view.unmount()
  mount(value, route)
  expect(globalThis.document.activeElement).toBe(screen.getByRole('button', { name: 'Close event details' }))
})

it('restored member details preserve shared objective-link focus and current owner isolation', () => {
  const value = snapshot()
  const view = mount(value, '/organization')
  fireEvent.click(screen.getByRole('button', { name: 'Inspect Engineering' }))
  expect(globalThis.document.activeElement).toBe(screen.getByRole('button', { name: 'Close agent details' }))
  const root = screen.getByTestId('workspace')
  root.scrollTop = 280
  const link = screen.getAllByRole('link').find(item => item.getAttribute('href')?.startsWith('/objectives/'))!
  const href = link.getAttribute('href')
  fireEvent.click(link)
  fireEvent.click(screen.getByRole('link', { name: 'Return' }))
  expect(root.scrollTop).toBe(280)
  expect(globalThis.document.activeElement?.getAttribute('href')).toBe(href)
  view.rerender(<MemoryRouter><Harness value={{ ...value, connection: { ...value.connection!, ownerScope: 'other-owner' } }} /></MemoryRouter>)
  expect(screen.queryByRole('complementary', { name: 'Agent details' })).toBeNull()
})

it('restores objective link focus after the origin event arrives later', async () => {
  const value = snapshot()
  const view = mount(value)
  fireEvent.click(screen.getByRole('button', { name: /Inspect event:.*token retention/ }))
  const root = screen.getByTestId('workspace')
  root.scrollTop = 320
  fireEvent.click(screen.getByRole('link', { name: 'Open objective' }))
  view.rerender(<MemoryRouter><Harness value={{ ...value, activity: [] }} /></MemoryRouter>)
  fireEvent.click(screen.getByRole('link', { name: 'Return' }))
  expect(screen.queryByRole('complementary', { name: 'Event details' })).toBeNull()
  view.rerender(<MemoryRouter><Harness value={value} /></MemoryRouter>)
  await waitFor(() => expect(globalThis.document.activeElement).toBe(screen.getByRole('link', { name: 'Open objective' })))
  expect(root.scrollTop).toBe(320)
  fireEvent.click(screen.getByRole('button', { name: 'Close event details' }))
  expect(globalThis.document.activeElement).toBe(screen.getByRole('button', { name: /Inspect event:.*token retention/ }))
})

it.each(['input', 'deadline'] as const)('late origin records cannot steal focus after restoration %s cancellation', async reason => {
  const value = snapshot()
  const view = mount(value)
  fireEvent.click(screen.getByRole('button', { name: /Inspect event:.*token retention/ }))
  fireEvent.click(screen.getByRole('link', { name: 'Open objective' }))
  view.rerender(<MemoryRouter><Harness value={{ ...value, activity: [] }} /></MemoryRouter>)
  fireEvent.click(screen.getByRole('link', { name: 'Return' }))
  const outside = screen.getByRole('textbox', { name: 'Outside search' })

  if (reason === 'input') { outside.focus() }
  else { await act(async () => { await new Promise(resolve => setTimeout(resolve, 1550)) }) }

  const focused = globalThis.document.activeElement
  view.rerender(<MemoryRouter><Harness value={value} /></MemoryRouter>)
  expect(globalThis.document.activeElement).toBe(focused)
})

it.each(['/activity', '/organization'])('closing recreated details returns to the exact visible origin row on %s', route => {
  mount(snapshot(), route)
  const name = route === '/activity' ? /Inspect event:.*token retention/ : 'Inspect Engineering'
  fireEvent.click(screen.getByRole('button', { name }))
  fireEvent.click(screen.getAllByRole('link').find(link => link.getAttribute('href')?.startsWith('/objectives/'))!)
  fireEvent.click(screen.getByRole('link', { name: 'Return' }))
  fireEvent.click(screen.getByRole('button', { name: route === '/activity' ? 'Close event details' : 'Close agent details' }))
  expect(globalThis.document.activeElement).toBe(screen.getByRole('button', { name }))
})

 it('does not return focus to a connected Activity row reused by another owner', () => {
  const value = snapshot()
  const view = mount(value)
  const row = screen.getByRole('button', { name: /Inspect event:.*token retention/ })
  row.focus()
  fireEvent.click(row)
  expect(globalThis.document.activeElement).toBe(screen.getByRole('button', { name: 'Close event details' }))
  view.rerender(<MemoryRouter><Harness value={{ ...value, connection: { ...value.connection!, ownerScope: 'replacement-owner' } }} /></MemoryRouter>)
  expect(row.isConnected).toBe(true)
  expect(globalThis.document.activeElement).not.toBe(row)
})

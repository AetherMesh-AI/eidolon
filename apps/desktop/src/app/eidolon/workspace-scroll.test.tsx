import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { Link, MemoryRouter, useLocation, useNavigate } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { useWorkspaceScroll } from './workspace'
import { createWorkspaceScrollMemory, objectiveHref, objectiveReturnTarget } from './workspace-navigation'

afterEach(() => { cleanup(); vi.useRealTimers() })

function View({ owner, revision = 0, loaded = true }: { owner: string; revision?: number; loaded?: boolean }) {
  const location = useLocation()
  const navigate = useNavigate()
  const root = useWorkspaceScroll(location, owner)
  const detail = location.pathname.startsWith('/objectives/')

  return <><input aria-label="Header search" /><main data-testid="workspace" ref={root}>
    <span>{revision}</span><button onClick={() => navigate(-1)}>Browser Back</button><button onClick={() => navigate(1)}>Browser Forward</button><Link to="/home">Home navigation</Link>
    {detail ? <><h1>Objective</h1><Link state={{ workspaceReturnOwner: owner }} to={objectiveReturnTarget(new URLSearchParams(location.search), owner)}>Back</Link></> :
      <><h1>Home</h1>{loaded && <div className="eid-home-panel-body" data-testid="panel"><Link to={objectiveHref('new', location.pathname + location.search, owner)}>Open objective</Link></div>}</>}
  </main></>
}

it('starts detail at the top and returns both list and panel position without jumping on refresh', () => {
  const view = render(<MemoryRouter initialEntries={['/home?intake=assisted']}><View owner="one" /></MemoryRouter>)
  const root = screen.getByTestId('workspace')
  const panel = screen.getByTestId('panel')
  root.scrollTop = 260
  panel.scrollTop = 75
  fireEvent.scroll(panel)
  fireEvent.click(screen.getByRole('link', { name: 'Open objective' }))
  expect(root.scrollTop).toBe(0)
  root.scrollTop = 400
  fireEvent.scroll(root)
  view.rerender(<MemoryRouter initialEntries={['/home?intake=assisted']}><View owner="one" revision={1} /></MemoryRouter>)
  expect(root.scrollTop).toBe(400)
  fireEvent.click(screen.getByRole('link', { name: 'Back' }))
  expect(root.scrollTop).toBe(260)
  expect(screen.getByTestId('panel').scrollTop).toBe(75)
  expect(globalThis.document.activeElement).toBe(screen.getByRole('link', { name: 'Open objective' }))
})

it('drops return position and focus when the authenticated owner changes', () => {
  const view = render(<MemoryRouter initialEntries={['/home']}><View owner="one" /></MemoryRouter>)
  const root = screen.getByTestId('workspace')
  root.scrollTop = 260
  fireEvent.click(screen.getByRole('link', { name: 'Open objective' }))
  view.rerender(<MemoryRouter initialEntries={['/home']}><View owner="two" /></MemoryRouter>)
  fireEvent.click(screen.getByRole('link', { name: 'Back' }))
  expect(root.scrollTop).toBe(0)
  expect(globalThis.document.activeElement).not.toBe(screen.getByRole('link', { name: 'Open objective' }))
})

it('never retains positions for unscoped or non-workspace routes and discards an old owner cache', () => {
  const memory = createWorkspaceScrollMemory()
  const position = { top: 80, left: 0, contentHeight: 0, panels: [], focusHref: null }
  memory.save('one', '/home', position)
  expect(memory.read('one', '/home')).toEqual(position)
  memory.save('one', '/settings', position)
  expect(memory.read('one', '/settings')).toBeUndefined()
  memory.reset('two')
  expect(memory.read('one', '/home')).toBeUndefined()
  memory.save(undefined, '/home', position)
  expect(memory.read(undefined, '/home')).toBeUndefined()
})


it('distinguishes an explicit Home navigation from browser Back and Forward', () => {
  render(<MemoryRouter initialEntries={['/home']}><View owner="one" /></MemoryRouter>)
  const root = screen.getByTestId('workspace')
  root.scrollTop = 240
  fireEvent.click(screen.getByRole('link', { name: 'Open objective' }))
  fireEvent.click(screen.getByRole('button', { name: 'Browser Back' }))
  expect(root.scrollTop).toBe(240)
  fireEvent.click(screen.getByRole('button', { name: 'Browser Forward' }))
  expect(root.scrollTop).toBe(0)
  fireEvent.click(screen.getByRole('link', { name: 'Home navigation' }))
  expect(root.scrollTop).toBe(0)
})

it('restores an asynchronously remounted origin panel once and then leaves background updates alone', async () => {
  const tree = (loaded: boolean, revision = 0) => <MemoryRouter initialEntries={['/home']}><View loaded={loaded} owner="one" revision={revision} /></MemoryRouter>
  const view = render(tree(true))
  const root = screen.getByTestId('workspace')
  root.scrollTop = 230
  screen.getByTestId('panel').scrollTop = 65
  fireEvent.click(screen.getByRole('link', { name: 'Open objective' }))
  view.rerender(tree(false))
  fireEvent.click(screen.getByRole('link', { name: 'Back' }))
  expect(screen.queryByTestId('panel')).toBeNull()
  await act(async () => view.rerender(tree(true)))
  expect(screen.getByTestId('panel').scrollTop).toBe(65)
  expect(root.scrollTop).toBe(230)
  root.scrollTop = 320
  await act(async () => view.rerender(tree(true, 2)))
  expect(root.scrollTop).toBe(320)
})

it('cancels deferred restoration when the owner starts a new interaction', async () => {
  const tree = (loaded: boolean) => <MemoryRouter initialEntries={['/home']}><View loaded={loaded} owner="one" /></MemoryRouter>
  const view = render(tree(true))
  screen.getByTestId('panel').scrollTop = 65
  fireEvent.click(screen.getByRole('link', { name: 'Open objective' }))
  view.rerender(tree(false))
  fireEvent.click(screen.getByRole('link', { name: 'Back' }))
  const root = screen.getByTestId('workspace')
  fireEvent.keyDown(root, { key: 'Tab' })
  await act(async () => view.rerender(tree(true)))
  expect(screen.getByTestId('panel').scrollTop).toBe(0)
})


it('does not steal header focus when origin content arrives after Back', async () => {
  const tree = (loaded: boolean) => <MemoryRouter initialEntries={['/home']}><View loaded={loaded} owner="one" /></MemoryRouter>
  const view = render(tree(true))
  screen.getByTestId('panel').scrollTop = 65
  fireEvent.click(screen.getByRole('link', { name: 'Open objective' }))
  view.rerender(tree(false))
  fireEvent.click(screen.getByRole('link', { name: 'Back' }))
  const search = screen.getByRole('textbox', { name: 'Header search' })
  search.focus()
  fireEvent.change(search, { target: { value: 'Keep typing here' } })
  await act(async () => view.rerender(tree(true)))
  expect(globalThis.document.activeElement).toBe(search)
  expect(screen.getByTestId('panel').scrollTop).toBe(0)
})


it('waits for the saved content extent before one-shot restore and ignores later growth', async () => {
  const tree = (revision: number) => <MemoryRouter initialEntries={['/home']}><View owner="one" revision={revision} /></MemoryRouter>
  const view = render(tree(0))
  const root = screen.getByTestId('workspace')
  let height = 2996
  Object.defineProperty(root, 'scrollHeight', { configurable: true, get: () => height })
  root.scrollTop = 2440.5
  fireEvent.click(screen.getByRole('link', { name: 'Open objective' }))
  height = 2792
  fireEvent.click(screen.getByRole('link', { name: 'Back' }))
  expect(root.scrollTop).toBe(0)
  height = 2838
  await act(async () => view.rerender(tree(1)))
  expect(root.scrollTop).toBe(0)
  height = 2996
  await act(async () => view.rerender(tree(2)))
  expect(root.scrollTop).toBe(2440.5)
  expect(globalThis.document.activeElement).toBe(screen.getByRole('link', { name: 'Open objective' }))
  root.scrollTop = 2500
  height = 3100
  await act(async () => view.rerender(tree(3)))
  expect(root.scrollTop).toBe(2500)
})


it('retires a permanently shorter return with one bounded fallback', async () => {
  vi.useFakeTimers()
  render(<MemoryRouter initialEntries={['/home']}><View owner="one" /></MemoryRouter>)
  const root = screen.getByTestId('workspace')
  let height = 1000
  Object.defineProperty(root, 'scrollHeight', { configurable: true, get: () => height })
  let top = 0
  Object.defineProperty(root, 'scrollTop', { configurable: true, get: () => top, set: value => { top = Math.min(value, height - 400) } })
  root.scrollTop = 250
  fireEvent.click(screen.getByRole('link', { name: 'Open objective' }))
  height = 600
  fireEvent.click(screen.getByRole('link', { name: 'Back' }))
  expect(root.scrollTop).toBe(0)
  await act(async () => vi.advanceTimersByTime(1500))
  expect(root.scrollTop).toBe(200)
  expect(globalThis.document.activeElement).toBe(screen.getByRole('link', { name: 'Open objective' }))
  height = 1000
  root.scrollTop = 300
  await act(async () => { height = 1000; root.append(globalThis.document.createElement('span')) })
  expect(root.scrollTop).toBe(300)
})

it('cancels the shorter-content fallback when the owner types outside the workspace', async () => {
  vi.useFakeTimers()
  render(<MemoryRouter initialEntries={['/home']}><View owner="one" /></MemoryRouter>)
  const root = screen.getByTestId('workspace')
  let height = 1000
  Object.defineProperty(root, 'scrollHeight', { configurable: true, get: () => height })
  root.scrollTop = 250
  fireEvent.click(screen.getByRole('link', { name: 'Open objective' }))
  height = 600
  fireEvent.click(screen.getByRole('link', { name: 'Back' }))
  const search = screen.getByRole('textbox', { name: 'Header search' })
  search.focus()
  fireEvent.keyDown(search, { key: 'a' })
  await act(async () => vi.advanceTimersByTime(1500))
  expect(root.scrollTop).toBe(0)
  expect(globalThis.document.activeElement).toBe(search)
})


it.each(['owner', 'navigation'])('retires deferred geometry on %s change', async change => {
  vi.useFakeTimers()
  const tree = (owner: string) => <MemoryRouter initialEntries={['/home']}><View owner={owner} /></MemoryRouter>
  const view = render(tree('one'))
  const root = screen.getByTestId('workspace')
  let height = 1000
  Object.defineProperty(root, 'scrollHeight', { configurable: true, get: () => height })
  root.scrollTop = 250
  fireEvent.click(screen.getByRole('link', { name: 'Open objective' }))
  height = 600
  fireEvent.click(screen.getByRole('link', { name: 'Back' }))

  if (change === 'owner') { view.rerender(tree('two')) }
  else { fireEvent.click(screen.getByRole('link', { name: 'Open objective' })) }

  root.scrollTop = 25
  await act(async () => vi.advanceTimersByTime(1500))
  expect(root.scrollTop).toBe(25)
})

it('retires the deadline when the saved origin permanently disappears', async () => {
  vi.useFakeTimers()
  const tree = (loaded: boolean) => <MemoryRouter initialEntries={['/home']}><View loaded={loaded} owner="one" /></MemoryRouter>
  const view = render(tree(true))
  const root = screen.getByTestId('workspace')
  root.scrollTop = 250
  fireEvent.click(screen.getByRole('link', { name: 'Open objective' }))
  view.rerender(tree(false))
  fireEvent.click(screen.getByRole('link', { name: 'Back' }))
  await act(async () => vi.advanceTimersByTime(1500))
  expect(vi.getTimerCount()).toBe(0)
  await act(async () => view.rerender(tree(true)))
  expect(root.scrollTop).toBe(0)
  expect(globalThis.document.activeElement).not.toBe(screen.getByRole('link', { name: 'Open objective' }))
})

it('removes the pending deadline on unmount without a late restore', async () => {
  vi.useFakeTimers()
  const tree = (loaded: boolean) => <MemoryRouter initialEntries={['/home']}><View loaded={loaded} owner="one" /></MemoryRouter>
  const view = render(tree(true))
  const root = screen.getByTestId('workspace')
  root.scrollTop = 250
  fireEvent.click(screen.getByRole('link', { name: 'Open objective' }))
  view.rerender(tree(false))
  fireEvent.click(screen.getByRole('link', { name: 'Back' }))
  expect(vi.getTimerCount()).toBe(1)
  view.unmount()
  expect(vi.getTimerCount()).toBe(0)
  await act(async () => vi.advanceTimersByTime(1500))
  expect(root.scrollTop).toBe(0)
})

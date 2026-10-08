import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { Link, MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway, type OrganizationScope } from './runtime-adapter'
import type { HistoryPage, HistoryQuery } from './runtime-history-types'
import type { Objective, OrganizationSnapshot } from './types'
import { OrganizationWorkspace } from './workspace'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void

  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })

  return { promise, resolve, reject }
}

function objective(id: string, archived = false): Objective {
  return {
    id,
    title: id === 'done-26' ? 'Literal [%_] completed' : `Retained ${id}`,
    description: 'Original submitted facts',
    ownerId: 'executive',
    createdAt: '2026-10-04T00:00:00Z',
    status: 'completed',
    source: 'runtime',
    result: `Accepted result ${id}`,
    history: { archived, revision: 0, updatedAt: null, canArchive: !archived, canRestore: archived, blocker: null }
  }
}

function fixture(path = '/objectives') {
  let owner = 'owner-a'
  let scope: OrganizationScope = { key: 'socket-a', ownerKey: owner, connected: true }

  let notify = () => {}
  let records = [...Array.from({ length: 27 }, (_, index) => objective(`done-${index}`)), objective('archived', true)]
  let intercept: ((method: string, params: Record<string, unknown>) => Promise<unknown> | undefined) | undefined

  const counts = () => ({
    current: records.filter(item => !item.history!.archived).length,
    archived: records.filter(item => item.history!.archived).length,
    currentLimit: 200,
    archiveLimit: 2000
  })

  const snapshot = (exactId?: string): OrganizationSnapshot => ({
    source: 'runtime',
    objectives: exactId
      ? records.filter(item => item.id === exactId)
      : records.filter(item => !item.history!.archived).slice(0, 25),
    agents: [],
    tasks: [],
    activity: [],
    requests: [],
    decisions: [],
    knowledge: exactId
      ? [
          {
            id: `evidence-${exactId}`,
            title: 'Original artifact',
            body: 'Short preview',
            kind: 'artifact',
            objectiveId: exactId
          }
        ]
      : [],
    runtime: { state: 'ready', capabilities: [], maxWorkers: 2, scope: 'Recorded work', history: counts() }
  })

  const page = ({ state = 'current', query = '', before, limit = 25 }: HistoryQuery): HistoryPage => {
    const matching = records.filter(
      item =>
        (state === 'all' || item.history!.archived === (state === 'archived')) &&
        item.title.toLowerCase().includes(query.toLowerCase())
    )

    const offset = before ? matching.findIndex(item => item.id === before) + 1 : 0
    const selected = matching.slice(offset, offset + limit)

    return {
      items: selected.map(item => ({
        id: item.id,
        title: item.title,
        createdAt: item.createdAt,
        status: 'completed',
        history: item.history!
      })),
      nextCursor: offset + limit < matching.length ? selected.at(-1)!.id : null,
      counts: counts()
    }
  }

  const request = vi.fn(async (method: string, params: Record<string, unknown> = {}) => {
    const pending = intercept?.(method, params)

    if (pending) {
      return pending
    }

    if (method === 'organization.snapshot') {
      return snapshot()
    }

    if (method === 'organization.history') {
      return page(params)
    }

    if (method === 'organization.historyObjective') {
      return snapshot(String(params.id))
    }

    if (method === 'organization.archive') {
      records = records.map(item =>
        item.id === params.id
          ? {
              ...item,
              history: {
                ...item.history!,
                archived: !!params.archived,
                revision: item.history!.revision + 1,
                canArchive: !params.archived,
                canRestore: !!params.archived
              }
            }
          : item
      )

      return snapshot()
    }

    if (method === 'organization.evidence') {
      return {
        id: params.id,
        content: 'Original immutable full evidence',
        sha256: 'original-digest',
        summary: 'Accepted evidence',
        createdAt: '2026-10-04T00:00:00Z',
        objectiveId: 'archived',
        taskId: null
      }
    }

    throw new Error(`Unexpected work dispatch: ${method}`)
  })

  const adapter = createRuntimeAdapter({
    request: request as OrganizationGateway['request'],
    getScope: () => scope,
    subscribeScope(listener) {
      notify = listener

      return () => {}
    }
  })

  const view = render(
    <MemoryRouter initialEntries={[path]}>
      <Link to="/objectives/done-26">Open old record</Link>
      <Link to="/objectives/archived">Open archived record</Link>
      <Link to="/objectives">Return to objectives</Link>
      <OrganizationWorkspace adapter={adapter} />
    </MemoryRouter>
  )

  return {
    view,
    adapter,
    request,
    snapshot,
    page,
    intercept(next: typeof intercept) {
      intercept = next
    },
    switchScope(next: OrganizationScope, nextRecords?: Objective[]) {
      owner = next.ownerKey ?? next.key
      scope = next

      if (nextRecords) {
        records = nextRecords
      }

      notify()
    }
  }
}

it('browses bounded literal history pages and reopens archived evidence without replacing live work or restarting it', async () => {
  const test = fixture()
  await waitFor(() => expect(test.adapter.getSnapshot().connection?.state).toBe('ready'))
  expect(test.request.mock.calls.filter(call => call[0] === 'organization.history')).toHaveLength(0)
  const trigger = screen.getByRole('button', { name: 'Objective history' })
  expect(trigger.getAttribute('aria-expanded')).toBe('false')
  fireEvent.click(trigger)
  const browser = within(screen.getByRole('region', { name: 'Objective history' }))
  await browser.findByRole('link', { name: 'Retained done-0' })
  expect(browser.queryByRole('link', { name: 'Literal [%_] completed' })).toBeNull()
  expect(test.request.mock.calls.find(call => call[0] === 'organization.history')?.[1]).toMatchObject({
    state: 'current',
    limit: 25
  })
  fireEvent.click(browser.getByRole('button', { name: 'Next history page' }))
  await browser.findByRole('link', { name: 'Literal [%_] completed' })
  expect(browser.queryByRole('link', { name: 'Retained done-0' })).toBeNull()
  fireEvent.click(browser.getByRole('button', { name: 'Previous history page' }))
  await browser.findByRole('link', { name: 'Retained done-0' })
  fireEvent.change(browser.getByRole('textbox', { name: 'Search retained objectives' }), { target: { value: '[%_]' } })
  fireEvent.click(browser.getByRole('button', { name: 'Search history' }))
  await browser.findByRole('link', { name: 'Literal [%_] completed' })
  expect(browser.getAllByRole('listitem')).toHaveLength(1)
  expect(test.request.mock.calls.filter(call => call[0] === 'organization.history').at(-1)?.[1]).toMatchObject({
    query: '[%_]',
    before: undefined
  })
  fireEvent.change(browser.getByRole('textbox', { name: 'Search retained objectives' }), { target: { value: '' } })
  fireEvent.click(browser.getByRole('button', { name: 'Search history' }))
  fireEvent.click(browser.getByRole('button', { name: 'Archived history' }))
  const archivedLink = await browser.findByRole('link', { name: 'Retained archived' })
  const live = test.adapter.getSnapshot()
  fireEvent.click(archivedLink)
  await screen.findByRole('heading', { name: 'Retained archived' })
  expect(test.adapter.getSnapshot()).toBe(live)
  expect(screen.getByText('Accepted result archived')).toBeTruthy()
  fireEvent.click(screen.getByRole('tab', { name: 'Artifacts' }))
  fireEvent.click(screen.getByRole('button', { name: /Original artifact/ }))
  await screen.findByText('Original immutable full evidence')
  expect(screen.getByText('original-digest')).toBeTruthy()
  expect(test.adapter.getSnapshot()).toBe(live)
  fireEvent.keyDown(window.document, { key: 'Escape' })
  fireEvent.click(screen.getByRole('button', { name: 'Restore to current history' }))
  await waitFor(() =>
    expect(test.request.mock.calls.find(call => call[0] === 'organization.archive')?.[1]).toMatchObject({
      id: 'archived',
      archived: false,
      expectedRevision: 0
    })
  )
  await screen.findByRole('button', { name: 'Archive objective' })
  expect(screen.getByText('Completed', { selector: '.eid-status' })).toBeTruthy()
  expect(test.request.mock.calls.some(call => /organization\.(create|retry|resolve|respond)$/.test(call[0]))).toBe(
    false
  )
  test.view.unmount()
})

it('locks repeated archive clicks and retries the same interrupted intent after navigation with its original evidence intact', async () => {
  const test = fixture('/objectives/done-26')
  await screen.findByRole('heading', { name: 'Literal [%_] completed' })
  const pending = deferred<OrganizationSnapshot>()
  test.intercept(method => (method === 'organization.archive' ? pending.promise : undefined))
  const archive = screen.getByRole('button', { name: 'Archive objective' })
  fireEvent.click(archive)
  fireEvent.click(archive)
  await waitFor(() =>
    expect(test.request.mock.calls.filter(call => call[0] === 'organization.archive')).toHaveLength(1)
  )
  expect(archive).toHaveProperty('disabled', true)
  await act(async () => {
    pending.reject(new Error('Acknowledgement lost'))
    await Promise.resolve()
  })
  await screen.findByText('Acknowledgement lost')
  expect(screen.getByText('Accepted result done-26')).toBeTruthy()
  fireEvent.click(screen.getByRole('link', { name: 'Return to objectives' }))
  fireEvent.click(screen.getByRole('link', { name: 'Open old record' }))
  await screen.findByRole('heading', { name: 'Literal [%_] completed' })
  test.intercept(undefined)
  fireEvent.click(screen.getByRole('button', { name: 'Archive objective' }))
  await screen.findByRole('button', { name: 'Restore to current history' })
  const calls = test.request.mock.calls.filter(call => call[0] === 'organization.archive')
  expect(calls).toHaveLength(2)
  expect(calls[1][1]).toEqual(calls[0][1])
  expect(screen.getByText('Accepted result done-26')).toBeTruthy()
  test.view.unmount()
})

it('retains same-owner detail offline, rejects late navigation reads and isolates a new profile', async () => {
  const test = fixture('/objectives/done-26')
  await screen.findByRole('heading', { name: 'Literal [%_] completed' })
  act(() => test.switchScope({ key: 'socket-offline', ownerKey: 'owner-a', connected: false }))
  expect(screen.getByRole('heading', { name: 'Literal [%_] completed' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Archive objective' })).toHaveProperty('disabled', true)
  act(() => test.switchScope({ key: 'socket-reconnected', ownerKey: 'owner-a', connected: true }))
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Archive objective' })).toHaveProperty('disabled', false)
  )
  const oldRead = deferred<OrganizationSnapshot>()
  test.intercept((method, params) =>
    method === 'organization.historyObjective' && params.id === 'archived' ? oldRead.promise : undefined
  )
  fireEvent.click(screen.getByRole('link', { name: 'Open archived record' }))
  await waitFor(() =>
    expect(
      test.request.mock.calls.some(call => call[0] === 'organization.historyObjective' && call[1]?.id === 'archived')
    ).toBe(true)
  )
  fireEvent.click(screen.getByRole('link', { name: 'Open old record' }))
  await screen.findByRole('heading', { name: 'Literal [%_] completed' })
  await act(async () => {
    oldRead.resolve(test.snapshot('archived'))
    await Promise.resolve()
  })
  expect(screen.queryByRole('heading', { name: 'Retained archived' })).toBeNull()
  const profileRead = deferred<OrganizationSnapshot>()
  const previous = test.snapshot('done-26')
  test.intercept(method => (method === 'organization.historyObjective' ? profileRead.promise : undefined))
  fireEvent.click(
    within(screen.getByRole('region', { name: 'Objective history' })).getByRole('button', { name: 'Refresh' })
  )
  await waitFor(() =>
    expect(test.request.mock.calls.filter(call => call[0] === 'organization.historyObjective').length).toBeGreaterThan(
      2
    )
  )
  test.intercept(undefined)
  act(() => test.switchScope({ key: 'socket-b', ownerKey: 'owner-b', connected: true }, [objective('other-owner')]))
  await act(async () => {
    profileRead.resolve(previous)
    await Promise.resolve()
  })
  await waitFor(() => expect(test.adapter.getSnapshot().connection?.ownerScope).toBe('owner-b'))
  expect(screen.queryByRole('heading', { name: 'Literal [%_] completed' })).toBeNull()
  expect(screen.queryByText('Accepted result done-26')).toBeNull()
  expect(screen.queryByRole('button', { name: 'Archive objective' })).toBeNull()
  test.view.unmount()
})

it('ignores late history-filter results and keeps older runtimes free of unsupported history calls', async () => {
  const test = fixture()
  await waitFor(() => expect(test.adapter.getSnapshot().connection?.state).toBe('ready'))
  const oldPage = deferred<HistoryPage>()
  test.intercept((method, params) =>
    method === 'organization.history' && params.state === 'current' ? oldPage.promise : undefined
  )
  fireEvent.click(screen.getByRole('button', { name: 'Objective history' }))
  fireEvent.click(screen.getByRole('button', { name: 'Archived history' }))
  const browser = within(screen.getByRole('region', { name: 'Objective history' }))
  await browser.findByRole('link', { name: 'Retained archived' })
  await act(async () => {
    oldPage.resolve(test.page({ state: 'current' }))
    await Promise.resolve()
  })
  expect(browser.queryByRole('link', { name: 'Retained done-0' })).toBeNull()
  act(() => test.switchScope({ key: 'socket-offline', ownerKey: 'owner-a', connected: false }))
  expect(browser.getByRole('link', { name: 'Retained archived' })).toBeTruthy()
  expect(browser.getByRole('button', { name: 'Restore to current history' })).toHaveProperty('disabled', true)
  test.view.unmount()

  const legacy = fixture('/objectives')
  const oldSnapshot = legacy.snapshot()
  delete oldSnapshot.runtime!.history
  oldSnapshot.runtime!.historyLimited = true
  legacy.intercept(method => (method === 'organization.snapshot' ? Promise.resolve(oldSnapshot) : undefined))
  await act(async () => {
    await legacy.adapter.refresh()
  })
  expect(screen.queryByRole('button', { name: 'Objective history' })).toBeNull()
  expect(screen.getByText(/Older history remains in the backend ledger/)).toBeTruthy()
  expect(legacy.request.mock.calls.some(call => call[0] === 'organization.history')).toBe(false)
  legacy.view.unmount()
})

it('uses authoritative archive blockers and newer exact revisions rather than inventing eligibility from terminal status', async () => {
  const test = fixture('/objectives/done-0')
  await screen.findByRole('heading', { name: 'Retained done-0' })
  const original = test.adapter.getSnapshot()
  const exact = test.snapshot('done-0')
  exact.objectives = exact.objectives.map(item => ({
    ...item,
    history: {
      ...item.history!,
      revision: 1,
      canArchive: false,
      blocker: 'An active dependent still references this objective.'
    }
  }))
  test.intercept(method => (method === 'organization.historyObjective' ? Promise.resolve(exact) : undefined))
  fireEvent.click(
    within(screen.getByRole('region', { name: 'Objective history' })).getByRole('button', { name: 'Refresh' })
  )
  await screen.findByText('An active dependent still references this objective.')
  expect(screen.getByRole('button', { name: 'Archive objective' })).toHaveProperty('disabled', true)
  expect(test.adapter.getSnapshot()).toBe(original)
  expect(screen.getByText('Completed', { selector: '.eid-status' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Archive objective' }))
  expect(test.request.mock.calls.some(call => call[0] === 'organization.archive')).toBe(false)
  test.view.unmount()
})

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Link, MemoryRouter } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway } from './runtime-adapter'
import { $intakeDrafts } from './runtime-intake-drafts'
import type { OrganizationSnapshot } from './types'
import { OrganizationWorkspace } from './workspace'

beforeEach(() => $intakeDrafts.set({}))

const objective = {
  id: `obj_${'a'.repeat(32)}`,
  title: 'Original admitted objective',
  createdAt: '2026-10-10T00:00:00+00:00',
  archived: true
}

const goal = () => screen.getByRole('textbox', { name: 'Objective' })
const create = () => screen.getByRole('button', { name: 'Create objective' })
const check = () => screen.getByRole('button', { name: 'Check submission' })

const calls = (request: ReturnType<typeof vi.fn>, method: string) =>
  request.mock.calls.filter(call => call[0] === method)

const retained = () => Object.values($intakeDrafts.get())[0]

async function fixture() {
  let profile = 'alpha'

  let notify = () => {}
  let snapshotError = false

  let snapshot: OrganizationSnapshot = {
    source: 'runtime',
    objectives: [],
    agents: [],
    tasks: [],
    knowledge: [],
    activity: [],
    requests: [],
    runtime: { profile, state: 'ready', capabilities: [], maxWorkers: 1, scope: 'Supplied text' }
  }

  let createResult = () => Promise.reject(new Error('Creation response lost'))

  let checkResult = (key: string): Promise<unknown> =>
    Promise.resolve({ version: 1, profile, idempotencyKey: key, objective })

  const request = vi.fn((method: string, params: Record<string, unknown>) => {
    if (method === 'organization.snapshot') {
      return snapshotError ? Promise.reject(new Error('Invalid organization configuration')) : Promise.resolve(snapshot)
    }

    if (method === 'organization.create') {
      return createResult()
    }

    if (method === 'organization.checkSubmission') {
      return checkResult(String(params.idempotencyKey))
    }

    throw new Error(`Unexpected method ${method}`)
  })

  const adapter = createRuntimeAdapter({
    request: request as OrganizationGateway['request'],
    getScope: () => ({ key: `socket-${profile}`, ownerKey: `connection/profile-${profile}`, connected: true }),
    subscribeScope(listener) {
      notify = listener

      return () => {}
    }
  })

  render(
    <MemoryRouter initialEntries={['/home']}>
      <Link to="/organization">Leave Command</Link>
      <Link to="/home">Return to Command</Link>
      <OrganizationWorkspace adapter={adapter} />
    </MemoryRouter>
  )
  await waitFor(() => expect(adapter.getSnapshot().connection?.state).toBe('ready'))

  return {
    adapter,
    request,
    async failSnapshot() {
      snapshotError = true
      await act(async () => {
        await adapter.refresh()
      })
    },
    setCheck(value: typeof checkResult) {
      checkResult = value
    },
    setCreate(value: typeof createResult) {
      createResult = value
    },
    async update(runtime: Partial<NonNullable<OrganizationSnapshot['runtime']>>) {
      snapshot = { ...snapshot, runtime: { ...snapshot.runtime!, ...runtime } }
      await act(async () => {
        await adapter.refresh()
      })
    },
    async switchProfile() {
      profile = 'beta'
      snapshot = { ...snapshot, runtime: { ...snapshot.runtime!, profile } }
      await act(async () => {
        notify()
        await adapter.refresh()
      })
    }
  }
}

async function submit() {
  fireEvent.change(goal(), { target: { value: 'Original admitted objective' } })
  fireEvent.click(create())
  await screen.findByText('Creation response lost')
}

it('checks only on explicit action, bypasses retired-project intake validation, and opens an exact archived receipt without another creation', async () => {
  const f = await fixture()
  await f.update({ availableProjects: [{ id: 'project', root: 'root0', recipe: 'tests', team: 'general' }] })
  fireEvent.click(screen.getByRole('checkbox', { name: /project ·/ }))
  await submit()
  const key = retained().idempotencyKey
  await f.update({ availableProjects: [] })
  fireEvent.change(goal(), { target: { value: 'Edited draft that was never admitted' } })
  fireEvent.click(create())
  expect(calls(f.request, 'organization.create')).toHaveLength(1)
  expect(calls(f.request, 'organization.checkSubmission')).toHaveLength(0)
  fireEvent.click(check())
  await screen.findByText(/Submission recorded\./)
  expect(calls(f.request, 'organization.checkSubmission')[0][1]).toEqual({ idempotencyKey: key })
  expect(screen.getByRole('link', { name: objective.title }).getAttribute('href')).toBe(`/objectives/${objective.id}`)
  expect(screen.getByText(/Recorded in profile: alpha/).textContent).toContain('Archived history')
  expect(goal()).toHaveProperty('value', 'Edited draft that was never admitted')
  expect(create()).toHaveProperty('disabled', true)
  fireEvent.submit(goal().closest('form')!)
  expect(calls(f.request, 'organization.create')).toHaveLength(1)
  expect(retained().idempotencyKey).toBe(key)
})

it('disables inspection during creation and coalesces rapid checks; missing remains uncertain with the same retry key', async () => {
  const f = await fixture()
  let reject!: (reason: Error) => void
  f.setCreate(
    () =>
      new Promise((_resolve, fail) => {
        reject = fail
      })
  )
  fireEvent.change(goal(), { target: { value: objective.title } })
  fireEvent.click(create())
  await waitFor(() => expect(calls(f.request, 'organization.create')).toHaveLength(1))
  expect(check()).toHaveProperty('disabled', true)
  fireEvent.click(check())
  expect(calls(f.request, 'organization.checkSubmission')).toHaveLength(0)
  await act(async () => reject(new Error('Creation response lost')))
  let resolve!: (value: unknown) => void
  f.setCheck(
    () =>
      new Promise(done => {
        resolve = done
      })
  )
  const key = retained().idempotencyKey
  fireEvent.click(check())
  fireEvent.click(screen.getByRole('button', { name: 'Checking submission…' }))
  expect(calls(f.request, 'organization.checkSubmission')).toHaveLength(1)
  expect(create()).toHaveProperty('disabled', true)
  await act(async () => resolve({ version: 1, profile: 'alpha', idempotencyKey: key, objective: null }))
  expect(screen.getByText(/No receipt found yet/)).toBeTruthy()
  expect(retained().idempotencyKey).toBe(key)
  f.setCreate(() => Promise.reject(new Error('Retry response lost')))
  fireEvent.click(create())
  await screen.findByText('Retry response lost')
  expect(calls(f.request, 'organization.create')[1][1].idempotencyKey).toBe(key)
})

it.each(['timeout', 'unsupported'])('keeps a %s inspection uncertain and retains the key', async reason => {
  const f = await fixture()
  await submit()
  const key = retained().idempotencyKey
  f.setCheck(() => Promise.reject(new Error(reason)))
  fireEvent.click(check())
  await screen.findByText(reason)
  expect(retained().idempotencyKey).toBe(key)
  expect(retained().receipt).toBeUndefined()
  expect(calls(f.request, 'organization.create')).toHaveLength(1)
  expect(screen.getByRole('link', { name: 'Find an existing objective' }).getAttribute('href')).toBe('/objectives')
})

it.each(['profile', 'key', 'shape'])('rejects a mismatched %s receipt', async mismatch => {
  const f = await fixture()
  await submit()
  const key = retained().idempotencyKey
  f.setCheck(key =>
    Promise.resolve({
      version: 1,
      profile: mismatch === 'profile' ? 'beta' : 'alpha',
      idempotencyKey: mismatch === 'key' ? 'other' : key,
      objective: mismatch === 'shape' ? { ...objective, archived: 'yes' } : objective
    })
  )
  fireEvent.click(check())
  await screen.findByText(/Submission could not be verified/)
  expect(retained().idempotencyKey).toBe(key)
  expect(retained().receipt).toBeUndefined()
  expect(screen.queryByRole('link', { name: objective.title })).toBeNull()
})

it('ignores a late response after navigation without clearing or resubmitting the draft', async () => {
  const f = await fixture()
  await submit()
  const key = retained().idempotencyKey
  let resolve!: (value: unknown) => void
  f.setCheck(
    () =>
      new Promise(done => {
        resolve = done
      })
  )
  fireEvent.click(check())
  fireEvent.click(screen.getByRole('link', { name: 'Leave Command' }))
  await act(async () => resolve({ version: 1, profile: 'alpha', idempotencyKey: key, objective }))
  expect(screen.getByRole('heading', { name: 'Organization' })).toBeTruthy()
  fireEvent.click(screen.getByRole('link', { name: 'Return to Command' }))
  expect(retained().receipt).toBeUndefined()
  expect(retained().checking).toBe(false)
  expect(goal()).toHaveProperty('value', objective.title)
  expect(calls(f.request, 'organization.create')).toHaveLength(1)
})

it('fences a late response after a profile switch and leaves the other profile blank', async () => {
  const f = await fixture()
  await submit()
  const key = retained().idempotencyKey
  let resolve!: (value: unknown) => void
  f.setCheck(
    () =>
      new Promise(done => {
        resolve = done
      })
  )
  fireEvent.click(check())
  await f.switchProfile()
  await act(async () => resolve({ version: 1, profile: 'alpha', idempotencyKey: key, objective }))
  expect(goal()).toHaveProperty('value', '')
  expect(screen.queryByRole('link', { name: objective.title })).toBeNull()
  expect(Object.values($intakeDrafts.get()).every(draft => !draft.receipt)).toBe(true)
  expect(calls(f.request, 'organization.create')).toHaveLength(1)
})

it('can inspect a retained key when current configuration breaks snapshot projection but the verified scope remains connected', async () => {
  const f = await fixture()
  await submit()
  await f.failSnapshot()
  expect(f.adapter.getSnapshot().connection?.state).toBe('error')
  expect(create()).toHaveProperty('disabled', true)
  expect(check()).toHaveProperty('disabled', false)
  fireEvent.click(check())
  await screen.findByText(/Submission recorded\./)
  expect(calls(f.request, 'organization.create')).toHaveLength(1)
  expect(calls(f.request, 'organization.checkSubmission')).toHaveLength(1)
})

it('does not inspect before a profile is verified', async () => {
  const f = await fixture()
  await submit()
  await f.update({ profile: undefined })
  expect(check()).toHaveProperty('disabled', true)
  await expect(f.adapter.checkSubmission!(retained().idempotencyKey!)).rejects.toThrow(
    'Submission could not be verified'
  )
  expect(calls(f.request, 'organization.checkSubmission')).toHaveLength(0)
})

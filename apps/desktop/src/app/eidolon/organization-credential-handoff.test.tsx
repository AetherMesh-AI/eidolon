import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useEffect, useSyncExternalStore } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { organizationCredentialHandoffCopy } from '@/i18n/organization-credential-handoff'
import { organizationCredentialsCopy } from '@/i18n/organization-credentials'

import { CredentialHandoffPanel } from './organization-credential-handoff'
import { useCredentialHandoff } from './organization-credential-handoff-hook'
import { handoffHarness, targetFixture } from './organization-credential-handoff-test-support'
import { deferred } from './organization-credentials-test-support'

vi.mock('./organization-credential-handoff-hook', () => ({ useCredentialHandoff: vi.fn() }))
const copy = organizationCredentialHandoffCopy.en
const common = organizationCredentialsCopy.en
type Harness = ReturnType<typeof handoffHarness>
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  HTMLElement.prototype.scrollIntoView = vi.fn()
  HTMLElement.prototype.hasPointerCapture = () => false
  HTMLElement.prototype.setPointerCapture = vi.fn()
  HTMLElement.prototype.releasePointerCapture = vi.fn()
})
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals() })

function mount(h: Harness) {
  const refresh = vi.fn(async () => {})
  vi.mocked(useCredentialHandoff).mockImplementation(function useFixture(identity: string) {
    const view = useSyncExternalStore(h.client.subscribe, h.client.getSnapshot)
    useEffect(() => { h.client.restore() }, [identity])

    return { client: h.client, view }
  })

  const tree = () => {
    const identity = JSON.stringify([h.scope.key, h.scope.ownerId, h.scope.profile, h.scope.eligible])
    const credentials = { client: { read: refresh }, view: { catalog: h.data, busy: false, intent: null }, identity } as unknown as Parameters<typeof CredentialHandoffPanel>[0]['credentials']

    return <I18nProvider configClient={null} initialLocale="en">{h.scope.eligible && <CredentialHandoffPanel credentials={credentials} key={identity} />}</I18nProvider>
  }

  const rendered = render(tree())

  return { ...rendered, repaint: () => rendered.rerender(tree()), refresh }
}

async function chooseTarget(h: Harness) {
  fireEvent.click(screen.getByRole('button', { name: copy.open }))
  fireEvent.click(screen.getByRole('combobox', { name: copy.entry }))
  fireEvent.click(screen.getByRole('option', { name: h.data.entries[0].name }))
  await waitFor(() => expect(screen.getByRole('combobox', { name: copy.worker })).toBeTruthy())
  fireEvent.click(screen.getByRole('combobox', { name: copy.worker }))
  fireEvent.click(screen.getByRole('option', { name: /request-1 · generation-1/ }))
}

function fillValue() {
  const field = screen.getByLabelText(common.value, { exact: true }) as HTMLInputElement
  fireEvent.change(field, { target: { value: 'SYNTHETIC_PRIVATE_VALUE' } })
  fireEvent.click(screen.getByRole('checkbox', { name: copy.reviewed }))

  return field
}

it('selects the live exact worker before explicit secure review, and distinguishes authorization from commitment', async () => {
  const h = handoffHarness()
  const mounted = mount(h)
  expect(h.request).not.toHaveBeenCalled()
  await chooseTarget(h)
  expect((screen.getByRole('button', { name: copy.authorize }) as HTMLButtonElement).disabled).toBe(true)
  const field = fillValue()
  expect(field.type).toBe('password')
  fireEvent.click(screen.getByRole('button', { name: copy.authorize }))
  expect(field.value).toBe('')
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(screen.getByText(copy.pending)).toBeTruthy()
  expect(screen.queryByText(copy.committed)).toBeNull()
  expect(mounted.refresh).not.toHaveBeenCalled()
  expect(mounted.baseElement.textContent).not.toContain('SYNTHETIC_PRIVATE_VALUE')
  expect(JSON.stringify([...h.values.values()])).not.toContain('SYNTHETIC_PRIVATE_VALUE')
  const intent = h.client.getSnapshot().retained!.intent
  h.statuses.set(intent.operationId, { operationId: intent.operationId, state: 'committed', receipt: {
    action: 'replace_value', entryId: intent.entryId, revision: intent.expectedRevision + 1, version: intent.nextVersion,
    operationId: h.client.getSnapshot().retained!.preparation!.workerOperationId
  } })
  fireEvent.click(screen.getByRole('button', { name: copy.check }))
  await waitFor(() => expect(screen.getByText(copy.committed)).toBeTruthy())
  expect(mounted.refresh).toHaveBeenCalledOnce()
  expect(h.values.size).toBe(0)
})

it('clears uncontrolled input on cancel, disconnect, owner/profile/socket changes, capability loss and unmount', async () => {
  for (const reason of ['cancel', 'disconnect', 'owner', 'profile', 'socket', 'capability', 'unmount']) {
    const h = handoffHarness()
    const mounted = mount(h)
    await chooseTarget(h)
    const field = fillValue()

    if (reason === 'cancel') { fireEvent.keyDown(field, { key: 'Escape' }) }

    if (reason === 'disconnect') { h.scope.eligible = false }

    if (reason === 'owner') { h.scope.ownerId = 'different-owner' }

    if (reason === 'profile') { h.scope.profile = 'other-profile' }

    if (reason === 'socket') { h.scope.key = 'other-socket' }

    if (reason === 'capability') { h.data.secureInputAvailable = false }

    if (reason === 'unmount') { mounted.unmount() } else { mounted.repaint() }

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(field.value).toBe('')
    expect(h.request.mock.calls.filter(([method]) => method.endsWith('.prepareReplacement'))).toHaveLength(0)
    expect(h.values.size).toBe(0)
    cleanup()
  }
})

it('keeps recovery reachable after a lost prepare acknowledgment, and fences the exact operation without replay', async () => {
  const h = handoffHarness()
  mount(h)
  await chooseTarget(h)
  const field = fillValue()
  h.request.mockRejectedValueOnce(new Error('SYNTHETIC_PRIVATE_VALUE'))
  fireEvent.click(screen.getByRole('button', { name: copy.authorize }))
  await waitFor(() => expect(screen.getByRole('button', { name: copy.check })).toBeTruthy())
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(field.value).toBe('')
  const intent = h.client.getSnapshot().retained!.intent
  fireEvent.click(screen.getByRole('button', { name: copy.check }))
  await waitFor(() => expect(screen.getByText(copy.absent)).toBeTruthy())
  expect((screen.getByRole('button', { name: copy.open }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: copy.cancel }))
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: copy.cancel }))
  await waitFor(() => expect(screen.getByText(copy.cancelled)).toBeTruthy())
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(h.request.mock.calls.filter(([method]) => method.endsWith('.prepareReplacement'))).toHaveLength(1)
  expect(h.request.mock.calls.find(([method]) => method.endsWith('.cancelReplacement'))?.[1]).toMatchObject({ operationId: intent.operationId, confirmed: true })
  expect(h.values.size).toBe(0)
  fireEvent.click(screen.getByRole('button', { name: copy.open }))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 700)) })
  expect(screen.getByRole('dialog')).toBeTruthy()
})

it('keeps the chooser mounted while targets load and never invents a worker when none are eligible', async () => {
  const h = handoffHarness()
  const pending = deferred<unknown>()
  h.request.mockReturnValueOnce(pending.promise)
  mount(h)
  fireEvent.click(screen.getByRole('button', { name: copy.open }))
  fireEvent.click(screen.getByRole('combobox', { name: copy.entry }))
  fireEvent.click(screen.getByRole('option', { name: h.data.entries[0].name }))
  expect(screen.getByRole('dialog')).toBeTruthy()
  expect(screen.queryByLabelText(common.value)).toBeNull()
  await act(async () => { pending.resolve({ targets: [] }); await pending.promise })
  expect(screen.getByText(copy.noTargets)).toBeTruthy()
  expect(screen.queryByRole('combobox', { name: copy.worker })).toBeNull()
  expect((screen.getByRole('button', { name: copy.authorize }) as HTMLButtonElement).disabled).toBe(true)
  expect(h.request.mock.calls.map(([method]) => method)).toEqual(['organization.credentials.replacementTargets'])
})

it('clears value and review when the selected exact worker changes', async () => {
  const h = handoffHarness()
  h.request.mockResolvedValueOnce({ targets: [targetFixture(), { ...targetFixture(), generation: 'generation-2', requestId: 'request-2' }] })
  mount(h)
  await chooseTarget(h)
  const field = fillValue()
  fireEvent.click(screen.getByRole('combobox', { name: copy.worker }))
  fireEvent.click(screen.getByRole('option', { name: /request-2 · generation-2/ }))
  expect(field.value).toBe('')
  expect((screen.getByLabelText(common.value) as HTMLInputElement).value).toBe('')
  expect((screen.getByRole('checkbox', { name: copy.reviewed }) as HTMLInputElement).checked).toBe(false)
  expect((screen.getByRole('button', { name: copy.authorize }) as HTMLButtonElement).disabled).toBe(true)
})

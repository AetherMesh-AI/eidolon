import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { TRANSLATIONS } from '@/i18n/catalog'
import { I18nProvider } from '@/i18n/context'
import type { Locale } from '@/i18n/types'

import type { OrganizationAttentionPage } from './runtime-attention-types'
import { requestTypeLabel } from './runtime-request-display'
import { RuntimeRequests } from './runtime-requests'
import type { OrganizationRequest, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

function request(id: string, type: string, extra: Partial<OrganizationRequest> = {}): OrganizationRequest {
  return {
    id,
    type,
    objectiveId: 'objective',
    team: 'writing',
    priority: 3,
    status: 'pending_intervention',
    attempts: 2,
    attentionRevision: 1,
    createdAt: '2026-10-09T00:00:00Z',
    ...extra
  }
}

function open(requests: OrganizationRequest[], locale: Locale = 'en', withAttention = false) {
  const pending = requests.filter(item => item.status === 'pending_intervention')

  const attention: OrganizationAttentionPage = {
    items: pending.map(item => ({
      requestId: item.id,
      objectiveId: item.objectiveId,
      revision: 1,
      seen: false,
      createdAt: item.createdAt,
      updatedAt: item.createdAt
    })),
    total: pending.length,
    unread: pending.length,
    hasMore: false,
    nextCursor: null
  }

  const snapshot: OrganizationSnapshot = {
    source: 'runtime',
    connection: { state: 'ready', scope: 'socket', ownerScope: 'owner' },
    objectives: [],
    agents: [],
    tasks: [],
    activity: [],
    knowledge: [],
    requests,
    ...(withAttention ? { attention } : {})
  }

  const mutation = vi.fn().mockResolvedValue(undefined)
  const retry = vi.fn().mockResolvedValue(undefined)
  const respond = vi.fn().mockResolvedValue(undefined)

  const adapter: RuntimeOrganizationAdapter = {
    mode: 'runtime',
    getSnapshot: () => snapshot,
    subscribe: () => () => undefined,
    refresh: mutation,
    configureOrganization: mutation,
    createObjective: mutation,
    cancelObjective: mutation,
    retryRequest: retry,
    respondRequest: respond,
    resolveRequest: mutation,
    getEvidence: mutation,
    getToolReceipts: async () => [],
    ...(withAttention ? { getAttention: async () => attention, markAttentionSeen: mutation } : {})
  }

  const view = render(
    <I18nProvider configClient={null} initialLocale={locale}>
      <MemoryRouter>
        <RuntimeRequests adapter={adapter} snapshot={snapshot} />
      </MemoryRouter>
    </I18nProvider>
  )

  return { view, mutation, retry, respond }
}

it.each<Locale>(['en', 'ja', 'zh', 'zh-hant', 'ar', 'ru'])(
  'localizes known request presentation in %s without rewriting provenance or authored outcomes',
  async locale => {
    const copy = TRANSLATIONS[locale].organizationWork
    const unknown = 'vendor.future.$&.v2'
    const outcome = 'Keep this exact owner question: ما هو الموعد؟'

    const fixture = open(
      [
        request('question', 'request.question', {
          requestedOutcome: outcome,
          parentRequestId: 'parent',
          allowedResolutions: []
        }),
        request('unknown', unknown),
        request('retry', 'request.hire'),
        request('parent', 'work.draft', { status: 'waiting_response' })
      ],
      locale,
      true
    )

    const inbox = within(screen.getByRole('region', { name: copy.attentionInbox }))
    const inboxRow = await inbox.findByRole('button', { name: `${copy.inspectRequest}: ${copy.requestQuestion}` })
    expect(within(inboxRow).getByText(copy.requestQuestion)).toBeTruthy()
    expect(within(inboxRow).getByText(outcome)).toBeTruthy()
    expect(inbox.getByRole('button', { name: `${copy.inspectRequest}: ${unknown}` })).toBeTruthy()
    fireEvent.click(inboxRow)
    const inboxDetails = within(screen.getByRole('complementary', { name: 'Request details' }))
    expect(inboxDetails.getByText(copy.recordedRequestType).nextElementSibling?.textContent).toBe('request.question')
    fireEvent.click(inboxDetails.getByRole('button', { name: 'Close request details' }))
    fireEvent.click(screen.getByRole('button', { name: copy.attentionQueue }))

    expect(screen.getByRole('button', { name: copy.retryRequest.replace('{type}', copy.requestHire) })).toBeTruthy()
    expect(screen.getByRole('button', { name: copy.retryRequest.replace('{type}', () => unknown) })).toBeTruthy()
    const label = copy.requestQuestion
    const row = screen.getByRole('button', { name: `${copy.inspectRequest}: ${label}` })
    expect(within(row).getByText(label)).toBeTruthy()
    expect(within(row).getByText(outcome)).toBeTruthy()
    expect(row.textContent).toContain(`${copy.attempts}: 2`)
    expect(within(row).queryByText('request.question')).toBeNull()
    expect(screen.getByRole('button', { name: `${copy.inspectRequest}: ${unknown}` })).toBeTruthy()
    expect(requestTypeLabel('toString', copy)).toBe('toString')
    expect(requestTypeLabel('__proto__', copy)).toBe('__proto__')
    const filter = screen.getByRole('combobox', { name: copy.type })
    expect(within(filter).getByRole('option', { name: label })).toHaveProperty('value', 'request.question')
    expect(within(filter).getByRole('option', { name: unknown })).toHaveProperty('value', unknown)
    fireEvent.change(filter, { target: { value: 'request.question' } })
    expect(screen.queryByRole('button', { name: `${copy.inspectRequest}: ${unknown}` })).toBeNull()
    fireEvent.change(filter, { target: { value: 'all' } })
    fireEvent.change(screen.getByRole('combobox', { name: copy.groupBy }), { target: { value: 'type' } })
    expect(screen.getByRole('heading', { name: label, level: 3 })).toBeTruthy()
    expect(screen.getByRole('heading', { name: unknown, level: 3 })).toBeTruthy()

    for (const query of [label, 'request.question', outcome]) {
      fireEvent.change(screen.getByRole('textbox', { name: copy.search }), { target: { value: query } })
      expect(screen.getByRole('button', { name: `${copy.inspectRequest}: ${label}` })).toBeTruthy()
      expect(screen.queryByRole('button', { name: `${copy.inspectRequest}: ${unknown}` })).toBeNull()
    }

    fireEvent.click(screen.getByRole('button', { name: `${copy.inspectRequest}: ${label}` }))
    const inspector = within(screen.getByRole('complementary', { name: 'Request details' }))
    expect(inspector.getByRole('heading', { name: label, level: 2 })).toBeTruthy()
    expect(inspector.getByText(copy.recordedRequestType).nextElementSibling?.textContent).toBe('request.question')
    expect(inspector.getByText(outcome)).toBeTruthy()
    expect(inspector.getByRole('button', { name: copy.openParentRequest }).textContent).toBe(copy.workDraft)
    fireEvent.click(inspector.getByRole('button', { name: copy.openParentRequest }))
    expect(
      within(screen.getByRole('complementary', { name: 'Request details' })).getByRole('heading', {
        name: copy.workDraft,
        level: 2
      })
    ).toBeTruthy()
    expect(fixture.mutation).not.toHaveBeenCalled()
    expect(fixture.retry).not.toHaveBeenCalled()
    expect(fixture.respond).not.toHaveBeenCalled()
    fixture.view.unmount()
  }
)

it('keeps legacy retry and authoritative response permissions separate with exact mutation payloads', async () => {
  const copy = TRANSLATIONS.en.organizationWork

  const fixture = open([
    request('legacy', 'work.draft'),
    request('empty', 'request.decision', { allowedResolutions: [] }),
    request('merge', 'request.merge'),
    request('answer', 'request.question', {
      allowedResolutions: [
        { action: 'answer_request', label: 'Backend option', requiresText: true, requiresEvidence: false }
      ]
    })
  ])

  const retryText = (type: string) => copy.retryRequest.replace('{type}', requestTypeLabel(type, copy))
  expect(screen.queryByRole('button', { name: retryText('request.decision') })).toBeNull()
  expect(screen.queryByRole('button', { name: retryText('request.merge') })).toBeNull()
  expect(screen.queryByRole('button', { name: retryText('request.question') })).toBeNull()
  let finish!: () => void
  fixture.retry.mockImplementation(
    () =>
      new Promise<void>(resolve => {
        finish = resolve
      })
  )
  fireEvent.click(screen.getByRole('button', { name: retryText('work.draft') }))
  expect(screen.getByRole('button', { name: copy.retryingRequest })).toHaveProperty('disabled', true)
  expect(fixture.retry).toHaveBeenCalledExactlyOnceWith('legacy')
  await act(async () => {
    finish()
  })
  expect(screen.getByRole('button', { name: retryText('work.draft') })).toHaveProperty('disabled', false)

  fireEvent.click(screen.getByRole('button', { name: `${copy.inspectRequest}: ${copy.requestQuestion}` }))
  const inspector = within(screen.getByRole('complementary', { name: 'Request details' }))
  expect(inspector.getByRole('button', { name: copy.submit })).toHaveProperty('disabled', true)
  fireEvent.change(inspector.getByRole('textbox', { name: copy.response }), {
    target: { value: ' Exact owner response. ' }
  })
  fireEvent.click(inspector.getByRole('button', { name: copy.submit }))
  fireEvent.click(inspector.getByRole('button', { name: copy.submitting }))
  expect(fixture.respond).toHaveBeenCalledExactlyOnceWith({
    id: 'answer',
    text: ' Exact owner response. ',
    decision: 'answered'
  })
  expect(fixture.mutation).not.toHaveBeenCalled()
  await act(async () => {})
  fixture.view.unmount()
})

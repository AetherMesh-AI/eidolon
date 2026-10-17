import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { TRANSLATIONS } from '@/i18n/catalog'
import { I18nProvider } from '@/i18n/context'
import { RuntimeArtifact } from './runtime-artifact'
import type { OrganizationArtifact, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

const copy = TRANSLATIONS.en.organizationRuntime
const artifact: OrganizationArtifact = { id: 'evidence-a', content: 'Synthetic evidence', sha256: 'digest', summary: 'Synthetic', createdAt: '2026-10-16T00:00:00Z', objectiveId: 'goal-a', taskId: null }
function setup() {
  const reads: { resolve(value: OrganizationArtifact): void; reject(error: Error): void }[] = []
  const getEvidence = vi.fn(() => new Promise<OrganizationArtifact>((resolve, reject) => reads.push({ resolve, reject })))
  const adapter = { getEvidence } as unknown as RuntimeOrganizationAdapter
  let snapshot: OrganizationSnapshot = { source: 'runtime', connection: { state: 'ready', scope: 'socket-a', ownerScope: 'owner-a' }, objectives: [], agents: [], tasks: [], requests: [], activity: [], knowledge: [] }
  const draw = () => <I18nProvider configClient={null} initialLocale="en"><button>Outside</button><RuntimeArtifact adapter={adapter} evidenceId="evidence-a" onClose={vi.fn()} snapshot={snapshot} title={'LongTitle'.repeat(80)} /></I18nProvider>
  const view = render(draw())
  return { view, reads, getEvidence, update(change: Partial<OrganizationSnapshot>) { snapshot = { ...snapshot, ...change }; view.rerender(draw()) } }
}

it.each(['refresh', 'retry'])('retains focused %s throughout delayed reads without duplicate requests', async action => {
  const user = userEvent.setup()
  const f = setup()
  await act(async () => { if (action === 'refresh') { f.reads[0].resolve(artifact) } else { f.reads[0].reject(new Error('Read failed')) } })
  const button = screen.getByRole('button', { name: action === 'refresh' ? copy.artifactRefresh : copy.artifactRetry })
  act(() => button.focus())
  await user.keyboard(action === 'refresh' ? '{Enter}' : ' ')
  expect(document.activeElement).toBe(button)
  expect(button.isConnected).toBe(true)
  expect(button.getAttribute('aria-disabled')).toBe('true')
  fireEvent.click(button)
  await user.keyboard('{Enter} ')
  expect(f.getEvidence).toHaveBeenCalledTimes(2)
  await act(async () => f.reads[1].resolve(artifact))
  expect(screen.getByRole('button', { name: copy.artifactRefresh })).toBe(button)
  expect(document.activeElement).toBe(button)
})

it('does not steal focus after background reads or expose old-owner completion', async () => {
  const f = setup()
  await act(async () => f.reads[0].resolve(artifact))
  fireEvent.click(screen.getByRole('button', { name: copy.artifactRefresh }))
  const outside = screen.getByRole('button', { name: 'Outside' })
  act(() => outside.focus())
  f.update({ connection: { state: 'ready', scope: 'socket-b', ownerScope: 'owner-b' } })
  await act(async () => f.reads[1].resolve({ ...artifact, content: 'Old owner content' }))
  expect(screen.queryByText('Old owner content')).toBeNull()
  expect(document.activeElement).toBe(outside)
  await act(async () => f.reads[2].resolve({ ...artifact, content: 'New owner content' }))
  expect(screen.getByText('New owner content')).toBeTruthy()
  expect(document.activeElement).toBe(outside)
  f.update({ connection: { state: 'connecting', scope: 'socket-b', ownerScope: 'owner-b' } })
  await act(async () => f.reads[3].reject(new Error('Background read failed')))
  expect(document.activeElement).toBe(outside)
})

it('wraps long unbroken Inspector titles and evidence errors in either direction', async () => {
  const f = setup()
  await act(async () => f.reads[0].reject(new Error('LongError'.repeat(80))))
  const style = document.createElement('style')
  style.textContent = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'eidolon.css'), 'utf8')
  document.head.append(style)
  try {
    for (const dir of ['ltr', 'rtl']) {
      f.view.container.dir = dir
      for (const element of [screen.getByRole('heading', { level: 2 }), screen.getByRole('alert')]) {
        expect(getComputedStyle(element).overflowWrap).toBe('anywhere')
      }
    }
  } finally { style.remove() }
})

it.each(['resolve', 'reject'] as const)('keeps the recovery action after failure and ignores unmounted %s', async completion => {
  const f = setup()
  await act(async () => f.reads[0].resolve(artifact))
  const button = screen.getByRole('button', { name: copy.artifactRefresh })
  act(() => button.focus())
  fireEvent.click(button)
  expect(screen.queryByText(artifact.content)).toBeNull()
  await act(async () => f.reads[1].reject(new Error('Refresh failed')))
  expect(screen.getByRole('button', { name: copy.artifactRetry })).toBe(button)
  expect(document.activeElement).toBe(button)
  expect(screen.queryByText(artifact.content)).toBeNull()
  fireEvent.click(button)
  const outside = screen.getByRole('button', { name: 'Outside' })
  act(() => outside.focus())
  f.view.unmount()
  await act(async () => {
    if (completion === 'resolve') { f.reads[2].resolve(artifact) } else { f.reads[2].reject(new Error('Late failure')) }
  })
  expect(screen.queryByText(artifact.content)).toBeNull()
})

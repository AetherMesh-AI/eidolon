import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { act, fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import type { OrganizationCreationClient, OrganizationModelSetupClient } from './organization-creation-client'
import { OrganizationCreationDialog } from './organization-creation-dialog'
import { OrganizationModelSetup } from './organization-model-setup'

it.each(['creation', 'model'] as const)(
  'owns immediate keyboard ring and scroll clearance in the %s portal',
  async kind => {
    const view = { stage: 'ready', busy: false, result: null, intent: null, error: null, canRetry: false } as const
    const client = { getSnapshot: () => view, subscribe: () => () => {}, load: vi.fn(), isCurrent: () => true }
    const style = globalThis.document.createElement('style')
    style.textContent = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'organization-dialog.css'), 'utf8')
    globalThis.document.head.append(style)

    const mounted = render(
      kind === 'creation' ? (
        <OrganizationCreationDialog
          client={client as unknown as OrganizationCreationClient}
          onClose={vi.fn()}
          onSetup={vi.fn()}
        />
      ) : (
        <OrganizationModelSetup client={client as unknown as OrganizationModelSetupClient} onClose={vi.fn()} />
      )
    )

    try {
      const dialog = screen.getByRole('dialog')
      const field = screen.getByLabelText(kind === 'creation' ? 'Organization name' : 'OpenAI API model ID')
      const close = screen.getByRole('button', { name: 'Close' })
      fireEvent.change(field, { target: { value: 'Retained explicit draft' } })
      expect(dialog.classList.contains('eid-organization-dialog')).toBe(true)
      // The shared shell supplies duration-200. Its default transition-property
      // must not interpolate viewport-driven position or size while tabbing.
      expect(getComputedStyle(dialog).transitionProperty).toBe(
        'color, background-color, border-color, box-shadow, opacity'
      )
      const body = dialog.querySelector('.eid-organization-dialog-body')!
      const scroller = field.closest('.eid-organization-dialog-scroll')!
      expect(getComputedStyle(body).overflow).toBe('hidden')
      expect(getComputedStyle(scroller).overflowY).toBe('auto')
      expect(getComputedStyle(scroller).getPropertyValue('scroll-padding')).toBe('7px')
      expect(scroller.contains(dialog.querySelector('[data-slot=dialog-title]'))).toBe(false)

      for (const target of [field, close]) {
        act(() => target.focus())
        expect(target.matches(':focus-visible')).toBe(true)
        expect(getComputedStyle(target).outline).toBe('3px solid var(--ui-accent)')
        expect(getComputedStyle(target).outlineOffset).toBe('3px')
        expect(getComputedStyle(target).getPropertyValue('scroll-margin')).toBe('7px')
        expect(getComputedStyle(target).transitionProperty).not.toMatch(/all|outline/)
      }

      expect((field as HTMLInputElement).value).toBe('Retained explicit draft')
      expect(getComputedStyle(close).minInlineSize).toBe('2.25rem')
      expect(getComputedStyle(close).minBlockSize).toBe('2.25rem')
      expect(getComputedStyle(close).right).toBe('auto')
      expect(getComputedStyle(close).insetInlineEnd).toBe('0.625rem')
      expect(getComputedStyle(dialog.querySelector('[data-slot=dialog-title]')!).paddingInlineEnd).toBe('2.75rem')
    } finally {
      mounted.unmount()
      style.remove()
    }
  }
)

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createElement } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SendDiagnosticsHost } from '@/components/send-diagnostics-dialog'
import { en } from '@/i18n/en'
import { $gateway } from '@/store/gateway'
import { $sendDiagnostics, dismissSendDiagnostics, requestSendDiagnostics } from '@/store/send-diagnostics'

const originalDesktop = window.hermesDesktop
const originalGateway = $gateway.get()

afterEach(() => {
  cleanup()
  dismissSendDiagnostics()
  $gateway.set(originalGateway)
  Object.defineProperty(window, 'hermesDesktop', { configurable: true, value: originalDesktop })
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('local diagnostics notice', () => {
  it('copies error details without collecting logs, uploading, or offering upstream support links', async () => {
    const request = vi.fn()
    const fetch = vi.fn()
    const getRecentLogs = vi.fn()
    const writeClipboard = vi.fn().mockResolvedValue(undefined)

    $gateway.set({ request } as never)
    vi.stubGlobal('fetch', fetch)
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: { getRecentLogs, writeClipboard }
    })

    requestSendDiagnostics('layer: provider\ncode: stream_drop')
    render(createElement(SendDiagnosticsHost))

    expect(screen.getByRole('dialog').textContent).toContain(en.sendDiagnostics.privacyNotice)
    expect(screen.queryByRole('button', { name: /upload/i })).toBeNull()
    expect(screen.queryAllByRole('link')).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: en.assistant.thread.errorCopyDiagnostics }))
    await waitFor(() => expect(writeClipboard).toHaveBeenCalledWith('layer: provider\ncode: stream_drop'))

    expect(request).not.toHaveBeenCalled()
    expect(fetch).not.toHaveBeenCalled()
    expect(getRecentLogs).not.toHaveBeenCalled()
  })

  it('dismisses with Close or Escape, and reopening only exposes the latest local details', () => {
    requestSendDiagnostics('old error')
    render(createElement(SendDiagnosticsHost))
    fireEvent.click(screen.getAllByRole('button', { name: en.sendDiagnostics.close }).at(-1)!)
    expect($sendDiagnostics.get()).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()

    act(() => requestSendDiagnostics('new error'))
    expect($sendDiagnostics.get()?.errorContext).toBe('new error')
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape', code: 'Escape' })
    expect($sendDiagnostics.get()).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()

    act(() => requestSendDiagnostics())
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.queryByRole('button', { name: en.assistant.thread.errorCopyDiagnostics })).toBeNull()
  })
})

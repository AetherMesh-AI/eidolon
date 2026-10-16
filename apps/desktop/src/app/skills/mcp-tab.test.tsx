import { QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useLayoutEffect, useRef, useState } from 'react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { CodeEditorApi } from '@/components/chat/code-editor'
import type * as HermesApi from '@/eidolon'
import { probeCache } from '@/lib/mcp-probe-cache'
import { queryClient } from '@/lib/query-client'

import { observeMasterDetailWidth } from '../responsive-master-detail.test-helpers'

const getConfig = vi.fn()
const saveMcpServers = vi.fn()
const testMcpServer = vi.fn()
const installMcpCatalogEntry = vi.fn()
const reload = vi.fn().mockResolvedValue({})

vi.mock('@/eidolon', async importOriginal => ({
  ...(await importOriginal<typeof HermesApi>()),
  getHermesConfigRecord: (...args: unknown[]) => getConfig(...args),
  saveMcpServers: (...args: unknown[]) => saveMcpServers(...args),
  testMcpServer: (...args: unknown[]) => testMcpServer(...args),
  installMcpCatalogEntry: (...args: unknown[]) => installMcpCatalogEntry(...args),
  getMcpCatalog: vi.fn().mockResolvedValue({ entries: [] }),
  getUsageAnalytics: vi.fn().mockResolvedValue({ tools: [] }),
  getLogs: vi.fn().mockResolvedValue({ lines: [] })
}))

vi.mock('@/store/notifications', () => ({ notify: vi.fn(), notifyError: vi.fn() }))

// Keep the editor's mounted draft and imperative cursor contract, without
// CodeMirror's browser geometry requirements in jsdom.
vi.mock('@/components/chat/code-editor', () => ({
  CodeEditor: function Editor({
    apiRef,
    initialValue,
    onChange,
    onCursorChange
  }: {
    apiRef: { current: CodeEditorApi | null }
    initialValue: string
    onChange: (text: string) => void
    onCursorChange: (cursor: number) => void
  }) {
    const [value, setValue] = useState(initialValue)
    const ref = useRef<HTMLTextAreaElement>(null)
    useLayoutEffect(() => {
      apiRef.current = {
        formatJson: () => ({ ok: true }),
        setCursor: cursor => {
          ref.current?.focus()
          onCursorChange(cursor)
        }
      }
    }, [apiRef, onCursorChange])

    return (
      <textarea
        aria-label="MCP document"
        onChange={event => {
          setValue(event.target.value)
          onChange(event.target.value)
        }}
        ref={ref}
        value={value}
      />
    )
  }
}))

const serverName = 'long-server-name-with-a-distinct-final-identity'
const scope = { connectionId: 'fixture-remote', profile: 'fixture-agent' }

async function renderMcp(gateway: HermesApi.HermesGateway | null = null, waitForEditor = true) {
  const { McpTab } = await import('./mcp-tab')
  let result: ReturnType<typeof render>
  await act(async () => {
    result = render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <McpTab gateway={gateway} profile={scope} />
        </MemoryRouter>
      </QueryClientProvider>
    )
  })

  if (waitForEditor) {
    await screen.findByLabelText('MCP document')
  }

  return result!
}

beforeEach(() => {
  queryClient.setQueryDefaults(['hermes-config-record'], { retry: false })
  getConfig.mockResolvedValue({
    mcp_servers: {
      [serverName]: { command: 'fixture-server', args: ['--fixture'], enabled: false }
    }
  })
  saveMcpServers.mockResolvedValue({ ok: true })
  testMcpServer.mockResolvedValue({ ok: true, tools: [] })
})

afterEach(() => {
  cleanup()
  queryClient.clear()
  probeCache.clear()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

describe('compact MCP workspace', { timeout: 60_000 }, () => {
  it('preserves the selected server, dirty editor, list scroll and Back focus without writes on navigation', async () => {
    const resize = observeMasterDetailWidth()
    const { container } = await renderMcp({ request: reload } as unknown as HermesApi.HermesGateway)
    resize(400)
    const list = container.querySelector<HTMLElement>('[data-pane="list"]')!
    const detail = container.querySelector<HTMLElement>('[data-pane="detail"]')!
    const scroll = list.querySelector<HTMLElement>('.eid-detail-scroll')!
    const row = screen.getByRole('button', { name: /Long-Server-Name-With-A-Distinct-Final-Identity/i })
    scroll.scrollTop = 86
    row.focus()
    fireEvent.click(row)
    expect(list.hidden).toBe(true)
    expect(detail.hidden).toBe(false)
    expect(window.document.activeElement).toBe(screen.getByRole('button', { name: 'Back' }))
    const editor = screen.getByLabelText('MCP document') as HTMLTextAreaElement
    const draft = editor.value.replace('--fixture', '--keep-this-draft')
    fireEvent.change(editor, { target: { value: draft } })
    resize(1100)
    editor.focus()
    resize(700)
    expect(window.document.activeElement).toBe(editor)
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(window.document.activeElement).toBe(row)
    expect(row.getAttribute('aria-current')).toBe('true')
    expect(scroll.scrollTop).toBe(86)
    row.focus()
    fireEvent.click(row)
    expect(screen.getByLabelText('MCP document')).toBe(editor)
    expect(editor.value).toBe(draft)
    expect(saveMcpServers).not.toHaveBeenCalled()
    expect(reload).not.toHaveBeenCalled()
    expect(installMcpCatalogEntry).not.toHaveBeenCalled()
  })

  it('keeps a failed config read retryable without saving or reloading MCP', async () => {
    getConfig.mockRejectedValueOnce(new Error('Fixture config unavailable'))
    await renderMcp(null, false)
    expect(await screen.findByText('Fixture config unavailable')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Reload MCP' }))
    await screen.findByLabelText('MCP document')
    expect(saveMcpServers).not.toHaveBeenCalled()
    expect(reload).not.toHaveBeenCalled()
  })

  it('keeps an empty compact fleet actionable and saves only on explicit Save to the pinned scope', async () => {
    getConfig.mockResolvedValue({ mcp_servers: {} })
    const resize = observeMasterDetailWidth()
    await renderMcp()
    resize(400)
    fireEvent.click(screen.getByRole('button', { name: /New server/i }))
    const editor = (await screen.findByLabelText('MCP document')) as HTMLTextAreaElement
    expect(editor.value).toContain('my-server')
    expect(saveMcpServers).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(saveMcpServers).toHaveBeenCalled())
    expect(saveMcpServers.mock.calls[0][1]).toEqual(scope)
    expect(reload).not.toHaveBeenCalled()
  })
})

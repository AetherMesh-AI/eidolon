import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CachedShikiBlock from '@/components/chat/shiki-block'
import { highlightCache } from '@/components/chat/shiki-highlight-cache'
import type { HermesReadFileTextResult } from '@/global'
import { I18nProvider } from '@/i18n'
import type { PreviewTarget } from '@/store/preview'
import { $connection } from '@/store/session'

import { LocalFilePreview, MarkdownPreview, PreviewModeSwitcher, SourceView } from './preview-file'

// Exercise the real lazy/cache/fallback layers; only the WASM engine boundary
// is replaced, as in shiki-block.test.tsx. Native tests use real Shiki.
const { codeToHtml } = vi.hoisted(() => ({ codeToHtml: vi.fn<(code: string) => string | Promise<string>>() }))
const { readFileText, gitRoot, fileDiff } = vi.hoisted(() => ({
  readFileText: vi.fn<(path: string) => Promise<HermesReadFileTextResult>>(),
  gitRoot: vi.fn<(path: string) => Promise<string | null>>(),
  fileDiff: vi.fn<(root: string, path: string) => Promise<string>>()
}))

vi.mock('@/lib/desktop-fs', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/desktop-fs')>(),
  readDesktopFileText: readFileText,
  desktopGitRoot: gitRoot,
  desktopFileDiff: fileDiff
}))

vi.mock('shiki', () => ({
  bundledLanguages: { json: 'json-loader', text: 'text-loader' },
  getSingletonHighlighter: vi.fn(async () => ({
    codeToHtml: (code: string) => codeToHtml(code),
    getLoadedLanguages: () => ['json', 'text'],
    loadLanguage: vi.fn(async () => undefined),
    loadTheme: vi.fn(async () => undefined)
  }))
}))

vi.mock('shiki/engine/oniguruma', () => ({ createOnigurumaEngine: vi.fn(() => ({}) as never) }))

function highlightedLines(code: string) {
  // Match Shiki's existing CRLF-to-LF display conversion before making spans.
  const escaped = code.replace(/\r\n/g, '\n').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

  return `<pre class="shiki"><code>${escaped.split('\n').map(line => `<span class="line">${line}</span>`).join('\n')}</code></pre>`
}

beforeEach(() => {
  highlightCache.clear()
  codeToHtml.mockReset().mockImplementation(highlightedLines)
})

// Behavior tests for the .md file preview renderer: input markdown goes
// through normalizeFilePreviewMath -> Streamdown (+ KaTeX math plugin) and must
// come out as real rendered elements, matching what the chat transcript
// renderer produces. Guards the regression where the preview was a bare
// Streamdown pass with no math plugin and no table/img/a components.
describe('MarkdownPreview', () => {
  afterEach(() => {
    cleanup()
  })

  it('renders block and inline math through KaTeX', () => {
    // KaTeX marks its output; raw "$" delimiters must be gone.
    const { container } = render(
      <MarkdownPreview
        text={'Formula:\n\n$$\nx = \\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}\n$$\n\nInline $a^2 + b^2 = c^2$ too.'}
      />
    )

    expect(container.querySelector('.katex')).not.toBeNull()
    expect(screen.queryByText(/\$\$/)).toBeNull()
  })

  it('renders GFM tables with header and body cells', () => {
    const { container } = render(<MarkdownPreview text={'| h1 | h2 |\n| --- | --- |\n| a | b |'} />)

    const table = container.querySelector('table')
    expect(table).not.toBeNull()
    expect(table?.querySelector('thead th')?.textContent).toBe('h1')
    expect(table?.querySelector('tbody td')?.textContent).toBe('a')
  })

  it('renders images with alt text', () => {
    const { container } = render(<MarkdownPreview text={'![a chart](https://example.com/chart.png)'} />)

    const img = container.querySelector('img')
    expect(img?.getAttribute('alt')).toBe('a chart')
    expect(img?.getAttribute('src')).toBe('https://example.com/chart.png')
  })

  it('renders external links to open in a new tab safely', () => {
    const { container } = render(<MarkdownPreview text={'[docs](https://example.com/docs)'} />)

    const anchor = container.querySelector('a')
    expect(anchor?.getAttribute('href')).toBe('https://example.com/docs')
    expect(anchor?.getAttribute('target')).toBe('_blank')
    expect(anchor?.getAttribute('rel')).toBe('noopener noreferrer')
  })
})

describe('SourceView reading direction', () => {
  afterEach(cleanup)

  it('isolates source and gutters from RTL chrome without changing selectable text or line references', () => {
    const text = '{\n  "message": "مرحبا"\n}'
    const filePath = '/synthetic/example.json'

    const { container } = render(
      <div dir="rtl">
        <button>المصدر</button>
        <SourceView filePath={filePath} language="json" text={text} />
      </div>
    )

    const code = container.querySelector('.preview-source-code')!
    expect(code.closest('[dir]')?.getAttribute('dir')).toBe('ltr')
    expect(screen.getByRole('button', { name: 'المصدر' }).closest('[dir]')?.getAttribute('dir')).toBe('rtl')
    const range = code.ownerDocument.createRange()
    range.selectNodeContents(code)
    expect(range.toString()).toBe(text)
    const lines = container.querySelectorAll('[draggable="true"]')
    expect(lines[0].closest('[dir]')?.getAttribute('dir')).toBe('ltr')
    fireEvent.click(lines[1])
    fireEvent.click(lines[2], { shiftKey: true })
    const dataTransfer = { setData: vi.fn(), effectAllowed: '' }
    fireEvent.dragStart(lines[1], { dataTransfer })
    expect(dataTransfer.setData).toHaveBeenCalledWith('text/plain', `${filePath}:2-3`)
    expect(dataTransfer.effectAllowed).toBe('copy')
    expect(range.toString()).toBe(text)
  })
})

describe('SourceView chunk delimiters', () => {
  afterEach(cleanup)

  it('keeps the real LF at a blank 200-line source seam without adding a final delimiter', () => {
    const text = `${'// before seam\n'.repeat(199)}\n// بعد الحد: café 🧪\n\n`
    const { container } = render(<SourceView language="text" text={text} />)
    const chunks = [...container.querySelectorAll('.preview-source-code')]
    expect(chunks).toHaveLength(2)
    expect(chunks.map(chunk => chunk.textContent).join('')).toBe(text)
  })
})

describe('SourceView highlighter fallback phases', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('keeps literal source through pending/error/retry and immediately replaces changed source without replacing its host', async () => {
    const text = `${'// before seam\n'.repeat(199)}\n// بعد الحد: café 🧪\n\n`
    let rejectHighlight!: (error: Error) => void
    const pending = new Promise<string>((_resolve, reject) => { rejectHighlight = reject })
    codeToHtml.mockImplementation(() => pending)
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const { container, rerender, unmount } = render(<SourceView language="text" text={text} />)
    const renderedText = () => [...container.querySelectorAll('.preview-source-code')].map(chunk => chunk.textContent).join('')

    expect(renderedText()).toBe(text)
    expect(container.querySelectorAll('[data-source-fallback]')).toHaveLength(2)
    // Resolve React.lazy without advancing the highlight debounce.
    await act(async () => { await import('@/components/chat/shiki-block') })
    await act(async () => { await vi.advanceTimersByTimeAsync(119) })
    expect(codeToHtml).not.toHaveBeenCalled()
    rerender(<SourceView language="text" text={text} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    expect(codeToHtml).toHaveBeenCalledTimes(2)
    expect(renderedText()).toBe(text)
    await act(async () => { rejectHighlight(new Error('controlled highlighter failure')) })
    expect(error).toHaveBeenCalledTimes(2)
    expect(renderedText()).toBe(text)
    expect(container.querySelectorAll('[data-source-fallback]')).toHaveLength(2)
    expect(highlightCache.size).toBe(0)
    unmount()

    codeToHtml.mockImplementation(highlightedLines)
    const retry = render(<SourceView language="text" text={text} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(120) })
    expect(codeToHtml).toHaveBeenCalledTimes(4)
    expect(retry.container.querySelectorAll('[data-source-fallback]')).toHaveLength(0)
    expect([...retry.container.querySelectorAll('.preview-source-code')].map(chunk => chunk.textContent).join('')).toBe(text)
    const host = retry.container.querySelector('.preview-source-code')
    const scroller = host?.closest('[dir]')
    const changed = '<tag>& بعد التغيير: café 🧪\r\n\n'
    retry.rerender(<SourceView language="text" text={changed} />)
    expect(retry.container.querySelector('.preview-source-code')).toBe(host)
    expect(host?.closest('[dir]')).toBe(scroller)
    expect(host?.textContent).toBe(changed)
    expect(host?.querySelector('[data-source-fallback]')).not.toBeNull()
    expect(host?.querySelector('tag')).toBeNull()
    await act(async () => { await vi.advanceTimersByTimeAsync(120) })
    // HTML parsing/highlighting normalizes CRLF; only the pending fallback is raw.
    expect(host?.textContent).toBe(changed.replace(/\r\n/g, '\n'))
    expect(host?.querySelector('[data-source-fallback]')).toBeNull()
  })

  it('leaves default pending/error rendering and successful warm-cache reuse unchanged', async () => {
    const code = '<default>& مرحبا\n\n'
    codeToHtml.mockRejectedValueOnce(new Error('controlled default failure'))
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const first = render(<CachedShikiBlock code={code} language="text" />)
    expect(first.container.textContent).toBe('')
    await act(async () => { await vi.advanceTimersByTimeAsync(119) })
    expect(first.container.textContent).toBe('')
    expect(codeToHtml).not.toHaveBeenCalled()
    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    expect(first.container.textContent).toBe(code)
    expect(first.container.querySelector('default')).toBeNull()
    expect(first.container.querySelectorAll('.line')).toHaveLength(0)
    expect(highlightCache.size).toBe(0)
    first.unmount()

    const retry = render(<CachedShikiBlock code={code} language="text" />)
    expect(retry.container.textContent).toBe('')
    await act(async () => { await vi.advanceTimersByTimeAsync(120) })
    expect(codeToHtml).toHaveBeenCalledTimes(2)
    expect(retry.container.textContent).toBe(code)
    retry.unmount()
    const warm = render(<CachedShikiBlock code={code} language="text" />)
    expect(warm.container.textContent).toBe(code)
    expect(codeToHtml).toHaveBeenCalledTimes(2)
  })
})

describe('PreviewModeSwitcher controls', () => {
  afterEach(cleanup)

  it('keeps mode selection and trailing actions independent in RTL chrome', () => {
    const onSelect = vi.fn()
    const onCancel = vi.fn()
    const onSave = vi.fn()

    const { container } = render(
      <div dir="rtl">
        <PreviewModeSwitcher
          active="source"
          modes={['rendered', 'source', 'diff']}
          onSelect={onSelect}
          trailing={<><button onClick={onCancel}>Cancel</button><button disabled onClick={onSave}>Save</button></>}
        />
      </div>
    )

    const source = screen.getByRole('button', { name: /^source$/i })
    const cancel = screen.getByRole('button', { name: 'Cancel' })
    expect(source.closest('[dir]')?.getAttribute('dir')).toBe('rtl')
    expect(cancel.closest('[dir]')?.getAttribute('dir')).toBe('rtl')
    source.focus()
    expect(container.ownerDocument.activeElement).toBe(source)
    fireEvent.click(source)
    expect(onSelect).toHaveBeenCalledWith('source')
    cancel.focus()
    expect(container.ownerDocument.activeElement).toBe(cancel)
    fireEvent.click(cancel)
    expect(onCancel).toHaveBeenCalledOnce()
    expect(onSelect).toHaveBeenCalledOnce()
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSave).not.toHaveBeenCalled()
    expect(screen.getAllByRole('button')).toHaveLength(5)
  })
})

describe('LocalFilePreview Copy Source', () => {
  const initialConnection = $connection.get()
  const writeText = vi.fn<(text: string) => Promise<void>>()
  const targetFor = (path = '/synthetic/source.txt'): PreviewTarget => ({
    kind: 'file', label: path, source: path, url: path, path, previewKind: 'text', language: 'text'
  })
  const resultFor = (text: string, extra: Partial<HermesReadFileTextResult> = {}): HermesReadFileTextResult => ({
    path: '/synthetic/source.txt', text, ...extra
  })
  const deferred = <T,>() => {
    let resolve!: (value: T) => void
    const promise = new Promise<T>(done => { resolve = done })

    return { promise, resolve }
  }

  beforeEach(() => {
    $connection.set({ mode: 'local', connectionId: 'owner-a', profile: 'profile-a' } as never)
    readFileText.mockReset()
    gitRoot.mockReset().mockResolvedValue(null)
    fileDiff.mockReset().mockResolvedValue('')
    writeText.mockReset().mockResolvedValue(undefined)
    vi.stubGlobal('hermesDesktop', { writeClipboard: writeText })
  })
  afterEach(() => {
    cleanup()
    $connection.set(initialConnection)
    vi.unstubAllGlobals()
  })

  it('copies the exact loaded decoded CRLF/Unicode source without another read', async () => {
    const text = 'مرحبا café 🧪\r\n\r\nlast\r\n'
    readFileText.mockResolvedValue(resultFor(text))
    render(<LocalFilePreview reloadKey={0} target={targetFor()} />)
    const copy = await screen.findByRole('button', { name: 'Copy source' })
    expect(copy.textContent).toBe('')
    fireEvent.click(copy)
    await waitFor(() => expect(writeText).toHaveBeenCalledExactlyOnceWith(text))
    expect(await screen.findByRole('button', { name: 'Copied' })).toBe(copy)
    expect(readFileText).toHaveBeenCalledExactlyOnceWith('/synthetic/source.txt')
  })

  it('labels a truncated loaded prefix honestly in Arabic and does not enable editing', async () => {
    const text = 'الجزء المحمّل فقط\r\n🧪'
    readFileText.mockResolvedValue(resultFor(text, { truncated: true, byteSize: 600 * 1024 }))
    render(<I18nProvider configClient={null} initialLocale="ar"><LocalFilePreview reloadKey={0} target={targetFor()} /></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'معاينة على أي حال' }))
    const copy = await screen.findByRole('button', { name: 'نسخ الجزء المحمّل فقط' })
    expect(screen.getByText('عرض أول 512 KB.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'تحرير' })).toBeNull()
    const reads = readFileText.mock.calls.length
    fireEvent.click(copy)
    await waitFor(() => expect(writeText).toHaveBeenCalledExactlyOnceWith(text))
    expect(readFileText).toHaveBeenCalledTimes(reads)
  })

  it('offers no copy for pending, empty, unreadable or explicitly binary payloads', async () => {
    readFileText.mockReturnValue(new Promise(() => undefined))
    const pending = render(<LocalFilePreview reloadKey={0} target={targetFor()} />)
    expect(screen.queryByRole('button', { name: /Copy .*source/ })).toBeNull()
    pending.unmount()

    for (const kind of ['empty', 'error', 'result-binary', 'target-binary', 'binary-kind'] as const) {
      readFileText.mockReset()
      if (kind === 'error') { readFileText.mockRejectedValue(new Error('controlled read failure')) }
      else { readFileText.mockResolvedValue(resultFor(kind === 'empty' ? '' : 'binary-looking text', { binary: kind === 'result-binary' })) }
      const target = { ...targetFor(), ...(kind === 'target-binary' ? { binary: true } : {}), ...(kind === 'binary-kind' ? { previewKind: 'binary' as const } : {}) }
      const view = render(<LocalFilePreview reloadKey={0} target={target} />)
      if (kind === 'target-binary' || kind === 'result-binary') {
        fireEvent.click(await screen.findByRole('button', { name: 'Preview anyway' }))
      }
      await waitFor(() => expect(screen.queryByRole('status', { name: 'Loading preview' })).toBeNull())
      expect(screen.queryByRole('button', { name: /Copy .*source/ })).toBeNull()
      view.unmount()
    }
    expect(writeText).not.toHaveBeenCalled()
  })

  it.each(['path', 'connection', 'profile', 'reload'] as const)('rejects stale pending %s responses and copies only the current owner', async change => {
    const oldRead = deferred<HermesReadFileTextResult>()
    const newRead = deferred<HermesReadFileTextResult>()
    readFileText.mockReturnValueOnce(oldRead.promise).mockReturnValueOnce(newRead.promise)
    const target = targetFor()
    const view = render(<LocalFilePreview reloadKey={0} target={target} />)
    await waitFor(() => expect(readFileText).toHaveBeenCalledTimes(1))
    if (change === 'path') { view.rerender(<LocalFilePreview reloadKey={0} target={targetFor('/synthetic/next.txt')} />) }
    else if (change === 'reload') { view.rerender(<LocalFilePreview reloadKey={1} target={target} />) }
    else { act(() => $connection.set({ ...$connection.get()!, [change === 'connection' ? 'connectionId' : 'profile']: 'owner-b' })) }
    await waitFor(() => expect(readFileText).toHaveBeenCalledTimes(2))
    expect(screen.queryByRole('button', { name: 'Copy source' })).toBeNull()
    await act(async () => { newRead.resolve(resultFor('current\r\nمرحبا 🧪')) })
    const copy = await screen.findByRole('button', { name: 'Copy source' })
    fireEvent.click(copy)
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith('current\r\nمرحبا 🧪'))
    await act(async () => { oldRead.resolve(resultFor('stale owner')) })
    fireEvent.click(copy)
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(2))
    expect(writeText.mock.calls.map(([text]) => text)).toEqual(['current\r\nمرحبا 🧪', 'current\r\nمرحبا 🧪'])
    expect(readFileText).toHaveBeenCalledTimes(2)
  })

  it('checks live connection ownership before React rerenders and resets copied feedback for the new owner', async () => {
    readFileText.mockResolvedValueOnce(resultFor('old owner')).mockResolvedValueOnce(resultFor('new owner'))
    render(<LocalFilePreview reloadKey={0} target={targetFor()} />)
    const oldButton = await screen.findByRole('button', { name: 'Copy source' })
    fireEvent.click(oldButton)
    await screen.findByRole('button', { name: 'Copied' })
    act(() => {
      $connection.set({ ...$connection.get()!, profile: 'new-profile' })
      // The old DOM handler is still attached in this batch; its live-store fence must reject it.
      fireEvent.click(oldButton)
    })
    const nextButton = await screen.findByRole('button', { name: 'Copy source' })
    expect(nextButton).not.toBe(oldButton)
    expect(writeText).toHaveBeenCalledExactlyOnceWith('old owner')
    fireEvent.click(nextButton)
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith('new owner'))
    expect(readFileText).toHaveBeenCalledTimes(2)
  })

  it('does not merge a delayed old-owner diff into identical new-owner text', async () => {
    const oldDiff = deferred<string>()
    readFileText.mockResolvedValue(resultFor('same text'))
    gitRoot.mockResolvedValue('/synthetic')
    fileDiff.mockReturnValueOnce(oldDiff.promise).mockResolvedValueOnce('')
    render(<LocalFilePreview reloadKey={0} target={targetFor()} />)
    await screen.findByRole('button', { name: 'Copy source' })
    await waitFor(() => expect(fileDiff).toHaveBeenCalledTimes(1))
    act(() => $connection.set({ ...$connection.get()!, profile: 'new-profile' }))
    await waitFor(() => expect(fileDiff).toHaveBeenCalledTimes(2))
    await act(async () => { oldDiff.resolve('diff --git a/source.txt b/source.txt\n+stale') })
    expect(screen.queryByRole('button', { name: 'DIFF' })).toBeNull()
    fireEvent.click(await screen.findByRole('button', { name: 'Copy source' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledExactlyOnceWith('same text'))
    expect(readFileText).toHaveBeenCalledTimes(2)
  })
})

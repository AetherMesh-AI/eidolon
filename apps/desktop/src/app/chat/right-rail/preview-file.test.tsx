import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { MarkdownPreview, SourceView } from './preview-file'

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

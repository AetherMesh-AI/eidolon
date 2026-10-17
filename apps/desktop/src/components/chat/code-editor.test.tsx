import { insertNewlineAndIndent, undo } from '@codemirror/commands'
import { EditorState } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { act, cleanup, render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { CodeEditor, type CodeEditorApi } from './code-editor'

const appearance = vi.hoisted(() => ({ resolvedMode: 'light' }))
vi.mock('@/themes/context', () => ({ useTheme: () => appearance }))

afterEach(() => {
  cleanup()
  appearance.resolvedMode = 'light'
})

describe('CodeEditor reading direction', () => {
  it('isolates source direction while retaining prose, buffer, selection, history and live configuration', () => {
    const initialValue = '{"message":"مرحبا","enabled":false}'

    for (const framed of [false, true]) {
      const apiRef = createRef<CodeEditorApi>()
      const onChange = vi.fn()
      const onCursorChange = vi.fn()

      const editor = (disabled = false, direction = 'rtl') => (
        <div dir={direction}>
          <button>حفظ</button>
          <CodeEditor
            apiRef={apiRef}
            disabled={disabled}
            filePath={framed ? 'SOUL.md' : 'mcp.json'}
            framed={framed}
            initialValue={initialValue}
            onChange={onChange}
            onCursorChange={onCursorChange}
          />
        </div>
      )

      const { container, rerender, unmount } = render(editor())
      const content = container.querySelector<HTMLElement>('.cm-content')!
      const view = EditorView.findFromDOM(content)!
      expect(content.closest('[dir]')?.getAttribute('dir')).toBe(framed ? 'rtl' : 'ltr')
      expect(screen.getByRole('button', { name: 'حفظ' }).closest('[dir]')?.getAttribute('dir')).toBe('rtl')
      expect(view.lineWrapping).toBe(framed)
      act(() => {
        apiRef.current!.setCursor(initialValue.indexOf('مرحبا'))
        view.dispatch({ changes: { from: 1, insert: ' ' } })
        view.dispatch({ selection: { anchor: 2, head: 11 } })
      })
      const draft = view.state.doc.toString()
      const selection = view.state.selection
      expect(onChange).toHaveBeenLastCalledWith(draft)
      expect(onCursorChange).toHaveBeenLastCalledWith(selection.main.head)
      appearance.resolvedMode = 'dark'
      rerender(editor(true))
      expect(EditorView.findFromDOM(content)).toBe(view)
      expect(view.state.doc.toString()).toBe(draft)
      expect(view.state.selection.eq(selection)).toBe(true)
      expect(view.state.facet(EditorState.readOnly)).toBe(true)
      expect(view.state.facet(EditorView.darkTheme)).toBe(true)
      expect(insertNewlineAndIndent(view)).toBe(false)
      expect(view.state.doc.toString()).toBe(draft)
      rerender(editor(false, 'ltr'))
      expect(content.closest('[dir]')?.getAttribute('dir')).toBe('ltr')
      expect(view.state.doc.toString()).toBe(draft)
      expect(view.state.selection.eq(selection)).toBe(true)
      act(() => {
        expect(undo(view)).toBe(true)
      })
      expect(view.state.doc.toString()).toBe(initialValue)
      unmount()
      expect(apiRef.current).toBeNull()
    }
  })
})

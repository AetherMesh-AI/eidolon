import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { registry } from '@/contrib/registry'

import type * as Geometry from '../../geometry'
import type { GroupNode } from '../model'
import { $treeDragging, NEW_SESSION_DRAG, SESSION_TILE_DRAG } from '../store'

import { TreeGroup } from './tree-group'

const nativeOverlap = vi.hoisted(() => ({ value: null as null | { x: number; y: number; width: number; height: number } }))

vi.mock('../../geometry', async importOriginal => ({
  ...await importOriginal<typeof Geometry>(),
  useWindowControlsOverlap: () => nativeOverlap.value
}))

let root: null | Root = null
let container: HTMLDivElement | null = null
let disposePane: (() => void) | null = null

function render(ui: ReactNode) {
  if (!container) {
    container = globalThis.document.createElement('div')
    globalThis.document.body.append(container)
    root = createRoot(container)
  }

  act(() => {
    root!.render(ui)
  })
}

function terminalGroup(minimized: boolean): GroupNode {
  return {
    active: 'terminal',
    id: 'terminal-zone',
    minimized,
    panes: ['terminal'],
    // The chevron lives in the strip, so this zone has to be showing one. A
    // lone unregistered pane is on auto and would render none.
    tabStrip: 'always',
    type: 'group'
  }
}

const toggle = (label: string) =>
  globalThis.document.querySelector<HTMLButtonElement>(
    `[data-tree-group="terminal-zone"] button[aria-label="${label}"]`
  )!

afterEach(() => {
  if (root) {
    act(() => root!.unmount())
  }

  container?.remove()
  disposePane?.()
  root = null
  container = null
  disposePane = null
  nativeOverlap.value = null
  vi.unstubAllGlobals()
})

describe('TreeGroup', () => {
  it.each([{ x: 0, side: 'left' }, { x: 700, side: 'right' }])('publishes the $side native corner to an owning header and restores vertical clearance for tabs', ({ x, side }) => {
    nativeOverlap.value = { x, y: 0, width: 100, height: 34 }
    vi.stubGlobal('CSS', { escape: (value: string) => value })
    disposePane = registry.register({
      area: 'panes', id: 'inset-owner', title: 'Inset owner',
      data: { nativeControlsInHeader: true }, render: () => <div>Header owns native inset</div>
    })
    const node: GroupNode = { id: 'inset-zone', type: 'group', panes: ['inset-owner'], active: 'inset-owner', tabStrip: 'never' }
    render(<TreeGroup node={node} />)
    const group = () => globalThis.document.querySelector<HTMLElement>('[data-tree-group="inset-zone"]')!
    expect(group().style.paddingTop).toBe('')
    expect(group().style.getPropertyValue(`--pane-native-controls-${side}`)).toBe('100px')
    render(<TreeGroup node={{ ...node, tabStrip: 'always' }} />)
    expect(group().style.paddingTop).toBe('34px')
    expect(group().style.getPropertyValue(`--pane-native-controls-${side}`)).toBe('')
  })

  it('points the docked-zone chevron in the collapse or restore action direction', () => {
    disposePane = registry.register({
      area: 'panes',
      data: { height: '12rem' },
      id: 'terminal',
      render: () => <div>Terminal</div>,
      title: 'Terminal'
    })
    // jsdom does not implement CSS.escape, which the real tab-strip effect uses.
    vi.stubGlobal('CSS', { escape: (value: string) => value })

    render(<TreeGroup node={terminalGroup(false)} parentAxis="column" />)

    expect(toggle('Minimize').querySelector('i')!.className).toContain('codicon-chevron-down')

    render(<TreeGroup node={terminalGroup(true)} parentAxis="column" />)

    expect(toggle('Restore').querySelector('i')!.className).toContain('codicon-chevron-up')
  })

  // The invariant behind the shared eligibility predicate
  // (hostsSessionDropTarget): a session or new-session drag must paint the
  // SAME zones either drag resolver would accept, and stay dark everywhere
  // else. Standing chrome (terminal) is always dark; a zone hosting a chat
  // strip (workspace) paints for both sentinels; with no session-drag active
  // nothing paints even over an eligible zone.
  describe('session-drop overlay eligibility (one truth with the resolvers)', () => {
    const groupFor = (panes: string[], id = 'zone-a'): GroupNode => ({
      active: panes[0]!,
      id,
      minimized: false,
      panes,
      type: 'group'
    })

    const sheet = () => globalThis.document.querySelector('[data-tree-group="zone-a"] .pointer-events-none.absolute')

    async function withDragging(dragging: null | string, run: () => void) {
      await act(async () => {
        $treeDragging.set(dragging)
      })

      try {
        run()
      } finally {
        await act(async () => {
          $treeDragging.set(null)
        })
      }
    }

    it('stays dark over standing chrome (terminal) during a new-session drag', async () => {
      disposePane = registry.register({
        area: 'panes',
        data: { height: '12rem' },
        id: 'terminal',
        render: () => <div>Terminal</div>,
        title: 'Terminal'
      })
      vi.stubGlobal('CSS', { escape: (value: string) => value })

      render(<TreeGroup node={terminalGroup(false)} parentAxis="column" />)

      await withDragging(NEW_SESSION_DRAG, () => {
        expect(sheet()).toBeNull()
      })
    })

    it('lights a chat-strip zone for BOTH session and new-session drags, and only then', async () => {
      disposePane = registry.register({
        area: 'panes',
        data: {},
        id: 'workspace',
        render: () => <div>Chat</div>,
        title: 'Hermes'
      })
      vi.stubGlobal('CSS', { escape: (value: string) => value })

      render(<TreeGroup node={groupFor(['workspace'])} parentAxis="column" />)

      await withDragging(null, () => {
        expect(sheet()).toBeNull()
      })

      await withDragging(SESSION_TILE_DRAG, () => {
        expect(sheet()).not.toBeNull()
      })

      await withDragging(NEW_SESSION_DRAG, () => {
        expect(sheet()).not.toBeNull()
      })
    })
  })
})

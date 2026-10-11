import { useStore } from '@nanostores/react'
import { act, cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { registry } from '@/contrib/registry'

import { group, type GroupNode, split } from '../model'
import {
  $layoutTree,
  markCollapsePane,
  noteActiveTreeGroup,
  registerPaneCloser,
  setTreeGroupTabStrip,
  tabStripVisibleForGroup,
  toggleTargetZoneTabStrip
} from '../store'

import { TreeGroup } from './tree-group'

/** TreeGroup reads its node from props; subscribe so store writes re-render. */
function LiveTreeGroup() {
  const tree = useStore($layoutTree)!
  const node = (tree.type === 'split' ? tree.children[0] : tree) as GroupNode

  return <TreeGroup node={node} parentAxis="column" />
}

// Pins the tab-strip hide grammar. Hiding is a COMMAND now, not a gesture: the
// pointer can no longer take the strip away by accident, and whatever does take
// it away leaves a way back that does not depend on the chrome it just removed.

class TestResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', TestResizeObserver)
  // jsdom lacks CSS.escape, which tab-strip-scroll uses in a layout effect.
  vi.stubGlobal('CSS', { ...globalThis.CSS, escape: (value: string) => value })
  Element.prototype.hasPointerCapture ??= () => false
  Element.prototype.setPointerCapture ??= () => undefined
  Element.prototype.releasePointerCapture ??= () => undefined
  HTMLElement.prototype.scrollIntoView ??= () => undefined
})

const disposers: (() => void)[] = []

beforeEach(async () => {
  window.localStorage.clear()

  const { $dismissedPanes, $hiddenTreePanes } = await import('../store')
  $dismissedPanes.set(new Set())
  $hiddenTreePanes.set(new Set())

  for (const [id, data] of [
    ['workspace', { placement: 'main', uncloseable: true }],
    ['terminal', { placement: 'bottom' }]
  ] as const) {
    disposers.push(registry.register({ area: 'panes', data, id, render: () => null, title: id }))
  }

  markCollapsePane('terminal')
  registerPaneCloser('terminal', () => undefined)

  $layoutTree.set(split('column', [group(['workspace', 'terminal'], { active: 'terminal', id: 'grp-main' })]))
})

afterEach(() => {
  cleanup()
  disposers.splice(0).forEach(dispose => dispose())
})


const groupNode = () => {
  const node = $layoutTree.get()!

  return (node.type === 'split' ? node.children[0] : node) as GroupNode
}

const tablist = () => globalThis.document.querySelector('[role="tablist"]')

/** Two sub-threshold taps: pointerdown on the target, pointerup on window
 *  (drag-session listens there), twice — the retired double-tap path. */
const doubleTap = (target: Element) => {
  for (let i = 0; i < 2; i++) {
    fireEvent.pointerDown(target, { button: 0, clientX: 10, clientY: 10, pointerType: 'mouse' })
    fireEvent.pointerUp(window, { button: 0, clientX: 10, clientY: 10, pointerType: 'mouse' })
  }
}

describe('tab strip hide grammar', () => {
  it('no pointer gesture hides the strip', () => {
    render(<LiveTreeGroup />)

    // Both halves of the strip: the tab, which was always activate-only, and
    // the background, which used to answer a double-tap nothing announced.
    doubleTap(globalThis.document.querySelector('[data-tree-tab="terminal"]')!)
    doubleTap(globalThis.document.querySelector('[data-zone-tabstrip="grp-main"]')!)

    expect(tablist()).toBeTruthy()
    expect(groupNode().tabStrip).toBeUndefined()
  })

  it('renders no strip at all for a zone set to never', () => {
    setTreeGroupTabStrip('grp-main', 'never')
    render(<LiveTreeGroup />)

    expect(tablist()).toBeNull()
  })

  // The state that had no way out. The command targets the zone by
  // hover/focus/workspace fallback, so restoring the strip never depends on
  // the strip — or on any other chrome the hide took away.
  it('the toggle command reaches a zone that has no chrome left to click', () => {
    setTreeGroupTabStrip('grp-main', 'never')
    expect(tabStripVisibleForGroup(groupNode())).toBe(false)

    expect(toggleTargetZoneTabStrip()).toBe('always')
    expect(tabStripVisibleForGroup(groupNode())).toBe(true)
  })
})


describe('organization contextual strip', () => {
  function organizationGroup(siblings: string[]) {
    disposers.push(registry.register({ area: 'panes', id: 'sessions', title: 'Eidolon', render: () => null,
      data: { placement: 'left', hideOnly: true, contextualTabPeers: ['hermes-bots:pane'] } }))
    disposers.push(registry.register({ area: 'panes', id: 'hermes-bots:pane', title: 'Profile chats', render: () => null,
      data: { placement: 'left', hideOnly: true } }))
    $layoutTree.set(split('column', [group(['sessions', ...siblings], { active: 'sessions', id: 'grp-main' })]))
    noteActiveTreeGroup('grp-main')
  }

  it('restores default-pair mouse tabs through the same Show tabs command and can hide them again', async () => {
    organizationGroup(['hermes-bots:pane'])
    render(<LiveTreeGroup />)
    expect(tablist()).toBeNull()
    await act(async () => { expect(toggleTargetZoneTabStrip()).toBe('always') })
    expect(document.querySelector('[data-tree-tab="sessions"]')).toBeTruthy()
    expect(document.querySelector('[data-tree-tab="hermes-bots:pane"]')).toBeTruthy()
    await act(async () => { expect(toggleTargetZoneTabStrip()).toBe('never') })
    expect(tablist()).toBeNull()
  })

  it('keeps a customized terminal sibling mouse-accessible while organization navigation is active', () => {
    organizationGroup(['hermes-bots:pane', 'terminal'])
    render(<LiveTreeGroup />)
    const terminal = document.querySelector('[data-tree-tab="terminal"]')!
    expect(terminal).toBeTruthy()
    fireEvent.pointerDown(terminal, { button: 0, clientX: 10, clientY: 10, pointerType: 'mouse' })
    fireEvent.pointerUp(window, { button: 0, clientX: 10, clientY: 10, pointerType: 'mouse' })
    expect(groupNode().active).toBe('terminal')
    fireEvent.pointerDown(document.querySelector('[data-tree-tab="sessions"]')!, { button: 0, clientX: 10, clientY: 10, pointerType: 'mouse' })
    fireEvent.pointerUp(window, { button: 0, clientX: 10, clientY: 10, pointerType: 'mouse' })
    expect(groupNode().active).toBe('sessions')
    expect(document.querySelector('[data-tree-tab="terminal"]')).toBeTruthy()
  })
})

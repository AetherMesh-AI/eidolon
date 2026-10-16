import { afterEach, expect, it, vi } from 'vitest'

import { queueHomeKeyboardReveal } from './home-keyboard-focus'

let frame: FrameRequestCallback
let cancel: (() => void) | undefined

afterEach(() => {cancel?.(); document.body.innerHTML = ''; vi.restoreAllMocks()})

function setup(top = 40, height = 74) {
  document.body.innerHTML = '<main class="eid-workspace"><details open><summary>Intake</summary><textarea></textarea><button>Submit</button></details></main><input id="outside">'
  const workspace = document.querySelector<HTMLElement>('main')!
  const intake = document.querySelector('details')!
  const field = document.querySelector('textarea')!
  field.style.outlineWidth = '3px'
  field.style.outlineOffset = '3px'
  Object.defineProperty(workspace, 'clientHeight', { value: 325 })
  workspace.scrollTop = 500
  vi.spyOn(workspace, 'getBoundingClientRect').mockReturnValue({ top: 47 } as DOMRect)
  vi.spyOn(field, 'getBoundingClientRect').mockReturnValue({ top, bottom: top + height, height } as DOMRect)
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation(callback => {frame = callback;

 return 1})
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {})
  cancel = queueHomeKeyboardReveal(intake)

  return { workspace, intake, field }
}

it('reveals the full top outline after native keyboard focus without changing draft/focus', () => {
  const { workspace, field } = setup()
  field.value = 'Retained draft'
  field.focus()
  frame(0)
  expect(workspace.scrollTop).toBe(486)
  expect(document.activeElement).toBe(field)
  expect(field.value).toBe('Retained draft')
})

it('moves only the minimum bottom overflow and retires after one frame', () => {
  const { workspace, field } = setup(320)
  field.focus()
  frame(0)
  expect(workspace.scrollTop).toBe(529)
  frame(1)
  expect(workspace.scrollTop).toBe(529)
})

it.each([[100, 74], [40, 400]])('does not move visible or oversized controls (%s,%s)', (top, height) => {
  const { workspace, field } = setup(top, height)
  field.focus()
  frame(0)
  expect(workspace.scrollTop).toBe(500)
})

it.each(['pointerdown', 'keydown', 'wheel', 'input', 'touchstart'])('cancels on later %s interaction', type => {
  const { workspace, field } = setup()
  field.focus()
  document.dispatchEvent(new Event(type))
  frame(0)
  expect(workspace.scrollTop).toBe(500)
})

it('cancels when focus moves again, including outside intake', () => {
  const { workspace, field } = setup()
  field.focus()
  document.querySelector<HTMLInputElement>('#outside')!.focus()
  frame(0)
  expect(workspace.scrollTop).toBe(500)
})

it('ignores prevented Tab with no new focus', () => {
  const { workspace } = setup()
  frame(0)
  expect(workspace.scrollTop).toBe(500)
})

it.each(['owner/route cleanup', 'unmount'])('cancels pending reveal on %s', reason => {
  const { workspace, intake, field } = setup()
  field.focus()

  if (reason === 'unmount') {intake.remove()} else {cancel?.()}
  frame(0)
  expect(workspace.scrollTop).toBe(500)
})

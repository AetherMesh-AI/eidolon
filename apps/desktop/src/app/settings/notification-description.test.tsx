import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { ToggleRow } from './primitives'

afterEach(cleanup)

it('opens a compact explanation independently of its labelled switch, even while disabled', () => {
  const onChange = vi.fn()
  const props = { checked: true, description: 'A command is waiting for you to approve or reject it.', label: 'Approval needed', onChange }
  const view = render(<ToggleRow {...props} collapsibleDescription />)
  const details = view.container.querySelector('details')!
  expect(details.open).toBe(false)
  fireEvent.click(details.querySelector('summary')!)
  expect(details.open).toBe(true)
  expect(details.textContent).toContain(props.description)
  expect(onChange).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('switch', { name: props.label }))
  expect(onChange).toHaveBeenCalledExactlyOnceWith(false)
  view.rerender(<ToggleRow {...props} collapsibleDescription disabled />)
  fireEvent.click(details.querySelector('summary')!)
  expect(details.open).toBe(false)
  fireEvent.click(details.querySelector('summary')!)
  expect(details.open).toBe(true)
  expect(screen.getByRole('switch', { name: props.label }).hasAttribute('disabled')).toBe(true)
  expect(onChange).toHaveBeenCalledOnce()
})

it('keeps the dedicated settings explanation expanded with its existing switch owner', () => {
  const onChange = vi.fn()
  const view = render(<ToggleRow checked={false} description="An agent needs an answer." label="Input needed" onChange={onChange} />)
  expect(view.container.querySelector('details')).toBeNull()
  expect(screen.getByText('An agent needs an answer.')).toBeTruthy()
  fireEvent.click(screen.getByRole('switch', { name: 'Input needed' }))
  expect(onChange).toHaveBeenCalledExactlyOnceWith(true)
})

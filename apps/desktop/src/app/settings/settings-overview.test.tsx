import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { SettingsOverview } from './settings-overview'

afterEach(cleanup)

it('keeps independent editor owners and opens the exact configuration destination', () => {
  const onSelect = vi.fn()
  const saveModel = vi.fn()
  const saveNotification = vi.fn()
  const { container } = render(<SettingsOverview editor={<button onClick={saveModel}>Apply model draft</button>} notifications={<button onClick={saveNotification}>Change device notification</button>} onSelect={onSelect} />)
  fireEvent.click(screen.getByRole('button', { name: 'Change device notification' }))
  expect(saveNotification).toHaveBeenCalledOnce()
  expect(saveModel).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Open Providers' }))
  expect(onSelect).toHaveBeenCalledWith('providers')
  expect(saveModel).not.toHaveBeenCalled()
  expect(container.querySelector('form')).toBeNull()
})

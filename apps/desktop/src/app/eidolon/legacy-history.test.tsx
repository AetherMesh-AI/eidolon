import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { LegacyOrganizationHistory, readLegacyOrganization } from './legacy-history'

it.each([' { "objectives": [], "agents": [], "tasks": [], "activity": [], "knowledge": [] }\n', '{broken historical bytes'])('exports original bytes without writes or replay: %s', async raw => {
  const storage = { getItem: vi.fn(() => raw), setItem: vi.fn(), removeItem: vi.fn() }
  expect(readLegacyOrganization(storage).raw).toBe(raw)
  let blob: Blob | undefined
  const oldCreate = URL.createObjectURL
  const oldRevoke = URL.revokeObjectURL
  URL.createObjectURL = vi.fn((value: Blob) => {blob = value;

 return 'blob:legacy'})
  URL.revokeObjectURL = vi.fn()
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
  const view = render(<LegacyOrganizationHistory storage={storage} />)
  fireEvent.click(screen.getByRole('button', { name: 'Export original records' }))
  const text = await new Promise<string>((resolve, reject) => {const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(blob!)})
  expect(text).toBe(raw)
  expect(click).toHaveBeenCalledOnce()
  expect(storage.setItem).not.toHaveBeenCalled()
  expect(storage.removeItem).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: /create|clear|reset|load example/i })).toBeNull()
  view.unmount()
  click.mockRestore()
  URL.createObjectURL = oldCreate
  URL.revokeObjectURL = oldRevoke
})

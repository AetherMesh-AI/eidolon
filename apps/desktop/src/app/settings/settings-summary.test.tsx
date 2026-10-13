import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'

import { SettingsConfigSummary } from './settings-summary'

afterEach(cleanup)

it('shows only selected saved provider values without presenting credentials or claiming a connection', () => {
  const record = { model: { provider: 'Other provider', default: 'retained-model', api_key: 'never-display-this' }, credentials: { secret: 'also-private' } }
  const { container, rerender } = render(<SettingsConfigSummary record={record} section="providers" />)
  expect(screen.getByText('Other provider')).toBeTruthy()
  expect(screen.getByText('retained-model')).toBeTruthy()
  expect(container.textContent).not.toMatch(/never-display-this|also-private|Connected/)
  expect(container.querySelector('button,input,select')).toBeNull()
  rerender(<SettingsConfigSummary record={{ model: 'legacy-model' }} section="providers" />)
  expect(screen.queryByText('Other provider')).toBeNull()
  expect(screen.getByText('legacy-model')).toBeTruthy()
  expect(screen.getByText('Not recorded')).toBeTruthy()
})

it('distinguishes explicit false and zero values from missing safety configuration', () => {
  const { rerender } = render(<SettingsConfigSummary record={{ approvals: { mode: 'manual', timeout: 0 }, security: { redact_secrets: false } }} section="safety" />)
  expect(screen.getByText('manual')).toBeTruthy()
  expect(screen.getByText('0')).toBeTruthy()
  expect(screen.getByText('false')).toBeTruthy()
  expect(screen.queryByText('Not recorded')).toBeNull()
  rerender(<SettingsConfigSummary record={{ approvals: { mode: {} }, security: {} }} section="safety" />)
  expect(screen.getAllByText('Not recorded')).toHaveLength(3)
  expect(screen.queryByText('[object Object]')).toBeNull()
})

it('does not present retained values as verified after a failed refresh', () => {
  const { rerender } = render(<SettingsConfigSummary loading section="workspace" />)
  expect(screen.getByRole('status').textContent).toContain('Loading')
  rerender(<SettingsConfigSummary failed record={{ terminal: { cwd: '/old/workspace' } }} section="workspace" />)
  expect(screen.getByRole('status')).toBeTruthy()
  expect(screen.queryByText('/old/workspace')).toBeNull()
  rerender(<SettingsConfigSummary record={{ terminal: { cwd: '/current/workspace', backend: 'local', timeout: 30 } }} section="workspace" />)
  expect(screen.getByText('/current/workspace')).toBeTruthy()
  expect(screen.queryByRole('status')).toBeNull()
})

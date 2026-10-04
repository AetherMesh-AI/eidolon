import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'

import { demoSnapshot } from './demo'
import { MetadataSummary } from './objective-metadata'

it('labels historical intent and estimates without exposing an editor', () => {
  const objective = { ...demoSnapshot().objectives[0], progress: 25, autonomyIntent: 'Ask before delivery' }
  render(<MetadataSummary objective={objective} />)
  expect(screen.getByText('25% · local estimate')).toBeTruthy()
  expect(screen.getByText('Ask before delivery')).toBeTruthy()
  expect(screen.getByText(/read-only record does not enforce permissions/)).toBeTruthy()
  expect(screen.queryByRole('textbox')).toBeNull()
})

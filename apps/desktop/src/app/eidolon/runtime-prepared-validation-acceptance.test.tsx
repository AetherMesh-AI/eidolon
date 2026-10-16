import { render, screen, within } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { RuntimeAcceptance } from './runtime-acceptance'
import { preparedValidationActivityCopy } from './runtime-prepared-validation-activity-copy'
import { preparedStatusObjective, preparedStatusSnapshot } from './runtime-prepared-validation-status.test-fixtures'
import { validateSnapshot } from './runtime-snapshot-contract'

it('keeps the legacy test execution placeholder when no prepared refs were selected', () => {
  const objective = preparedStatusObjective()
  delete objective.preparedValidations; delete objective.preparedValidation
  objective.requiredChecks = ['project_tests']
  render(<RuntimeAcceptance objective={validateSnapshot(preparedStatusSnapshot(objective)).objectives[0]} onOpenEvidence={vi.fn()} />)
  expect(screen.getByRole('region', { name: 'Latest project test execution' })).toBeTruthy()
  expect(screen.queryByRole('region', { name: preparedValidationActivityCopy.en.title })).toBeNull()
})

it('shows exact prepared selection and missing-status unavailability instead of the empty legacy placeholder', () => {
  const objective = preparedStatusObjective()
  delete objective.preparedValidation
  const onOpenEvidence = vi.fn()
  render(<RuntimeAcceptance objective={validateSnapshot(preparedStatusSnapshot(objective)).objectives[0]} onOpenEvidence={onOpenEvidence} />)
  const section = screen.getByRole('region', { name: preparedValidationActivityCopy.en.title })
  expect(within(section).getByText(preparedValidationActivityCopy.en.unavailable)).toBeTruthy()
  expect(within(section).getByText(objective.preparedValidations![0].registrationSha256)).toBeTruthy()
  expect(screen.queryByRole('region', { name: 'Latest project test execution' })).toBeNull()
  expect(within(section).queryByRole('link')).toBeNull()
  expect(within(section).queryByRole('button')).toBeNull()
  expect(onOpenEvidence).not.toHaveBeenCalled()
})

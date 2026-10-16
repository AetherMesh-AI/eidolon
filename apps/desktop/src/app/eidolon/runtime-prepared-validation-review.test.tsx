import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'

import { preparedValidationCopy } from './runtime-prepared-validation-copy'
import { preparedReviewSummary, PreparedValidationReview } from './runtime-prepared-validation-review'
import { preparedPreview } from './runtime-prepared-validation.test-fixtures'

it('keeps a large dependency inventory out of the DOM and formats bounded scope only after expansion', async () => {
  const candidate = preparedPreview().candidate
  candidate.contract.source.files = Array.from({ length: 110 }, (_, index) => `src/exact-${index}.ts`)
  candidate.contract.source.executionOnlyHashes = Object.fromEntries(candidate.contract.source.files.map(path => [path, 'a'.repeat(64)]))
  candidate.verification.dependencyFiles = 92160
  // Reading the inventory is unnecessary for the review view, regardless of its size.
  Object.defineProperty(candidate.contract.dependencies.manifest, 'files', { get() { throw new Error('Large inventory was traversed') } })
  const summary = preparedReviewSummary(candidate)
  expect(summary.source.fileCount).toBe(110)
  expect(summary.source.firstSourcePaths).toHaveLength(100)
  expect(Object.keys(summary.source.firstExecutionOnlyHashes)).toHaveLength(100)
  expect(summary.dependencies.verifiedFiles).toBe(92160)
  expect(summary.dependencies).not.toHaveProperty('manifest')
  expect(summary.profile).toEqual(candidate.contract.profile)
  render(<PreparedValidationReview candidate={candidate} />)
  expect(screen.queryByText(preparedValidationCopy.en.boundedReview)).toBeNull()
  const disclosure = screen.getByText(preparedValidationCopy.en.exact).closest('details')!
  disclosure.open = true; fireEvent(disclosure, new Event('toggle'))
  await screen.findByText(preparedValidationCopy.en.boundedReview)
  expect(screen.getByText(/firstSourcePaths/).textContent).toContain('src/exact-99.ts')
  expect(screen.getByText(/firstSourcePaths/).textContent).not.toContain('src/exact-100.ts')
})

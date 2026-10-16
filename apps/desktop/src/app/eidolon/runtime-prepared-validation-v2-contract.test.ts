import { expect, it } from 'vitest'

import { validPreparedCandidate, validPreparedSelection } from './runtime-prepared-validation-contract'
import { preparedDescriptor, preparedPreview, preparedSelection } from './runtime-prepared-validation.test-fixtures'

it.each([1, 2] as const)('binds v%s to its complete exact recipes and rejects changed version, commands, or incomplete evidence', version => {
  const selection = preparedSelection(version)
  const descriptor = preparedDescriptor()
  const candidate = preparedPreview(descriptor, selection).candidate
  expect(validPreparedSelection(selection)).toBe(true)
  expect(validPreparedCandidate(candidate, selection, descriptor)).toBe(true)

  for (const mutate of [
    (value: typeof selection) => { value.profile.version = 3 },
    (value: typeof selection) => { value.profile.command = 'npm run build' },
    (value: typeof selection) => { (value.profile.limits as Record<string, unknown>).command = 'shell' },
    (value: typeof selection) => { (value.profile.steps as unknown[]).pop() },
    (value: typeof selection) => { (value.profile.steps as Record<string, unknown>[])[0].recipe = 'shell' },
    (value: typeof selection) => { (value.profile.steps as Record<string, unknown>[])[0].checkKind = 'project_build' },
    (value: typeof selection) => { (value.profile.steps as Record<string, unknown>[])[0].config = '../outside.json' },
    (value: typeof selection) => { (value.profile.steps as Record<string, unknown>[])[0].args = ['--unsafe'] }
  ]) {
    const invalid = structuredClone(selection); mutate(invalid)
    expect(validPreparedSelection(invalid)).toBe(false)
  }

  for (const contract of ['prepared-validation-v3', version === 2 ? 'prepared-validation-v1' : 'prepared-validation-v2']) {
    const invalid = structuredClone(candidate)
    Object.assign(invalid.contract, { contract })
    expect(validPreparedCandidate(invalid, selection, descriptor)).toBe(false)
  }

  const mismatched = structuredClone(candidate)
  mismatched.contract.version = version === 2 ? 1 : 2
  expect(validPreparedCandidate(mismatched, selection, descriptor)).toBe(false)
})

it('requires the v2 build exactly once with no test selection, source directories, or new command fields', () => {
  for (const mutation of ['duplicate', 'tests', 'directories', 'command', 'legacy'] as const) {
    const selection = preparedSelection(2)
    const steps = selection.profile.steps as Record<string, unknown>[]
    const build = steps.find(step => step.recipe === 'workspace_vite_build')!

    if (mutation === 'duplicate') { steps[0] = { ...build } }

    if (mutation === 'tests') { build.testFiles = ['apps/desktop/src/main.test.tsx'] }

    if (mutation === 'directories') { build.sourceDirectories = ['apps/desktop/src'] }

    if (mutation === 'command') { build.command = 'vite build' }

    if (mutation === 'legacy') { selection.profile.version = 1 }
    expect(validPreparedSelection(selection), mutation).toBe(false)
  }
})

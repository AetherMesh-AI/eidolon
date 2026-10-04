import { expect, it } from 'vitest'

import { organizationStartHash } from './navigation'

it('lands a fresh primary window on Home without replacing a session deep link', () => {
  expect(organizationStartHash('')).toBe('#/home')
  expect(organizationStartHash('#/s/profile-session')).toBe('#/s/profile-session')
  expect(organizationStartHash('#/')).toBe('#/')
})

import { expect, it } from 'vitest'

import { initializeOrganizationStart, isAutomaticHomeStart, organizationStartHash } from './navigation'

it('lands a fresh primary window on Home without replacing a session deep link', () => {
  expect(organizationStartHash('')).toBe('#/home')
  expect(organizationStartHash('#/s/profile-session')).toBe('#/s/profile-session')
  expect(organizationStartHash('#/')).toBe('#/')
})

it('defaults only an unaddressed primary window and does not persist that intent across reloads', () => {
  const previousUrl = window.location.href

  try {
    window.history.replaceState(null, '', '/')
    initializeOrganizationStart()
    expect(window.location.hash).toBe('#/home')
    expect(isAutomaticHomeStart('/home')).toBe(true)

    // The now-explicit Home URL on reload must not resume a remembered chat.
    initializeOrganizationStart()
    expect(isAutomaticHomeStart('/home')).toBe(false)

    for (const kind of ['hud', 'browser', 'secondary', 'overlay', 'quick', 'wake']) {
      window.history.replaceState(null, '', `/?win=${kind}`)
      initializeOrganizationStart()
      expect(window.location.hash).toBe('')
      expect(isAutomaticHomeStart('/home')).toBe(false)
    }
  } finally {
    window.history.replaceState(null, '', previousUrl)
    initializeOrganizationStart()
  }
})

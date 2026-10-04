import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '@/i18n/en'
import { $desktopVersion } from '@/store/updates'

import { AboutSettings } from './about-settings'

vi.mock('@/store/updates', async () => {
  const { atom } = await import('nanostores')

  return {
    $desktopVersion: atom(null),
    $updateApply: atom({ applying: false, stage: 'idle' }),
    $updateChecking: atom(false),
    $updateStatus: atom(null),
    checkUpdates: vi.fn(),
    openUpdatesWindow: vi.fn(),
    refreshDesktopVersion: vi.fn(),
    startActiveUpdate: vi.fn()
  }
})

// Uninstallation has its own backend side effects and is unrelated to the
// recovery/release navigation exercised here.
vi.mock('./uninstall-section', () => ({ UninstallSection: () => null }))

const RELEASES_URL = 'https://github.com/AetherMesh-AI/Eidolon/releases'
const openExternal = vi.fn()
const originalBridge = window.hermesDesktop

beforeEach(() => {
  $desktopVersion.set(null)
  Object.defineProperty(window, 'hermesDesktop', {
    configurable: true,
    value: { openExternal }
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  Object.defineProperty(window, 'hermesDesktop', {
    configurable: true,
    value: originalBridge
  })
})

describe('About project navigation', () => {
  it('opens this project\'s release notes through the desktop bridge', () => {
    render(<AboutSettings />)
    const releaseNotes = screen.getByRole('link', { name: en.settings.about.releaseNotes })

    expect(releaseNotes.getAttribute('href')).toBe(RELEASES_URL)
    fireEvent.click(releaseNotes)
    expect(openExternal).toHaveBeenCalledExactlyOnceWith(RELEASES_URL)
  })

  it('offers the same project releases when the installed app needs recovery', () => {
    $desktopVersion.set({
      appVersion: '0.1.1',
      bundleOutOfSync: true,
      electronVersion: '',
      hermesRoot: '/fixture/eidolon',
      nodeVersion: '',
      platform: 'linux'
    })
    render(<AboutSettings />)
    const recovery = screen.getByRole('link', { name: en.settings.about.bundleOutOfSyncAction })

    expect(recovery.getAttribute('href')).toBe(RELEASES_URL)
    fireEvent.click(recovery)
    expect(openExternal).toHaveBeenCalledExactlyOnceWith(RELEASES_URL)
  })
})

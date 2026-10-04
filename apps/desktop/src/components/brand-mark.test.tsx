import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import eidolonIcon from '../../assets/icon.png'

import { BrandMark } from './brand-mark'

afterEach(() => {
  cleanup()
  vi.unstubAllEnvs()
})

describe('BrandMark', () => {
  it.each(['./', '/', '/eidolon/'])('uses the bundled app icon with the %s asset base', baseUrl => {
    vi.stubEnv('BASE_URL', baseUrl)

    const { container } = render(<BrandMark aria-label="Eidolon" className="size-8" />)
    const tile = container.firstElementChild
    const image = tile?.querySelector('img')

    expect(image?.getAttribute('src')).toBe(eidolonIcon)
    expect(image?.getAttribute('alt')).toBe('')
    expect(tile?.getAttribute('aria-label')).toBe('Eidolon')
    expect(tile?.classList.contains('size-8')).toBe(true)
  })
})

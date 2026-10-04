import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { BrandMark } from './brand-mark'

afterEach(() => {
  cleanup()
  vi.unstubAllEnvs()
})

describe('BrandMark', () => {
  it.each(['./', '/', '/eidolon/'])('loads the app icon relative to the %s asset base', baseUrl => {
    vi.stubEnv('BASE_URL', baseUrl)

    const { container } = render(<BrandMark aria-label="Eidolon" className="size-8" />)
    const tile = container.firstElementChild
    const image = tile?.querySelector('img')

    expect(image?.getAttribute('src')).toBe(`${baseUrl}icon.png`)
    expect(image?.getAttribute('alt')).toBe('')
    expect(tile?.getAttribute('aria-label')).toBe('Eidolon')
    expect(tile?.classList.contains('size-8')).toBe(true)
  })
})

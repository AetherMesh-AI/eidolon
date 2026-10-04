import { readFileSync } from 'node:fs'

import { expect, it } from 'vitest'

import { DESKTOP_APP_ID } from './app-identity'

it('uses the packaged app identity for native notifications', () => {
  const manifest = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'))

  expect(DESKTOP_APP_ID).toBe(manifest.build.appId)
})

import { describe, expect, it } from 'vitest'

import { contrastRatio, mix } from './color'
import { organizationPalette } from './organization-palette'
import { BUILTIN_THEME_LIST } from './presets'

describe('organization ink follows every built-in Settings palette', () => {
  for (const theme of BUILTIN_THEME_LIST) {
    for (const [mode, colors] of [
      ['light', theme.colors],
      ['dark', theme.darkColors ?? theme.colors]
    ] as const) {
      it(`${theme.name} ${mode}: readable ordinary text and on-accent controls`, () => {
        const tokens = organizationPalette(colors)
        const ink = tokens['--eidolon-foreground']

        // All actual palette surfaces, including tinted selection/card states,
        // must retain AA headroom before the native compositing checks.
        for (const background of [colors.background, colors.card, colors.sidebarBackground ?? colors.background]) {
          expect(contrastRatio(ink, background)).toBeGreaterThanOrEqual(7)
          expect(contrastRatio(ink, mix(background, colors.primary, 0.08))).toBeGreaterThanOrEqual(4.5)
        }

        expect(contrastRatio(tokens['--eidolon-on-primary'], colors.primary)).toBeGreaterThanOrEqual(4.5)
      })
    }
  }
})

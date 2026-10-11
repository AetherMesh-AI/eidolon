import { ensureContrast, readableOn } from './color'
import type { DesktopThemeColors } from './types'

/** Organization cards and compact controls need more contrast headroom than
 * the translucent general chrome. Derive their ink from the selected palette;
 * do not introduce another theme preference or a fixed brand palette. */
export function organizationPalette(colors: DesktopThemeColors): Record<string, string> {
  const backgrounds = [colors.background, colors.card, colors.sidebarBackground ?? colors.background]
  const foreground = backgrounds.reduce((ink, background) => ensureContrast(ink, background, 7), colors.foreground)

  return {
    '--eidolon-foreground': foreground,
    '--eidolon-on-primary': readableOn(colors.primary)
  }
}

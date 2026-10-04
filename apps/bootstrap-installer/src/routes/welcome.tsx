import { type CSSProperties } from 'react'

import { HackeryButton } from '../components/hackery-button'
import { startInstall } from '../store'

/*
 * Welcome screen.
 *
 * Mirrors the desktop's chat intro (apps/desktop/src/components/chat/intro.tsx):
 *   - EIDOLON wordmark rendered in Collapse Bold, uppercase, tracked
 *   - mix-blend-plus-lighter so the type "glows" on the canvas
 *   - fit-text utility so the wordmark sizes itself to the column
 *
 * The default home matches the desktop/runtime. The legacy -HermesHome
 * option remains available for an explicitly selected custom location.
 */
export default function Welcome() {
  return (
    <div className="eidolon-fade-in flex h-full flex-col items-center justify-center gap-10 px-12 py-10">
      {/* Hero — same recipe the desktop's chat/intro.tsx uses */}
      <div className="w-full max-w-2xl min-w-0 text-center">
        <p
          className="fit-text mx-auto mb-4 w-full font-['Collapse'] font-bold uppercase leading-[0.9] tracking-[0.08em] text-midground mix-blend-plus-lighter dark:text-foreground/90"
          style={
            {
              '--fit-text-line-height': '0.9',
              '--fit-text-max': '6rem',
              '--fit-text-min': '2.5rem'
            } as CSSProperties
          }
        >
          <span>
            <span>EIDOLON</span>
          </span>
          <span aria-hidden="true">EIDOLON</span>
        </p>

        <p className="m-0 text-center text-base leading-normal tracking-tight text-muted-foreground">
          Set up the experimental Eidolon desktop agent manager. Installation
          downloads dependencies and may take several minutes.
        </p>
      </div>

      <HackeryButton label="Install" onClick={() => void startInstall()} />
    </div>
  )
}

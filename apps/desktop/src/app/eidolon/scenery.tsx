import './scenery.css'

import { useId } from 'react'

/** Original vector scenery, decorative only: no project, artifact or agent identity is implied. */
export function OrganizationScenery({ className = '' }: { className?: string }) {
  const id = useId().replaceAll(':', '')

  return <svg aria-hidden="true" className={`eid-scenery ${className}`} focusable="false" preserveAspectRatio="xMidYMid slice" viewBox="0 0 1000 240">
    <defs>
      <linearGradient id={`${id}-sky`} x2="0" y2="1"><stop stopColor="var(--eid-art-sky)" /><stop offset="1" stopColor="var(--eidolon-bg)" /></linearGradient>
      <linearGradient id={`${id}-mist`} x2="0" y2="1"><stop stopColor="var(--eidolon-bg)" stopOpacity="0" /><stop offset="1" stopColor="var(--eidolon-bg)" /></linearGradient>
    </defs>
    <path d="M0 0H1000V240H0Z" fill={`url(#${id}-sky)`} />
    <circle cx="737" cy="66" fill="var(--eid-art-moon)" r="45" />
    <path d="M0 170 92 112 166 154 280 48 343 107 389 91 465 149 559 98 608 125 682 65 749 129 847 44 924 114 1000 82V240H0Z" fill="var(--eid-art-far)" />
    <path d="m223 106 57-58 31 72-33-24-18 20-6-25Zm576-21 48-41 29 58-29-24-21 27Z" fill="var(--eid-art-snow)" />
    <path d="M0 220 83 165 145 184 231 153 329 190 426 126 501 172 586 143 664 163 755 118 834 151 928 104 1000 156V240H0Z" fill="var(--eid-art-mid)" />
    <g fill="var(--eid-art-near)">
      <path d="M623 185v-28h14v-29h11v-18l7-17 7 17v18h9v-42l8-24 8 24v52h14v-25l8-16 8 16v25h13v47Z" />
      <path d="M606 195v-26h9v-13h13v13h104v-15h11v15h12v26Zm34 0v-10h75v10Z" />
      <path d="M0 228 90 211 164 228 289 203 375 225 470 194 564 205 651 191 744 213 820 175 923 205 1000 188V240H0Z" />
      <path d="m803 184 9-29 9 29h-5l8 19h-23l7-19Zm40 13 11-38 11 38h-7l9 22h-27l9-22Zm89 13 9-33 9 33h-5l7 20h-22l7-20Z" />
    </g>
    <path d="M0 178H1000V240H0Z" fill={`url(#${id}-mist)`} />
  </svg>
}

/** Organization-only mark; installed application and provider icons keep their existing owners. */
export function OrganizationMark() {
  return <svg aria-hidden="true" className="eid-organization-mark" focusable="false" viewBox="0 0 32 32">
    <path d="M16 2 20 12 30 16 20 20 16 30 12 20 2 16 12 12Z" fill="var(--eidolon-surface-3)" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.5" />
    <path d="m16 8 2 6 6 2-6 2-2 6-2-6-6-2 6-2Z" fill="currentColor" />
  </svg>
}

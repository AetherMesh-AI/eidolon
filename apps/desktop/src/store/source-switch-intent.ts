import { atom, batch } from 'nanostores'

import { $organizationObservationEpoch } from './organization-observation-mode'

// Foreground intent is distinct from committed route identity: a newer pick
// owns the UI while still dialing, even if it later returns to the same route.
export const $sourceSwitchIntent = atom(0)

export type SourceSwitchIntent = number

export function beginSourceSwitchIntent(): SourceSwitchIntent {
  const revision = $sourceSwitchIntent.get() + 1
  // Every source door (including profile and Settings) revokes starting reads
  // before it can publish a new route. Mounted and remounted organization panes
  // remain observational; execution still requires its explicit mutation.
  batch(() => {
    $sourceSwitchIntent.set(revision)
    $organizationObservationEpoch.set($organizationObservationEpoch.get() + 1)
  })

  return revision
}

export function currentSourceSwitchIntent(): SourceSwitchIntent {
  return $sourceSwitchIntent.get()
}

export function isCurrentSourceSwitchIntent(intent: SourceSwitchIntent): boolean {
  return intent === $sourceSwitchIntent.get()
}

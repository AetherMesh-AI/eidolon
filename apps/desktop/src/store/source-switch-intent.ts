import { $organizationObservationEpoch } from './organization-observation-mode'

// Foreground intent is distinct from committed route identity: a newer pick
// owns the UI while still dialing, even if it later returns to the same route.
let revision = 0

export type SourceSwitchIntent = number

export function beginSourceSwitchIntent(): SourceSwitchIntent {
  revision++
  // Every source door (including profile and Settings) revokes starting reads
  // before it can publish a new route. Mounted and remounted organization panes
  // remain observational; execution still requires its explicit mutation.
  $organizationObservationEpoch.set($organizationObservationEpoch.get() + 1)

  return revision
}

export function currentSourceSwitchIntent(): SourceSwitchIntent {
  return revision
}

export function isCurrentSourceSwitchIntent(intent: SourceSwitchIntent): boolean {
  return intent === revision
}

// Foreground intent is distinct from committed route identity: a newer pick
// owns the UI while still dialing, even if it later returns to the same route.
let revision = 0

export type SourceSwitchIntent = number

export function beginSourceSwitchIntent(): SourceSwitchIntent {
  return ++revision
}

export function currentSourceSwitchIntent(): SourceSwitchIntent {
  return revision
}

export function isCurrentSourceSwitchIntent(intent: SourceSwitchIntent): boolean {
  return intent === revision
}

import { type ConnectionSwitchOutcome, selectConnectionWithOutcome } from './connections'
import { $organizationObservationEpoch, $organizationObservationSwitching } from './organization-observation-mode'
import { currentSourceSwitchIntent, isCurrentSourceSwitchIntent, type SourceSwitchIntent } from './source-switch-intent'

let pending: { target: string; intent: SourceSwitchIntent; promise: Promise<ConnectionSwitchOutcome> } | undefined

/** Set the read policy before any dial/reset/publication can wake the provider. */
export function selectOrganizationConnection(connectionId: string, profile: string): Promise<ConnectionSwitchOutcome> {
  const target = JSON.stringify([connectionId, profile])

  if (pending?.target === target && isCurrentSourceSwitchIntent(pending.intent)) {return pending.promise}

  $organizationObservationSwitching.set(true)
  $organizationObservationEpoch.set($organizationObservationEpoch.get() + 1)
  const operation = selectConnectionWithOutcome(connectionId, { profile, preserveRoute: true })
  const entry = { target, intent: currentSourceSwitchIntent(), promise: operation }

  entry.promise = operation.finally(() => {
    if (pending === entry) {$organizationObservationSwitching.set(false)}

    if (pending === entry) {pending = undefined}
  })
  pending = entry

  return entry.promise
}

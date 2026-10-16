import { useStore } from '@nanostores/react'
import { useEffect, useId, useSyncExternalStore } from 'react'

import { Button } from '@/components/ui/button'
import { Loader } from '@/components/ui/loader'
import { useI18n } from '@/i18n/context'
import { $pendingConnectionId } from '@/store/connections'
import { $activeGatewayRoute, $gateway, $localOwnerProofRevision } from '@/store/gateway'
import { $organizationObservationEpoch, $organizationObservationSwitching } from '@/store/organization-observation-mode'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'
import { $connection, $gatewayState } from '@/store/session'

import { preparedObjectiveCopy } from './runtime-prepared-objective-copy'
import {
  $preparedObjectiveSelections,
  preparedObjectiveBlockingReasons,
  preparedObjectiveOwnerMatches,
  preparedObjectiveProjectMatches,
  preparedObjectiveReviewCurrent,
  readPreparedObjectiveRegistrations,
  reconcilePreparedObjectiveSelection,
  selectPreparedObjectiveReference,
  setPreparedObjectiveMode
} from './runtime-prepared-objective-state'
import { readPreparedValidationScope } from './runtime-prepared-validation-scope'
import type { ObjectiveMetadata, OrganizationSnapshot } from './types'
import { organizationMetadataClient } from './use-organization-metadata'

// Observe identity only. Availability reads remain deliberate owner actions.
const subscribeIdentity = (notify: () => void) => {
  const unsubscribes = [$pendingConnectionId, $activeGatewayRoute, $gateway, $localOwnerProofRevision,
    $organizationObservationEpoch, $organizationObservationSwitching, $activeGatewayProfile,
    $gatewaySwapTarget, $connection, $gatewayState].map(store => store.listen(notify))

  unsubscribes.push(organizationMetadataClient.subscribe(notify))

  return () => unsubscribes.forEach(unsubscribe => unsubscribe())
}

const identitySnapshot = () => {
  const scope = readPreparedValidationScope()

  return JSON.stringify([scope.key, scope.ownerKey, scope.profile, scope.eligible, scope.organizationId, scope.ownerId])
}

interface RuntimePreparedObjectiveSelectorProps {
  draftKey: string
  metadata: ObjectiveMetadata
  snapshot: OrganizationSnapshot
  getSnapshot(): OrganizationSnapshot
  disabled: boolean
}

export function RuntimePreparedObjectiveSelector({ draftKey, metadata, snapshot, getSnapshot, disabled }: RuntimePreparedObjectiveSelectorProps) {
  const { locale } = useI18n()
  const copy = preparedObjectiveCopy[locale]
  const states = useStore($preparedObjectiveSelections)
  const identityKey = useSyncExternalStore(subscribeIdentity, identitySnapshot)
  const state = states[draftKey]
  const context = () => ({ scope: readPreparedValidationScope(), snapshot: getSnapshot() })
  const current = preparedObjectiveReviewCurrent(state, { scope: readPreparedValidationScope(), snapshot })
  const eligible = preparedObjectiveOwnerMatches({ scope: readPreparedValidationScope(), snapshot })
  const descriptor = current ? state?.review?.descriptor : undefined

  const refs = descriptor?.registrationRefs.filter(reference => metadata.projectIds?.includes(reference.projectId) &&
    preparedObjectiveProjectMatches(descriptor, reference.projectId, snapshot)) ?? []

  const id = useId()

  // External authority changes invalidate reviewed capabilities without changing retained submission intent.
  useEffect(() => {
    reconcilePreparedObjectiveSelection(draftKey, { scope: readPreparedValidationScope(), snapshot })
  }, [draftKey, identityKey, snapshot])

  return <section aria-label={copy.title} className="eid-resolution-form">
    <h3>{copy.title}</h3>
    <label className="eid-inline" htmlFor={id}>
      <input checked={state?.requested ?? false} disabled={disabled} id={id}
        onChange={event => setPreparedObjectiveMode(draftKey, event.target.checked)} type="checkbox" />
      {copy.enable}
    </label>
    <p className="eid-note">{copy.activation}</p>
    <p className="eid-note">{copy.disabling}</p>
    {state?.requested && <>
      <p className="eid-note">{copy.boundaries}</p>
      <p className="eid-note">{copy.newOnly}</p>
      <Button disabled={disabled || state.busy || !eligible} onClick={() => void readPreparedObjectiveRegistrations(draftKey, context)} type="button" variant="secondary">
        {descriptor ? copy.refresh : copy.read}
      </Button>
      {state.busy && <Loader label={copy.reading} />}
      {!eligible && <p role="alert">{copy.unavailable}</p>}
      {state.error && <p role="alert">{copy[state.error]}</p>}
      {!current && state.review && <p role="alert">{copy.changed}</p>}
      {descriptor && (!descriptor.platform.supported || preparedObjectiveBlockingReasons(descriptor).length > 0) && <p role="alert">{copy.unsupported}</p>}
      {descriptor?.blockers.includes('prepared_registration_capacity') && <p className="eid-note">{copy.capacity}</p>}
      {!metadata.projectIds?.length && <p>{copy.selectProjects}</p>}
      {descriptor && !refs.length && <p>{copy.empty}</p>}
      <fieldset disabled={disabled || state.busy || !current || !descriptor?.platform.supported || !!descriptor && preparedObjectiveBlockingReasons(descriptor).length > 0}>
        <legend>{copy.selected}</legend>
        {refs.map(reference => <label className="eid-inline" key={reference.projectId}>
          <input checked={metadata.preparedValidations?.some(ref => ref.projectId === reference.projectId && ref.revision === reference.revision && ref.registrationSha256 === reference.registrationSha256) ?? false}
            onChange={event => selectPreparedObjectiveReference(draftKey, reference, event.target.checked, context())} type="checkbox" />
          <span className="min-w-0 break-all">{copy.choose}: {reference.projectId} · {copy.revision}: {reference.revision}<br />{copy.hash}: {reference.registrationSha256}</span>
        </label>)}
        {!metadata.preparedValidations?.length && <p className="eid-note">{copy.none}</p>}
      </fieldset>
    </>}
  </section>
}

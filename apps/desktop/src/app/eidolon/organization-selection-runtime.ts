import type { HermesGateway } from '@/eidolon'
import type { DesktopRegistryConnection, HermesConnection } from '@/global'
import { BACKEND_BOOT_WAIT_TIMEOUT_MS, withTimeout } from '@/lib/with-timeout'
import { $connectionsRegistry, $pendingConnectionId, type ConnectionSwitchOutcome, setConnectionsRegistry } from '@/store/connections'
import { $gateway, type GatewayActivationVerifier, gatewayTransportGeneration } from '@/store/gateway'
import {
  authorizeOrganizationApi, finishLegacyOrganizationDeparture, matchesOrganizationPresentation, type OrganizationSelectionIdentity, type OrganizationSelectionRecord,
  ownsPendingOrganizationSelection, promoteOrganizationSelection, readOrganizationBootSelection,
  showOrganizationSelectionRecovery, stageLegacyOrganizationDeparture, stageOrganizationSelection,
  validOrganizationSelectionIdentity
} from '@/store/organization-selection'
import { selectOrganizationConnection } from '@/store/organization-switch'
import { $gatewaySwapTarget } from '@/store/profile'
import { $connection } from '@/store/session'
import { currentSourceSwitchIntent, isCurrentSourceSwitchIntent } from '@/store/source-switch-intent'

import { verifyCreatedWorkspace } from './organization-creation-contract'
import type { MetadataDescriptor } from './organization-metadata-client'

interface SelectionProof {
  committed: boolean
  gateway: HermesGateway
  connection: HermesConnection
  generation: number
  registryRoute: string
  intent: number
  identity: OrganizationSelectionIdentity | null
  pending: OrganizationSelectionRecord | null
}
const proofs = new WeakMap<GatewayActivationVerifier, SelectionProof>()
/** Post-commit authority: installId is a display-only inventory hint, not routing. */
export function organizationRegistryRouteSignature(route: DesktopRegistryConnection | undefined): string {
  if (!route) { return '' }

  // Keep every other field, including unknown future auth/routing fields. Only
  // top-level key ordering is canonical; pre-reset registry fences stay exact.
  return JSON.stringify(Object.fromEntries(Object.entries(route).filter(([key]) => key !== 'installId').sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)))
}
const registeredRoute = (connectionId: string) => $connectionsRegistry.get()?.connections.find(item => item.id === connectionId)

function descriptorIdentity(value: unknown, connection: HermesConnection): OrganizationSelectionIdentity | null {
  const descriptor = value as MetadataDescriptor & { isolation?: unknown }

  if (descriptor?.capabilityVersion !== 1 || descriptor.scope?.kind !== 'served-profile' || descriptor.scope.profile !== connection.profile) {
    throw new Error('Organization scope could not be verified')
  }
  if (descriptor.state === 'unconfigured' && descriptor.metadata === null) { return null }
  const identity = {
    connectionId: connection.connectionId!, profile: connection.profile!,
    organizationId: descriptor.metadata?.organizationId, ownerId: descriptor.metadata?.owner?.ownerId,
    isolationVersion: descriptor.isolation === undefined ? null : 1
  }

  if (descriptor.state !== 'ready' || !validOrganizationSelectionIdentity(identity)) { throw new Error('Organization owner could not be verified') }
  if (identity.isolationVersion === 1) { verifyCreatedWorkspace(value, identity) }

  return identity
}

/** One proof and pending write per activation, never writes in its repeated final guard. */
export function persistedOrganizationVerifier(
  sourceCurrent: () => boolean,
  verify: (value: unknown) => void,
  expected?: OrganizationSelectionIdentity,
  startingRecord?: OrganizationSelectionRecord
): GatewayActivationVerifier {
  const verifier: GatewayActivationVerifier = async (gateway, connection, signal) => {
    const generation = gatewayTransportGeneration(gateway)
    const route = registeredRoute(connection.connectionId ?? '')
    const registry = $connectionsRegistry.get()
    const intent = currentSourceSwitchIntent()
    const current = () => sourceCurrent() && !signal.aborted && isCurrentSourceSwitchIntent(intent) &&
      gateway.connectionState === 'open' && gatewayTransportGeneration(gateway) === generation &&
      $connectionsRegistry.get() === registry

    if (!current() || !Number.isFinite(generation) || route?.kind !== 'local' || connection.mode !== 'local' ||
        !connection.registryScoped || connection.sharedPrimary || connection.sharedRemote) { throw new Error('Organization transport changed') }
    const descriptor = await gateway.request('organization.descriptor', {}, 15_000, signal)

    if (!current()) { throw new Error('Organization source changed') }
    verify(descriptor)
    const identity = descriptorIdentity(descriptor, connection)

    if (expected && (!identity || (Object.keys(expected) as (keyof OrganizationSelectionIdentity)[]).some(key => identity[key] !== expected[key]))) { throw new Error('Saved organization identity changed') }
    const saved = startingRecord ? readOrganizationBootSelection() : null
    if (!current() || (startingRecord && (saved?.kind !== 'record' || JSON.stringify(saved.record) !== JSON.stringify(startingRecord)))) {
      throw new Error('Organization selection changed before confirmation')
    }
    const pending = identity ? stageOrganizationSelection(identity) : stageLegacyOrganizationDeparture(connection.connectionId ?? null, connection.profile ?? 'default')
    const proof: SelectionProof = { committed: false, gateway, connection, generation: generation!, registryRoute: organizationRegistryRouteSignature(route), intent, identity, pending }
    proofs.set(verifier, proof)
    authorizeOrganizationApi(pending, () => (proof.committed ? !$pendingConnectionId.get() && !$gatewaySwapTarget.get() : isCurrentSourceSwitchIntent(intent)) && $gateway.get() === gateway &&
      matchesOrganizationPresentation($connection.get(), connection) && gateway.connectionState === 'open' && gatewayTransportGeneration(gateway) === generation &&
      organizationRegistryRouteSignature(registeredRoute(connection.connectionId!)) === proof.registryRoute, () => $connection.get())

    return () => {
      if (!current() || !ownsPendingOrganizationSelection(pending)) { return false }
      // The caller owns cancellation/disposal too. Repeat only its pure proof
      // validation, never the pending write, immediately around the reset.
      verify(descriptor)

      return current() && ownsPendingOrganizationSelection(pending)
    }
  }

  return verifier
}

/** Promotion checks destination facts directly: activation revokes its verifier signal on success. */
export async function selectRuntimeOrganizationConnection(connectionId: string, profile: string, verify?: GatewayActivationVerifier): Promise<ConnectionSwitchOutcome> {
  if (!verify) { throw new Error('Organization selection requires live verification') }
  const outcome = await selectOrganizationConnection(connectionId, profile, verify)
  const proof = proofs.get(verify)
  proofs.delete(verify)

  if (!proof) { return outcome }
  const current = () => isCurrentSourceSwitchIntent(proof.intent) && $gateway.get() === proof.gateway &&
    matchesOrganizationPresentation($connection.get(), proof.connection) && proof.gateway.connectionState === 'open' &&
    gatewayTransportGeneration(proof.gateway) === proof.generation &&
    organizationRegistryRouteSignature(registeredRoute(connectionId)) === proof.registryRoute &&
    proof.connection.connectionId === connectionId && proof.connection.profile === profile

  if (outcome.status !== 'committed' || !current()) {
    if (ownsPendingOrganizationSelection(proof.pending)) { showOrganizationSelectionRecovery('pending', proof.identity) }

    return outcome
  }

  proof.committed = true
  try {
    if (proof.identity) { promoteOrganizationSelection(proof.pending) }
    else { finishLegacyOrganizationDeparture(proof.pending) }
  } catch (error) {
    showOrganizationSelectionRecovery('storage', proof.identity)

    return { ...outcome, phase: 'bookkeeping', error }
  }

  return outcome
}

/** Cold restore never resolves or opens the window's default backend. */
export async function restoreOrganizationSelection(current: () => boolean, explicitRetry = false): Promise<boolean> {
  const saved = readOrganizationBootSelection()

  if (saved.kind === 'none') { return false }
  if (saved.kind !== 'record') {
    showOrganizationSelectionRecovery(saved.kind)
    throw new Error('Saved organization is unavailable')
  }
  const identity = saved.record.identity

  if (!identity || (saved.record.phase === 'pending' && !explicitRetry)) {
    showOrganizationSelectionRecovery('pending', identity)
    throw new Error('Organization selection needs confirmation')
  }

  const startingIntent = currentSourceSwitchIntent()
  let ownedIntent = startingIntent
  const ownsAttempt = () => current() && isCurrentSourceSwitchIntent(ownedIntent)
  const savedRecord = JSON.stringify(saved.record)

  try {
    const bridge = window.hermesDesktop?.connections

    if (!bridge) { throw new Error('Connection registry is unavailable') }
    const registry = await withTimeout(bridge.list(), BACKEND_BOOT_WAIT_TIMEOUT_MS, 'Timed out loading organization connections')

    const latest = readOrganizationBootSelection()
    if (!ownsAttempt() || latest.kind !== 'record' || JSON.stringify(latest.record) !== savedRecord) { throw new Error('Organization restore cancelled') }
    const route = registry.connections.find(item => item.id === identity.connectionId)

    if (route?.kind !== 'local') { throw new Error('Saved organization connection is unavailable') }
    setConnectionsRegistry(registry)
    const afterRegistry = readOrganizationBootSelection()
    if (!ownsAttempt() || afterRegistry.kind !== 'record' || JSON.stringify(afterRegistry.record) !== savedRecord) { throw new Error('Organization restore cancelled') }
    const verify = persistedOrganizationVerifier(current, () => undefined, identity, saved.record)
    const operation = selectRuntimeOrganizationConnection(identity.connectionId, identity.profile, verify)
    ownedIntent = currentSourceSwitchIntent()
    const result = await operation

    if (!ownsAttempt() || result.status !== 'committed' || result.error) { throw new Error('Saved organization could not be restored') }

    return true
  } catch (error) {
    if (ownsAttempt()) { showOrganizationSelectionRecovery('unavailable', identity) }
    throw error
  }
}

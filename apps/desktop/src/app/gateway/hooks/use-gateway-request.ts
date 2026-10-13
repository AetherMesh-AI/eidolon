import { isGatewayReauthRequired, resolveGatewayWsUrl } from '@aethermesh/shared'
import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef } from 'react'

import type { HermesGateway } from '@/eidolon'
import { RECONNECT_ATTEMPT_TIMEOUT_MS, withTimeout } from '@/lib/with-timeout'
import { $gateway, activeGatewayProfileKey, adoptPrimaryGatewayConnection, assertGatewayOAuthAuthority, assertOAuthConnectionAuthority, captureOAuthRequestAuthority, connectPrimaryGateway, ensureActiveGatewayOpen, gatewayActivationEpoch, gatewayTransportGeneration, isActivePrimary, isPrimaryGateway, requestGatewayWithRuntimeBinding } from '@/store/gateway'
import { $gatewaySwitching } from '@/store/gateway-switch'
import { $activeGatewayProfile, normalizeProfileKey } from '@/store/profile'
import { $connection, $gatewayState, setConnection } from '@/store/session'
import { currentSourceSwitchIntent, isCurrentSourceSwitchIntent } from '@/store/source-switch-intent'

export function useGatewayRequest() {
  const gatewayState = useStore($gatewayState)
  // Reactive companion to `gatewayRef`. The ref exists so `requestGateway`
  // keeps a stable identity and always reaches the live socket, but it is only
  // populated by the subscription effect below — i.e. AFTER the first render.
  // A component that reads `gatewayRef.current` while rendering therefore sees
  // null on mount, and if the connection state doesn't happen to flip
  // afterwards it never re-renders to pick the instance up. Anything that needs
  // the gateway as a render-time VALUE (props, memo deps) must use this.
  const gateway = useStore($gateway) as HermesGateway | null
  const gatewayRef = useRef<HermesGateway | null>(null)

  const connectionRef = useRef<Awaited<ReturnType<NonNullable<typeof window.hermesDesktop>['getConnection']>> | null>(
    null
  )

  const gatewayStateRef = useRef(gatewayState)
  const reconnectingRef = useRef<Promise<HermesGateway | null> | null>(null)
  // Holds the reauth error from the most recent failed reconnect so
  // requestGateway can surface the gateway's "session expired, sign in again"
  // message instead of the opaque "connection closed" that triggered the retry.
  const reauthErrorRef = useRef<unknown>(null)

  // eslint-disable-next-line no-restricted-syntax -- legitimate non-atom ref write (see eslint rule comment)
  useEffect(() => {
    gatewayStateRef.current = gatewayState
  }, [gatewayState])

  // Track the active gateway (primary or a background profile's socket) so
  // outbound requests and overlay props always target the focused profile.
  useEffect(
    () =>
      $gateway.subscribe(gateway => {
        gatewayRef.current = gateway as HermesGateway | null
      }),
    []
  )

  const ensureGatewayOpen = useCallback(async (authority = captureOAuthRequestAuthority()) => {
    const existing = gatewayRef.current

    if (!existing) {
      return null
    }

    if (gatewayStateRef.current === 'open' && existing.connectionState === 'open') {
      return existing
    }

    if (reconnectingRef.current) {
      return reconnectingRef.current
    }

    reconnectingRef.current = (async () => {
      const desktop = window.hermesDesktop

      if (!desktop) {
        return null
      }

      reauthErrorRef.current = null
      const intent = currentSourceSwitchIntent()
      const activationEpoch = gatewayActivationEpoch()
      const requestedProfile = normalizeProfileKey($activeGatewayProfile.get())
      const requestedConnection = $connection.get()
      const sharedMember = Boolean(requestedConnection?.sharedPrimary || requestedConnection?.sharedRemote)

      const ownsForeground = () => isCurrentSourceSwitchIntent(intent) &&
        gatewayActivationEpoch() === activationEpoch && isActivePrimary() &&
        normalizeProfileKey($activeGatewayProfile.get()) === requestedProfile &&
        isPrimaryGateway(existing) && !$gatewaySwitching.get()

      try {
        // This path owns the window primary. The descriptor must identify its
        // actual process, independently of a remembered or foreground profile.
        // Both awaits below are IPC round-trips
        // into the main process with no timeout of their own (#93454) — a
        // wedged main-process round-trip otherwise hangs this await forever,
        // latching reconnectingRef.current so every later requestGateway() call
        // returns the same never-settling promise. Bound the same way
        // use-gateway-boot.ts bounds the primary boot/soft-switch equivalents.
        const conn = await withTimeout(
          desktop.getConnection(),
          RECONNECT_ATTEMPT_TIMEOUT_MS,
          'Timed out reconnecting to Eidolon backend'
        )

        if (!ownsForeground()) {return null}
        assertOAuthConnectionAuthority(conn, authority)

        // Re-mint the WS URL before reconnecting. OAuth tickets are single-use
        // and short-lived, so the cached conn.wsUrl ticket is dead here;
        // resolveGatewayWsUrl() never connects with a stale ticket. An explicit
        // auth rejection becomes a reauth error; transport failures remain
        // retryable. Stash only the former so requestGateway can show the
        // actionable "sign in again" message.
        const wsUrl = await withTimeout(
          resolveGatewayWsUrl(desktop, conn),
          RECONNECT_ATTEMPT_TIMEOUT_MS,
          'Timed out re-minting the gateway WebSocket URL'
        )

        if (!ownsForeground()) {return null}
        assertOAuthConnectionAuthority(conn, authority)
        await connectPrimaryGateway(existing, conn, wsUrl)

        if (!ownsForeground()) {return null}

        let foregroundConnection = conn

        if (sharedMember) {
          foregroundConnection = await withTimeout(
            requestedConnection?.sharedRemote && requestedConnection.connectionId && desktop.getConnectionFor
              ? desktop.getConnectionFor({ connectionId: requestedConnection.connectionId, profile: requestedProfile })
              : desktop.getConnection(requestedProfile),
            RECONNECT_ATTEMPT_TIMEOUT_MS,
            'Timed out resolving the shared member after reconnect'
          )

          if (!ownsForeground()) {return null}

          // Same URL/source can still mean different credentials or header values.
          // Compare the captured descriptor identities, never current registry labels.
          if (!(foregroundConnection.sharedPrimary || foregroundConnection.sharedRemote) ||
              (conn.mode === 'remote' && !conn.authenticationIdentity) ||
              foregroundConnection.authenticationIdentity !== conn.authenticationIdentity ||
              requestedConnection?.authenticationIdentity !== conn.authenticationIdentity ||
              foregroundConnection.baseUrl !== conn.baseUrl ||
              foregroundConnection.connectionId !== conn.connectionId ||
              normalizeProfileKey(foregroundConnection.profile) !== requestedProfile) {
            throw new Error('Eidolon shared member route changed during reconnect')
          }
        }

        if (!adoptPrimaryGatewayConnection(existing, foregroundConnection, activationEpoch)) {return null}
        connectionRef.current = foregroundConnection

        // Recover the view, but never replay an old owner's operation in a
        // replacement process that serves a different profile.
        return normalizeProfileKey($activeGatewayProfile.get()) === requestedProfile ? existing : null
      } catch (error) {
        if (isGatewayReauthRequired(error)) {
          reauthErrorRef.current = error
        }

        if (ownsForeground()) {
          connectionRef.current = null
          setConnection(null)
        }

        return null
      } finally {
        reconnectingRef.current = null
      }
    })()

    return reconnectingRef.current
  }, [])

  const requestGateway = useCallback(
    async <T>(method: string, params: Record<string, unknown> = {}, timeoutMs?: number, signal?: AbortSignal) => {
      const authority = captureOAuthRequestAuthority()
      const gateway = gatewayRef.current

      if (!gateway) {
        throw new Error('Eidolon gateway unavailable')
      }

      // A failed RPC belongs to the foreground and transport that dispatched
      // it, not whichever profile happens to be selected when it rejects.
      const intent = currentSourceSwitchIntent()
      const activationEpoch = gatewayActivationEpoch()
      const profile = normalizeProfileKey($activeGatewayProfile.get())
      const connection = $connection.get()

      const scopeProfile = isActivePrimary() && normalizeProfileKey(connection?.profile) === profile &&
        Boolean(connection?.sharedPrimary || connection?.sharedRemote)

      if (isActivePrimary() && profile !== activeGatewayProfileKey() && !scopeProfile) {
        throw new Error('Eidolon selected profile has no verified shared primary route')
      }

      const generation = gatewayTransportGeneration(gateway)

      const ownsRequest = () => isCurrentSourceSwitchIntent(intent) &&
        gatewayActivationEpoch() === activationEpoch && normalizeProfileKey($activeGatewayProfile.get()) === profile &&
        gatewayRef.current === gateway && $gateway.get() === gateway &&
        gatewayTransportGeneration(gateway) === generation

      assertGatewayOAuthAuthority(gateway, authority)

      try {
        return await requestGatewayWithRuntimeBinding<T>(gateway, method, params, timeoutMs, signal, profile, scopeProfile)
      } catch (error) {
        if (!isGatewayTransportError(error) || !ownsRequest()) {
          throw error
        }

        // Primary keeps the OAuth-aware reconnect (remote gateways re-mint a
        // single-use ticket). Background profiles stay on the registry's
        // connection-owned reconnect path, including composite remote/SSH
        // sources.
        const recovered = isActivePrimary() ? await ensureGatewayOpen(authority) : await ensureActiveGatewayOpen()

        if (!recovered) {
          // Prefer the reauth error from the failed reconnect (OAuth session
          // expired) over the generic transport error that triggered the retry.
          const reauthError = reauthErrorRef.current
          reauthErrorRef.current = null

          if (reauthError) {
            throw reauthError
          }

          throw error
        }

        // Recovery may advance this owner's generation. It cannot grant an
        // old operation the authority of a newer source-selection intent.
        if (!isCurrentSourceSwitchIntent(intent) || normalizeProfileKey($activeGatewayProfile.get()) !== profile ||
            recovered !== gateway || gatewayRef.current !== gateway || $gateway.get() !== gateway) {throw error}

        assertGatewayOAuthAuthority(recovered, authority)

        return requestGatewayWithRuntimeBinding<T>(recovered, method, params, timeoutMs, signal, profile, scopeProfile)
      }
    },
    [ensureGatewayOpen]
  )

  return { connectionRef, gateway, gatewayRef, requestGateway }
}

const GATEWAY_TRANSPORT_ERROR_CODES = new Set([
  'ECONNABORTED',
  'ECONNREFUSED',
  'ECONNRESET',
  'EHOSTUNREACH',
  'ENETUNREACH',
  'ENOTFOUND',
  'EPIPE',
  'ETIMEDOUT',
  'ERR_NETWORK',
  'ERR_SOCKET_CLOSED'
])

function errorCode(value: unknown): string | null {
  if (typeof value !== 'object' || value === null) {
    return null
  }

  const code = (value as { code?: unknown }).code

  return typeof code === 'string' ? code.toUpperCase() : null
}

function isGatewayTransportError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error)

  if (/not connected|connection closed|connection reset|ECONNRESET/i.test(message)) {
    return true
  }

  const cause = typeof error === 'object' && error !== null ? (error as { cause?: unknown }).cause : undefined

  return [error, cause].some(value => {
    const code = errorCode(value)

    return code !== null && GATEWAY_TRANSPORT_ERROR_CODES.has(code)
  })
}

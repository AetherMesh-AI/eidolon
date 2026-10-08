import type { OrganizationRuntime } from './types'

/** Additive guidance must not break work/history on older or newer runtimes. */
export function validOrganizationSetup(value: unknown): value is NonNullable<OrganizationRuntime['setup']> {
  if (!value || typeof value !== 'object') {
    return false
  }

  const setup = value as Record<string, unknown>
  const provider = setup.provider as Record<string, unknown> | undefined

  return (
    setup.version === 1 &&
    typeof setup.backgroundOptIn === 'boolean' &&
    !!provider &&
    typeof provider === 'object' &&
    ['warning', 'unchecked'].includes(String(provider.status)) &&
    Array.isArray(provider.blockers) &&
    provider.blockers.length <= 1 &&
    provider.blockers.every(blocker => blocker === 'codex_app_server') &&
    (provider.status === 'warning') === provider.blockers.length > 0 &&
    [provider.inheritedMembers, provider.overriddenMembers].every(
      count => Number.isSafeInteger(count) && Number(count) >= 0
    )
  )
}

/** Preserve every explicit route, especially canonical profile-session links. */
export function organizationStartHash(hash: string): string {
  return hash || '#/home'
}

// Renderer-lifetime intent, deliberately not persisted: reloading an explicit
// Home URL must not turn it into a request to resume yesterday's chat.
let automaticHomeKey: string | null = null

/** Run before HashRouter mounts; only an unaddressed primary launch defaults Home. */
export function initializeOrganizationStart(): void {
  const { hash, pathname, search } = window.location
  automaticHomeKey = null

  if (!new URLSearchParams(search).get('win') && !hash) {
    // HashRouter uses `default` for the initial entry when no key exists yet.
    automaticHomeKey = window.history.state?.key ?? 'default'
    window.history.replaceState(window.history.state, '', `${pathname}${search}${organizationStartHash(hash)}`)
  }
}

export function isAutomaticHomeStart(
  pathname: string,
  locationKey: string = window.history.state?.key ?? 'default'
): boolean {
  // A click on Home also creates a new router key, even at the same pathname.
  // Retire the startup intent on any observed navigation so back/forward cannot
  // revive it while profile/config readiness is still pending.
  if (pathname !== '/home' || locationKey !== automaticHomeKey) {
    automaticHomeKey = null
  }

  return automaticHomeKey !== null
}

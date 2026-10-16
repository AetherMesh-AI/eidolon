/** Presentation-only routes. Never accept an arbitrary URL as a return target. */
const returnPaths = new Set(['/home', '/work', '/objectives', '/messages', '/requests', '/artifacts'])

export function workspaceReturnTarget(value: string | null): string | null {
  if (!value || value.length > 4096 || !value.startsWith('/') || value.startsWith('//') || [...value].some(character => character === '\\' || character.charCodeAt(0) < 32)) {return null}

  const url = new URL(value, 'https://workspace.invalid')

  return url.origin === 'https://workspace.invalid' && returnPaths.has(url.pathname) && !url.hash
    ? `${url.pathname}${url.search}`
    : null
}

export function objectiveHref(objectiveId: string, origin: string, ownerScope?: string, requestId?: string): string {
  const params = new URLSearchParams()
  const from = workspaceReturnTarget(origin)

  if (from && ownerScope) {params.set('from', from); params.set('originOwner', ownerScope)}

  if (requestId && ownerScope) {params.set('request', requestId); params.set('requestOwner', ownerScope)}

  const query = params.toString()

  return `/objectives/${encodeURIComponent(objectiveId)}${query ? `?${query}` : ''}`
}

export function objectiveReturnTarget(params: URLSearchParams, ownerScope?: string): string {
  return ownerScope && params.get('originOwner') === ownerScope
    ? workspaceReturnTarget(params.get('from')) ?? '/objectives'
    : '/objectives'
}

/** Normal provider discovery probes are reads; their mock 404 is not a fixture error. */
const catalogPaths = new Set([
  '/v1/models',
  // agent/model_metadata.py detects local servers before context-length lookup.
  '/api/v1/models',
  '/api/tags',
  '/v1/props',
  '/props',
  '/version'
])

export function organizationProviderTarget(mockUrl: string, method: string | undefined, target: string | undefined): URL {
  const base = new URL(mockUrl)
  if (base.protocol !== 'http:' || base.hostname !== '127.0.0.1' || !base.port || base.username || base.password || base.pathname !== '/' || base.search || base.hash) {
    throw new Error('Organization provider requires its existing loopback mock origin')
  }
  // Require HTTP origin-form before URL parsing, which otherwise accepts an
  // absolute URL, //host, or backslash-normalized authority as a new recipient.
  if (!target?.startsWith('/') || target.startsWith('//') || /[\\\s#]/.test(target)) {
    throw new Error('Unexpected provider request target')
  }
  const destination = new URL(target, base)
  if (destination.origin !== base.origin || destination.username || destination.password) {
    throw new Error('Provider forwarding must stay on its existing mock origin')
  }
  const catalogRead = method === 'GET' && (catalogPaths.has(destination.pathname) || /^\/v1\/models\/[^/]+$/.test(destination.pathname))
  // The provider-agnostic Ollama capability/context probe is a read via POST.
  const modelCapabilities = method === 'POST' && destination.pathname === '/api/show'
  const completion = method === 'POST' && destination.pathname === '/v1/chat/completions'
  if (!catalogRead && !modelCapabilities && !completion) {
    throw new Error(`Unexpected provider endpoint: ${method ?? 'unknown'} ${destination.pathname}`)
  }
  return destination
}

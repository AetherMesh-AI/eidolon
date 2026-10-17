/** Synthetic settings catalogs only. Never opens credentials or contacts a provider. */
const empty = {
  env: {},
  accounts: { providers: [] },
  archives: { sessions: [], total: 0, limit: 200, offset: 0 },
  endpoints: { endpoints: [], current: { base_url: '', model: '', provider: '' } }
}
const field = category => ({
  advanced: false, category, description: 'Synthetic unsaved draft for read-only recovery proof.',
  is_password: false, is_set: false, redacted_value: null, tools: [], url: ''
})
const populated = {
  env: {
    SYNTHETIC_TOOL_API_KEY: field('tool'),
    SYNTHETIC_PROVIDER_API_KEY: { ...field('provider'), provider: 'synthetic', provider_label: 'Synthetic Provider' }
  },
  accounts: { providers: [{ id: 'synthetic', name: 'Synthetic connected account', cli_command: '', docs_url: '',
    flow: 'external', disconnectable: false, status: { logged_in: true } }] },
  archives: { ...empty.archives, total: 1, sessions: [{ id: 'synthetic-archive', title: 'Synthetic archived conversation',
    profile: 'default', archived: true, ended_at: 1700000001, started_at: 1700000000, last_active: 1700000001,
    input_tokens: 0, output_tokens: 0, is_active: false, message_count: 2, model: 'synthetic', preview: 'Synthetic archived metadata.',
    source: 'desktop', tool_call_count: 0, cwd: '/synthetic/workspace' }] },
  endpoints: { ...empty.endpoints, endpoints: [{ id: 'synthetic-endpoint', name: 'Synthetic endpoint',
    base_url: 'https://synthetic.invalid/v1', model: 'synthetic-model', models: [], discover_models: false,
    has_api_key: false, is_current: false }] }
}

const model = {
  info: { provider: 'synthetic', model: 'synthetic-current' },
  options: { providers: [{ name: 'Synthetic Model Provider', slug: 'synthetic', authenticated: true,
    models: ['synthetic-current', 'synthetic-unsaved'] }] },
  auxiliary: { main: { provider: 'synthetic', model: 'synthetic-current' }, tasks: [] }
}
module.exports = { empty, populated, model }
if (process.versions.electron) {
  require('./http-read-fault-bootstrap.cjs')
  const control = globalThis.__eidolonReadFaults
  for (const [path, data] of [
    ['/api/env', empty.env], ['/api/providers/oauth', empty.accounts],
    ['/api/profiles/sessions', empty.archives], ['/api/providers/custom-endpoints', empty.endpoints]
  ]) control.set(path, { kind: 'success', data })
  // Own the whole model subtree so accidental assignments are rejected and recorded.
  control.set('/api/model', { kind: 'error' })
  for (const [path, data] of [
    ['/api/model/info', model.info], ['/api/model/options', model.options],
    ['/api/model/auxiliary', model.auxiliary], ['/api/model/moa', null]
  ]) control.set(path, { kind: 'success', data })
  // Validation and authentication are actions, never part of a read retry.
  for (const path of ['/api/providers/validate', '/api/providers/oauth/start']) control.set(path, { kind: 'error' })
}

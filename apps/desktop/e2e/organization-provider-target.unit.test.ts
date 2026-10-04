import { expect, it } from 'vitest'

import { MOCK_REPLY, startMockServer } from './mock-server'
import { organizationProviderTarget } from './organization-provider-target'

it('preserves real mock catalog, inference and negative local-capability probe responses', async () => {
  const mock = await startMockServer()
  try {
    const models = await fetch(organizationProviderTarget(mock.url, 'GET', '/v1/models'))
    expect(models.status).toBe(200)
    expect(await models.json()).toMatchObject({ data: [{ id: 'mock-model' }] })
    for (const path of ['/api/v1/models', '/api/tags', '/v1/props', '/props', '/props?model=mock-model', '/version', '/v1/models/mock-model']) {
      const target = organizationProviderTarget(mock.url, 'GET', path)
      expect(target.origin).toBe(mock.url)
      const response = await fetch(target)
      expect(response.status).toBe(404)
    }
    const capabilities = await fetch(organizationProviderTarget(mock.url, 'POST', '/api/show'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'mock-model' })
    })
    expect(capabilities.status).toBe(404)
    const response = await fetch(organizationProviderTarget(mock.url, 'POST', '/v1/chat/completions'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: 'mock-model', messages: [{ role: 'user', content: 'Baseline readiness' }] })
    })
    expect(response.status).toBe(200)
    expect(await response.json()).toMatchObject({ choices: [{ message: { content: MOCK_REPLY } }] })
  } finally {
    await mock.close()
  }
})

it('rejects authority changes and non-discovery actions before any forwarding', () => {
  const origin = 'http://127.0.0.1:12345'
  for (const target of [undefined, 'http://example.invalid/v1/models', 'http://127.0.0.1:12346/v1/models', '//example.invalid/v1/models', '/\\example.invalid/v1/models', '/v1/models#fragment', '/v1/models\n', '/v1/files', '/v1/../secrets', '/api/v1/models/load']) {
    expect(() => organizationProviderTarget(origin, 'GET', target)).toThrow()
  }
  expect(() => organizationProviderTarget(origin, 'POST', '/api/v1/models')).toThrow()
  expect(() => organizationProviderTarget(origin, 'DELETE', '/v1/models/mock-model')).toThrow()
  expect(() => organizationProviderTarget('https://example.invalid', 'GET', '/v1/models')).toThrow()
  expect(() => organizationProviderTarget('http://user:password@127.0.0.1:12345', 'GET', '/v1/models')).toThrow()
})

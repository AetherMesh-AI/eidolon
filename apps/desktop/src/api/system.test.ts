import { expect, it, vi } from 'vitest'

vi.mock('./client', () => ({
  capabilityScoped: vi.fn(),
  hermesApi: vi.fn(),
  profileScoped: () => ({ profile: 'work', connectionId: 'remote-work' })
}))

const { hermesApi } = await import('./client')
const { checkHermesUpdate, updateHermes } = await import('./system')

it('routes backend updates through the canonical endpoint with the selected connection scope', async () => {
  await checkHermesUpdate()
  await checkHermesUpdate(true)
  await updateHermes()

  expect(vi.mocked(hermesApi).mock.calls.map(([request]) => request)).toEqual([
    { connectionId: 'remote-work', profile: 'work', path: '/api/eidolon/update/check' },
    { connectionId: 'remote-work', profile: 'work', path: '/api/eidolon/update/check?force=true' },
    { connectionId: 'remote-work', profile: 'work', path: '/api/eidolon/update', method: 'POST' }
  ])
})

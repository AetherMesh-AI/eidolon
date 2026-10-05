import { expect, it } from 'vitest'

import { mockProviderConfig } from './mock-provider-config'

it('keeps the generated primary-model and named-provider windows equal to the explicit fixture limit', () => {
  for (const contextWindow of [128000, 256000]) {
    const generated = mockProviderConfig(
      'http://127.0.0.1:1234',
      undefined,
      'organization:\n  max_context_tokens: 32768',
      contextWindow,
      false
    )

    // These are the generated YAML fields consumed by the runtime, not source
    // text. The route and default-model paths must describe the same window.
    const modelSection = generated.split('\nproviders:\n')[0]
    const providerSection = generated.split('\nproviders:\n')[1].split('\nauxiliary:\n')[0]
    const readWindow = (section: string) => Number(section.match(/context_length: (\d+)/)?.[1])
    expect(readWindow(modelSection)).toBe(contextWindow)
    expect(readWindow(providerSection)).toBe(readWindow(modelSection))
    expect(modelSection).toContain('streaming: false')
    expect(generated).toContain('max_context_tokens: 32768')
  }
})

it('preserves default fixture behavior when no model overrides were requested', () => {
  const generated = mockProviderConfig('http://127.0.0.1:1234')
  const [modelSection, providerSection] = generated.split('\nproviders:\n')
  expect(modelSection).not.toContain('context_length:')
  expect(modelSection).not.toContain('streaming:')
  expect(providerSection).toContain('context_length: 4096')
  expect(generated).toContain('title_generation:\n    enabled: false')
  expect(generated).toContain('approvals:\n  mode: "off"')
})

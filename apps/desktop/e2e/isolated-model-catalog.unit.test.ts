import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { expect, it } from 'vitest'

import { seedIsolatedModelCatalog } from './isolated-model-catalog'
import { mockProviderConfig } from './mock-provider-config'

it('seeds a valid synthetic registry while preserving scenario settings and loopback provider discovery', () => {
  const home = mkdtempSync(join(tmpdir(), 'isolated-model-catalog-'))

  try {
    const extraConfig = seedIsolatedModelCatalog(home, 'checkpoints:\n  enabled: false\nsettings_fixture_marker: retained\n')
    const config = mockProviderConfig('http://127.0.0.1:1234', '  language: zh-hant', extraConfig)
    const registry = JSON.parse(readFileSync(join(home, 'models_dev_cache.json'), 'utf8'))
    const manifest = JSON.parse(readFileSync(join(home, 'cache/model_catalog.json'), 'utf8'))
    const catalog = JSON.parse(config.split('\nmodel_catalog: ')[1].trim())

    expect(registry.mock.models['mock-model'].id).toBe('mock-model')
    expect(manifest.providers.mock.models.map((model: { id: string }) => model.id)).toEqual(['mock-model'])
    expect(catalog.excluded_providers).toContain('opencode-free')
    expect(config).toContain('api: http://127.0.0.1:1234/v1')
    expect(config).not.toContain('discover_models: false')
    expect(config).toContain('language: zh-hant')
    expect(config).toContain('checkpoints:\n  enabled: false\nsettings_fixture_marker: retained')
    expect(() => seedIsolatedModelCatalog(home, 'model_catalog:\n  excluded_providers: []'))
      .toThrow('cannot override a scenario model_catalog section')
    expect(mockProviderConfig('http://127.0.0.1:1234')).not.toContain('model_catalog:')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

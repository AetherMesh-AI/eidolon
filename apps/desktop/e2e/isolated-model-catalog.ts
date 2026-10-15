import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const fixture = JSON.parse(readFileSync(new URL('./isolated-model-catalog.json', import.meta.url), 'utf8')) as {
  config: { model_catalog: { excluded_providers: string[] } }
  manifest: Record<string, unknown>
  registry: Record<string, unknown>
}

/** Visual fixtures exercise the real catalog route with a fresh synthetic registry.
 * Unrelated keyless discovery otherwise adds external HTTP latency to a loopback test. */
export function seedIsolatedModelCatalog(hermesHome: string, extraConfig = ''): string {
  if (/^model_catalog\s*:/m.test(extraConfig)) {
    throw new Error('An isolated model catalog cannot override a scenario model_catalog section')
  }

  writeFileSync(join(hermesHome, 'models_dev_cache.json'), JSON.stringify(fixture.registry))
  mkdirSync(join(hermesHome, 'cache'), { recursive: true })
  writeFileSync(join(hermesHome, 'cache/model_catalog.json'), JSON.stringify(fixture.manifest))
  // JSON is valid YAML; retain the scenario's other top-level settings verbatim.
  return `${extraConfig}\nmodel_catalog: ${JSON.stringify(fixture.config.model_catalog)}\n`
}

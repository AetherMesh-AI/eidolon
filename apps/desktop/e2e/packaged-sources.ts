/** The acceptance harness and actual-main product are independently pinned. */
import { createRequire } from 'node:module'
import path from 'node:path'

export interface PackagedSource {
  root: string
  commit: string
  tree: string
  clean: true
}

export interface PackagedSources {
  product: PackagedSource & { mainTip: string }
  harness: PackagedSource
  eventCommit: string
  freshness: { repository: string; ref: string; remoteTip: string; verifiedAt: string }
}

const gate = createRequire(import.meta.url)('../scripts/check-packaged-macos-proof.cjs') as {
  readSources: (harnessRoot: string) => PackagedSources
}

export function packagedSources(): PackagedSources {
  return gate.readSources(path.resolve(import.meta.dirname, '../../..'))
}

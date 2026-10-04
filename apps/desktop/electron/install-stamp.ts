import fs from 'node:fs'

import desktopPackage from '../package.json'

const RELEASE_VERSION = desktopPackage.version

const INSTALL_STAMP_SCHEMA_VERSION = 1

type VersionStamp = {
  schemaVersion?: number; version?: string | null; channel?: string | null; commit?: string
  shortCommit?: string; dirty?: boolean | null; versionSource?: string
  baseTag?: string; baseCommit?: string; distance?: number
  repository?: string; updateBranch?: string
}

function verifiedVersion(stamp: VersionStamp | null): boolean {
  if (!stamp || stamp.schemaVersion !== 1 || stamp.channel !== 'alpha' ||
      stamp.repository !== 'AetherMesh-AI/Eidolon' || stamp.updateBranch !== 'main' ||
      !['git-derived', 'stamp'].includes(stamp.versionSource || '') ||
      !/^[0-9a-f]{40}$/.test(stamp.commit || '') || /^0+$/.test(stamp.commit || '') ||
      !/^[0-9a-f]{40}$/.test(stamp.baseCommit || '') || /^0+$/.test(stamp.baseCommit || '') ||
      stamp.shortCommit !== stamp.commit?.slice(0, 12) ||
      !Number.isSafeInteger(stamp.distance) || (stamp.distance ?? -1) < 0 ||
      ![true, false, null].includes(stamp.dirty as boolean | null)) {return false}
  const base = /^alpha-v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.exec(stamp.baseTag || '')
  return !!base && (stamp.distance === 0) === (stamp.commit === stamp.baseCommit) &&
    stamp.version === RELEASE_VERSION
}

/** Packaged location first, dev build second; unknown dirtiness stays unknown. */
export function readInstallStampFromPaths(candidates: string[], warn = console.warn) {
  for (const p of candidates) {
    try {
      const parsed = JSON.parse(fs.readFileSync(p, 'utf8'))
      if (parsed && typeof parsed === 'object' && typeof parsed.commit === 'string' && parsed.commit.length >= 7) {
        if (parsed.schemaVersion !== INSTALL_STAMP_SCHEMA_VERSION) {
          warn(`[eidolon] install-stamp.json schemaVersion ${parsed.schemaVersion} != expected ${INSTALL_STAMP_SCHEMA_VERSION}; ignoring`)
          continue
        }
        return Object.freeze({
          schemaVersion: parsed.schemaVersion,
          commit: parsed.commit,
          branch: parsed.branch || null,
          builtAt: parsed.builtAt || null,
          dirty: typeof parsed.dirty === 'boolean' ? parsed.dirty : null,
          source: parsed.source || null,
          version: verifiedVersion(parsed) ? parsed.version as string : null,
          channel: verifiedVersion(parsed) ? 'alpha' : null,
          versionSource: verifiedVersion(parsed) ? parsed.versionSource as string : 'fallback',
          shortCommit: parsed.shortCommit,
          repository: parsed.repository,
          updateBranch: parsed.updateBranch,
          baseTag: parsed.baseTag,
          baseCommit: parsed.baseCommit,

          distance: parsed.distance,
          path: p
        })
      }
    } catch (e) {
      warn(`[eidolon] install-stamp.json found at ${p} , but parsing failed with ${e}`)
    }
  }
  return null
}

export function formatInstallStamp(stamp: { commit: string; branch?: string | null; dirty?: boolean | null; source?: string | null }) {
  const dirty = stamp.dirty === true ? ' [DIRTY]' : stamp.dirty === false ? '' : ' [DIRTY UNKNOWN]'
  return `${stamp.commit.slice(0, 12)}${stamp.branch ? ` (${stamp.branch})` : ''}${dirty} from ${stamp.source || 'unknown'}`
}

/** Shared by the renderer version IPC, including absent/legacy stamp cases. */
export function formatInstallVersion(stamp: VersionStamp | null) {
  const commit = stamp?.commit
  const exact = typeof commit === 'string' && /^[0-9a-f]{40}$/.test(commit) && !/^0+$/.test(commit)
  const dirty = stamp?.dirty === true ? ' (dirty source)' : stamp?.dirty === false ? '' : ' (source status unknown)'
  const verified = verifiedVersion(stamp)
  return `${verified ? stamp!.version : RELEASE_VERSION} · ${exact ? commit.slice(0, 12) : 'unknown commit'}${dirty}${verified ? '' : ' (build provenance unverified)'}`
}

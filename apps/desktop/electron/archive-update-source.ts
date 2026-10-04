import fs from 'node:fs'
import path from 'node:path'

const ORIGIN = 'https://github.com/AetherMesh-AI/Eidolon.git'
const SHA = /^[0-9a-f]{40}$/
type Git = (args: string[], options?: { cwd?: string }) => Promise<{ code: number; stdout: string; stderr: string }>

/** Read-only release check: equal commits still require archive migration. */
export async function checkArchiveUpdate({ runGit, currentSha, archiveRoot }: {
  runGit: Git; currentSha?: string; archiveRoot: string
}) {
  const result = await runGit(['ls-remote', '--exit-code', ORIGIN, 'refs/heads/main'])
  const targetSha = result.stdout.trim().split(/\s+/)[0]
  if (result.code !== 0 || !SHA.test(targetSha)) {
    return { supported: true, error: 'fetch-failed', message: result.stderr || 'Cannot resolve Eidolon main.', hermesRoot: archiveRoot }
  }
  return { supported: true, branch: 'main', currentSha, targetSha, behind: null,
    updateAvailable: true, migrationRequired: true, dirty: false, commits: [],
    hermesRoot: archiveRoot, fetchedAt: Date.now() }
}

/** A source readiness pointer, not installer/executable ownership metadata. */
export function readPreparedSource(hermesHome: string): string | null {
  const parent = path.join(hermesHome, 'desktop-source')
  try {
    const { root } = JSON.parse(fs.readFileSync(path.join(parent, 'active.json'), 'utf8'))
    if (typeof root !== 'string' || path.dirname(root) !== parent || !path.basename(root).startsWith('checkout-')) {return null}
    if (!fs.statSync(path.join(root, '.git')).isDirectory()) {return null}
    const python = process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python'
    return fs.existsSync(path.join(root, python)) ? root : null
  } catch {
    return null
  }
}

/** Shared by backend launch and tested with a real disposable runtime. */
export function preparedSourceBackend<T>(hermesHome: string, createBackend: (root: string) => T | null): T | null {
  const root = readPreparedSource(hermesHome)
  return root ? createBackend(root) : null
}

/** Production apply caller: refusal happens before any source or tool writes. */
export async function prepareArchiveUpdateWithConsent(options: Parameters<typeof prepareArchiveUpdate>[0] & {
  confirm: () => Promise<boolean>
}) {
  if (!await options.confirm()) {return { ok: false, error: 'Source preparation cancelled.' }}
  try {
    const root = await prepareArchiveUpdate(options)
    return { ok: true, root }
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) }
  }
}

/** Prepare away from the active archive; publish only after bootstrap succeeds. */
export async function prepareArchiveUpdate({ hermesHome, runGit, prepare }: {
  hermesHome: string; runGit: Git; prepare: (root: string) => Promise<{ ok: boolean; error?: string }>
}) {
  const parent = path.join(hermesHome, 'desktop-source')
  fs.mkdirSync(parent, { recursive: true })
  const root = fs.mkdtempSync(path.join(parent, 'checkout-'))
  const git = async (args: string[]) => {
    const result = await runGit(args, { cwd: root })
    if (result.code !== 0) {throw new Error(result.stderr || `git ${args[0]} failed`)}
    return result.stdout.trim()
  }
  const temporary = path.join(parent, `active-${path.basename(root)}.json`)
  try {
    // No depth/filter: deterministic 0.1.x versions need complete first-parent history.
    await git(['clone', '--branch', 'main', '--single-branch', '--', ORIGIN, '.'])
    if (await git(['config', '--get', 'remote.origin.url']) !== ORIGIN ||
        await git(['rev-parse', '--is-shallow-repository']) !== 'false' ||
        !SHA.test(await git(['rev-parse', 'HEAD']))) {throw new Error('Invalid Eidolon checkout')}
    const result = await prepare(root)
    if (!result.ok) {throw new Error(result.error || 'Source runtime preparation failed')}
    const python = process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python'
    if (!fs.existsSync(path.join(root, python))) {throw new Error('Prepared source has no Python runtime')}
    fs.writeFileSync(temporary, JSON.stringify({ root }))
    fs.renameSync(temporary, path.join(parent, 'active.json'))
  } catch (error) {
    // Only this unpublished attempt belongs to this failure path. Never sweep
    // sibling checkouts or mask the preparation/publication error with cleanup.
    try { fs.rmSync(temporary, { force: true }) } catch { /* Keep the preparation failure authoritative. */ }
    try { fs.rmSync(root, { recursive: true, force: true }) } catch { /* Unpublished cleanup is best-effort. */ }
    throw error
  }
  return root
}

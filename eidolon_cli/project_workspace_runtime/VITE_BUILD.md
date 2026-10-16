# Experimental renderer build metadata protocol

The `workspace_vite_build` / `project_build` step belongs only to a version 2
three-recipe validation profile. Version 1's TypeScript/UI pair and its retained
receipt shape remain unchanged. The build step uses the existing common step
fields, with empty `testFiles` and `sourceDirectories`. Its input is the selected
workspace's exact `index.html`, alongside the authored Vite configuration.

The isolated harness imports the inventoried
`node_modules/vite/dist/node/index.js` API. With the pinned Vite 8 API it calls
`createBuilder(options, true)` and builds the sole `client` environment. The
actual configuration is loaded with `configLoader: runner`; authored plugins,
aliases, CSS processing and chunking remain active. Fixed options select
production mode, disable env-file loading and environment prefixes, use
scratch-local cache/output directories, require `write: true`, and disable
watch, SSR, library builds and Vite DevTools. Resolved configuration, environment and build-start
checks reject changes to that scope. The original source and prepared dependencies
remain read-only. No package script, npm lifecycle, install, image pull,
credential lookup, network permission or host fallback is added.

## Metadata version 1

A build outcome retains the ordinary identity/count fields with zero test/source
counts and empty selected/reported/module lists. Only build outcomes add:

- `outputManifestVersion: 1`
- `outputManifest: [{path, bytes, sha256, kind}, ...]`
- `outputManifestSha256`
- `outputBodiesRetained: false`

Paths are relative to `/tmp/vite-build/out`, canonical NFC UTF-8, at most 512
bytes and 32 components. Entries are strictly sorted by UTF-8 path bytes and
reject duplicate paths, case aliases and file/directory collisions. Each kind
is `chunk` or `asset`: emitted types come from the Vite/Rolldown output claim;
additional physical files, including public-directory copies, are assets. A
`.js` suffix never makes an unreported file a chunk. Every reported emitted
file must be physically inventoried. A passing build requires a nonempty
`index.html` asset, a nonempty chunk, and a reported entry chunk.

Collection accepts regular files only, with one link each and distinct inode
identities; symlinks, linked directories, hardlinks and special files fail.
Files are opened no-follow/nonblocking and hashed as binary bytes in bounded
chunks, with metadata checks before and after reading. Directory identity and
metadata are checked around traversal. Bounds fail closed without truncation:

- 1,024 regular files
- 32 MiB per file
- 128 MiB total file bytes
- 4,096 visited directory entries, including directories
- 256 KiB canonical manifest JSON

The aggregate digest is SHA-256 of the manifest's compact UTF-8 JSON with each
entry's keys sorted (`bytes`, `kind`, `path`, `sha256`). The existing separate
1 MiB control-frame limit and profile log/scratch/CPU/PID/time/memory limits are
unchanged. Failure defaults contain an empty manifest and its digest. Only the
full bounded metadata claim is retained in the host receipt; file bodies are
never exported from scratch.

## Trust and cleanup

All collection runs in the workload that has already imported project code.
Its metadata and hashes are explicitly `evidenceOrigin: workload-claim`, even
when transported and checked by the trusted supervisor. They are not a trusted
collector signature, proof of honest compilation/assertions, or immutable
artifact-copy proof. Descendants can remain active until the namespace's PID 1
exits; consistency checks do not establish global quiescence. No output tree is
copied to the host, published or used as a deliverable.

The existing supervisor and host must independently establish terminal state,
container removal and private staging cleanup. A plausible manifest cannot
replace those observations, and cleanup uncertainty still blocks replay and
successful receipt acceptance. Independent review and the complete current
three-recipe validation set remain necessary.

Pure protocol/filesystem tests exercise bounds and binary/public-file handling.
They do not establish native Vite compatibility. The real renderer, authored
config/plugins, failures and hostile configuration probes must pass in the
pinned sandbox before native support is claimed.

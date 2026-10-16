# Prepared workspace dependency artifacts

`prepare_workspace_dependencies.py` inventories a preexisting installation. It
never runs npm, imports package code, downloads packages, or activates a runtime.
Both modes require an exact absolute repository path and a new output directory
outside that repository. The output's existing parent must be owned by the caller
and private (`0700`). Existing artifacts are never overwritten.

## Candidate inspection

Pass `--repository`, `--output`, and `--candidate`. This mode accepts no runtime
or installation claims and writes only `candidate.json`, with no dependency
payload copies. It records the complete bounded file inventory, candidate native
path/hash/format policy, and verified omitted workspace and npm bin links.
Preparation provenance is explicitly unknown, and execution authorization and
runtime compatibility remain false.

This supports reviewing an existing tree without pretending it came from an
approved installation. It still rejects unknown links, special files, hardlinks,
unowned cache files, lock mismatches, ambiguous paths, and inventory overflow.
Empty directories have no representation in the file-only bundle format. Source
modes with owner-read and no special bits or group/other write are normalized in
the inventory to `0444` or `0555`; the original files are never modified.

## Canonical bundle preparation

Pass `--repository`, `--output`, `--native-policy`, `--runtime-metadata`, and
`--preparation-record`. The output contains `bundle/`, `manifest.json`,
`toolchain.json`, `preparation-record.json`, and `receipt.json`.

The supplied installation record must have these exact fields:

- `schemaVersion`: integer `1`
- `command`: `npm`, `ci`, `--ignore-scripts`, `--no-audit`, `--no-fund`, and
  `--bin-links=false`, as an ordered JSON array
- `exitCode`: integer `0`
- `lockfileSha256`: SHA-256 of the exact raw root `package-lock.json`
- `runtimeMetadataSha256`: SHA-256 of canonical runtime metadata JSON
- `registry`: `https://registry.npmjs.org`
- `lifecycleScripts`: `false`

Runtime metadata must contain exactly `schemaVersion`, `image`, `platform`,
`arch`, `libc`, `glibcVersion`, `nodeVersion`, `nodeAbi`, and `npmVersion`.
Its values must describe observations recorded by the preparation workflow in
the existing pinned Node container: Linux, x64, positively identified glibc,
exact Node and npm versions, exact Node module ABI, and the pinned image digest.
The generator does not substitute its own host Node metadata. Tool versions for
TypeScript, Vitest and Vite come from the inventoried root packages and must
match the exact lockfile. The workload supervisor separately verifies its actual
runtime; this metadata does not establish that execution will work.

The native policy is an object with `schemaVersion: 1` and `files`, an array of
exact `{path, sha256, format}` entries. Supported inventory formats are `elf`,
`pe`, `macho`, `wasm`, and `archive`. Every detected native file, including
retained foreign-platform payloads, must match the reviewed policy. No native
file is executed, silently omitted, or represented as ABI-compatible merely
because it was inventoried. Candidate output is material for review, not an
approval to prepare or execute its contents.

Only `apps/desktop` and `apps/shared` are selected. Root dependencies and their
nested package trees, plus these workspaces' nested installations, are retained.
Published payload directories such as `dist/node_modules/.pnpm/` stay owned by
their enclosing locked package and retain every file. Actual nested installed
packages still require their own exact lock record and package manifest.
Exact lock-declared links to other workspaces may be omitted and are recorded.
Selected workspace links become inert mappings for later source assembly.
Canonical preparation rejects npm bin links because the reviewed installation
command disables them; runtime tools use fixed entry files. Candidate inspection
can validate existing bin links against exact package declarations, including
workspace declarations, before recording their omission.

Canonical JSON uses sorted keys, ASCII escapes, compact comma/colon separators,
and no trailing newline. Registry URLs and integrity declarations are checked
against the lockfile, but no archive is fetched or authenticated. The returned
receipt therefore retains `provenanceAuthenticated: false`,
`executionAuthorized: false`, and `runtimeCompatibilityVerified: false`.
Real native sandbox acceptance is a separate required gate.

## Reviewed CI renderer policy

The experimental workflow pins the native-file inventory from candidate run
38829761731, head `bf78a832d21ad5c5a84db10cdfff4834506c1c34`, artifact
12016587569 (ZIP SHA-256
`fcf4e01ae14c2fd4574022eaf879f52b2116f197a45c2c53057a1e6575209ed9`).
The checked-in policy's canonical SHA-256 is
`93322c453437b9c788f40ec28105f4cd8b0f5317e34456c8f1a58891318b032a`.
It covers all 69 detected native files in the 92,160-file, 904,208,820-byte
installation of lockfile SHA-256
`35c2d26b12dc9e3e3a6690d3f40831826080ab319ed9ab28650e0e41cc4e8fd2`.
Foreign-platform payloads stay inventoried; their presence does not establish
platform support. The metadata artifact contains no dependency payload bytes.

The subsequent native lane prepares a new complete bundle from the same exact
lock, verifies every detected native file against that policy, and exercises the
reviewed public renderer source fixture. Preparation may use the official npm
registry; workload execution has no network, installation, or image pull. The
53 exact execution-only source exceptions are test-fixture data and grant no
model-context or production secret access. Actual green native receipts are
required before claiming this bounded renderer/UI runtime works. Whole-project
Electron builds, full UI-suite capacity, durable multi-step acceptance, and
unknown-cleanup recovery remain separate acceptance gates.

Native and abuse suites use separate 45-minute jobs with matrix fail-fast off,
1800-second whole-file caps, zero file retries, and stop on the first failed test.
The first renderer fixture permits 180 seconds per step; this test policy does
not establish support for every larger production profile limit. A successful
and confirmed-clean typecheck is required before the paired UI invocation.
Any outer timeout is inconclusive failure and cannot authorize replay.

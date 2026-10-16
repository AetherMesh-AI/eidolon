# Prepared renderer validation lifecycle

This implementation adds an explicit, owner-reviewed prepared profile to a new
managed-artifact objective. It does not activate a profile merely because one
was saved. The objective retains exact registration revision/hash references for
all selected projects. Ordinary recipe bindings remain unchanged.

## Execution and acceptance

- Version 1 has two mandatory recipes: renderer TypeScript checking and selected
  Vitest UI tests. Explicit version 2 adds the authored Vite renderer build.
  Each check remains distinct. The backend reserves all two or three run units
  atomically before the first step.
  Failed, undispatched, interrupted and earlier generations keep their charges.
- The runtime uses the existing pinned Linux Docker image and a preprovisioned,
  lock/package/toolchain-bound dependency bundle. There is no runtime install,
  image pull, network access, arbitrary script entry point or credential injection.
- Each step binds its own nonce/run identity to the same immutable source,
  profile and bundle. Host events, cleanup observations and terminal receipts
  are append-only and bound to the exact private invocation. Runtime returns and
  model responses cannot replace those retained observations.
- The latest generation must contain every mandatory successful, strictly verified receipt.
  A later failed/incomplete generation cannot reuse an earlier pass.
- Independent review receives both receipts, the complete selected test/config
  contents and linked independently reviewed source work. Execution-only files
  are excluded from model context. Complete snapshot identity is verified by the
  backend; the model is not represented as reviewing every byte. Essential scope
  overflow or unavailable disclosure blocks the review rather than truncating it.
- Finish, review and acceptance recapture current source and managed lineage and
  verify current dependency bytes. Source and dependency freshness are sequential
  observed-at-check results, not a single atomic filesystem snapshot or lock.
  A file can change after its observation while another file is being checked. Runtime execution still consumes its
  separately verified private copy. Finish performs one full byte preflight
  outside the write transaction. A private, one-operation observation is checked
  against exact request, contract, run, grant, agent and source-lineage identities
  under the transaction. Strict retained pair proof and its complete typed SQL
  input inventory come from the same read snapshot before the write. Private
  one-operation reuse compares exact row values, membership, absence and schema;
  it never caches review approval. Current owner/grant/agent/request authority and
  independent review are checked inside the caller transaction. Changes, retries,
  rollback and mismatched operations invalidate reuse. The read inventory and
  final transition guards also reject late same-transaction mutations.
  Before commit, selected source bytes and the complete observed dependency
  fingerprint inventory are rechecked. The final source check retains both byte
  passes and exact equality to the fully classified preflight original; only
  repeated secret classification is skipped behind a private one-operation
  capability bound to the same limits, exceptions and live classifier policy.
  Dependency metadata uses no-follow O_PATH regular-file anchors without reopening
  file contents, retaining every path, inode, hardlink and fingerprint check.
  Exceptions, retries and rollback discard
  the observation. Original lease/deadline checks remain after these checks.
  Native lock-hold and heartbeat measurements are required before calling this
  practical on a large dependency bundle; synthetic scan counts are insufficient.

## Owner and recovery boundaries

Saving/revoking a profile uses descriptor-only owner RPCs; it does not start an
organization service. Creating a new objective requires deliberate current refs.
Stale refs require a new review; uncertain submission retains its idempotency key.
Scope changes preserve every mandatory check. Version 1 cannot claim project_build.
Version 2 records bounded build output metadata and requires its separate build
receipt. Source publication remains unsupported. A single objective cannot mix
version 1 and version 2 selections; separate objectives and historical rows can coexist.

Any reserved interrupted request currently requires inspection; automatic replay
is prohibited, including when no step started. Exact retained cleanup can settle
resources, but does not turn an unknown or failed outcome into success. Existing
legacy finish-only recovery is unchanged. A dedicated successful-pair recovery
route remains future work.

Automatic replacement/profile carry-forward and accepted-dependency reuse of a
prepared objective are explicitly unsupported. The ledger-only downstream
receipt interface cannot establish this profile's current source/bundle
freshness. A new objective requires a new explicit owner selection.

Routine status shows bounded scalar recorded claims and counts, never runtime or
acceptance authority. Actual accounting, settlement, review and acceptance use
separate strict verifiers. Missing/corrupt required storage fails closed. The
existing owner schema guard also prevents folder revocation if entire required
prepared tables are missing; the runtime is fenced by the same malformed storage,
but a corruption-tolerant deny-only administrative path is not implemented.

## Supported retained-data capacity

Prepared finish has an aggregate 128-MiB raw retained-payload limit and a
100,000-row inventory limit. SQL inventory row/byte bounds are checked before
expensive finish proof scans. The aggregate retained raw-payload guard, including
serialized proofs and source observations, runs after those scans and before
finish writes; the final guard remains authoritative. These limits do not describe total process memory: decoded objects,
transient copies and runtime buffers add memory beyond retained raw bytes.

A local reconstruction using the pinned renderer's actual source and dependency
manifest, with synthetic request/runtime scaffolding, retained about 76.6 MB
(73.1 MiB) for one full profile. One comparable profile fits this measured boundary.
Two comparable full-size prepared profiles are currently unsupported together and
fail closed before finish writes. This does not refund or prevent earlier run
charges. Smaller combinations still require exact bounds checks; this measurement
is not full multi-repository readiness or an exact native heap measurement.

Owner registration does not guarantee that a later combined objective will fit.
Earlier cross-profile admission estimates are not implemented; changed source and
retained history can affect final capacity. No memory limit is enlarged implicitly.

## Proof status

Local synthetic tests exercise the real owner authority, SQLite transitions,
complete provider context, typed checks and independent review. Synthetic runner
outputs are not native execution evidence. Native proof uses the separately
pinned reviewed renderer fixture at
b79325580a3373ad6f5fac0169e2df03eaeb97a0; it must not be represented as validation
of current-main renderer bytes. Actual hosted lifecycle proof and owner UI proof
must pass on the final integrated source before completion is claimed.

## Version 2 renderer build boundary

The Vite build consumes the same immutable selected source and pinned dependency
bundle as the compiler and selected UI test. Its source selection must include
the exact index.html and public asset closure. Runtime installation and network
access remain disabled. Authored configuration runs inside the existing sandbox;
output and cache paths are fixed scratch locations.

The complete output manifest contains sorted paths, byte counts, SHA-256 values
and asset/chunk kinds. Limits are 1,024 files, 32 MiB per file, 128 MiB total and
256 KiB canonical metadata. Symlinks, hardlinks, special files and ambiguous
paths are rejected. The manifest is a workload claim authenticated by the host
execution receipt, not independently collected deployable artifact proof. No
output bodies are retained or exported. Review receives bounded manifest summary
metadata, while strict backend verification consumes the complete retained receipt.

This validates the renderer build only. Electron packaging, release creation,
exportable artifact delivery, prepared downstream reuse and two comparable full-size
profiles in one objective remain outside this supported slice. Version 1 schema
history is migrated transactionally before new writes; existing IDs, hashes and
charges are preserved.

# Prepared renderer validation lifecycle

This implementation adds an explicit, owner-reviewed prepared profile to a new
managed-artifact objective. It does not activate a profile merely because one
was saved. The objective retains exact registration revision/hash references for
all selected projects. Ordinary recipe bindings remain unchanged.

## Execution and acceptance

- The selected profile has exactly two distinct mandatory recipes: renderer
  TypeScript checking and selected Vitest UI tests. Neither substitutes for the
  other. The backend reserves both run units atomically before the first step.
  Failed, undispatched, interrupted and earlier generations keep their charges.
- The runtime uses the existing pinned Linux Docker image and a preprovisioned,
  lock/package/toolchain-bound dependency bundle. There is no runtime install,
  image pull, network access, arbitrary script entry point or credential injection.
- Each step binds its own nonce/run identity to the same immutable source,
  profile and bundle. Host events, cleanup observations and terminal receipts
  are append-only and bound to the exact private invocation. Runtime returns and
  model responses cannot replace those retained observations.
- The latest generation must contain both successful, strictly verified receipts.
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
  under the transaction; retained receipt and review proofs still run every time.
  Before commit, selected source bytes and the complete observed dependency
  fingerprint inventory are rechecked. Exceptions, retries and rollback discard
  the observation. Original lease/deadline checks remain after these checks.
  Native lock-hold and heartbeat measurements are required before calling this
  practical on a large dependency bundle; synthetic scan counts are insufficient.

## Owner and recovery boundaries

Saving/revoking a profile uses descriptor-only owner RPCs; it does not start an
organization service. Creating a new objective requires deliberate current refs.
Stale refs require a new review; uncertain submission retains its idempotency key.
Scope changes preserve both mandatory checks. Source publication and project
build claims are unsupported for this prepared profile.

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

## Proof status

Local synthetic tests exercise the real owner authority, SQLite transitions,
complete provider context, typed checks and independent review. Synthetic runner
outputs are not native execution evidence. Native proof uses the separately
pinned reviewed renderer fixture at
b79325580a3373ad6f5fac0169e2df03eaeb97a0; it must not be represented as validation
of current-main renderer bytes. Actual hosted lifecycle proof and owner UI proof
must pass on the final integrated source before completion is claimed.

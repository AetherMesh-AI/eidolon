# Owner-prepared agent replacement

This extends the organization catalog with a narrowly authorized local value
replacement. It does not create or rotate credentials at a provider, enable
provider tools, inject environment variables, or grant access to an agent.

## Authority and data

The authenticated local owner chooses an existing entry, exact revision, and an
already-running persistent Worker with a current `replace_value` grant. The
server lists only registered live capsules; listing never starts an agent or
adopts a later request retry. Preparation binds the organization, owner, agent,
entry revision, next value version, exact grant revision, request, and capsule
generation. Naming and descriptions confer no authority.
The owner submits the exact grant revision from the listed target; a grant
change before preparation requires fresh review before any input is staged.

The dedicated owner RPC accepts the value through the same server-derived,
authenticated local transport boundary as owner value input. It captures the
exact owner connection generation. The actual single-use input handle and value
remain in bounded process memory for at most 90 seconds. The durable catalog
stores only authorization metadata, independent owner/worker operation IDs,
receipts and audit. Its internal receipt-verification hash covers a random input
handle identity and metadata, never value bytes; that hash is not exposed.

The model receives only a non-bearer `handoffId` and scoped entry/revision/expiry
metadata. It never receives the actual secure-input handle, value, value prefix,
suffix or value-derived hash. Copying the public handoff reference into a task
output does not transfer authority: use still requires the exact live capsule,
current grant revision, request lease and owner connection. A dedicated tool
accepts only that reference, with no raw-value or authority fields.

## Transactions and lifetime

The lock order is organization lease writer, catalog writer, then short memory
locks. Neither registry nor transport locks are held while acquiring a database
lock. The live lease is checked again after catalog contention and immediately
before consuming input. Grant revoke/regrant invalidates the old grant revision.
Entry changes, revoke, expiry, worker retirement, request reassignment, owner
disconnect or connection-generation change all prevent new consumption.

Service completion retires the exact capsule on success, cancellation, error,
drain and lease loss, preserving existing execution-slot cleanup. Retirement is
idempotent. A process restart has no memory handle, so persisted pending
authorization is unavailable and cannot reconstruct a value.

Native storage starts at input consumption. The catalog and lease fences remain
held through the native put and receipt transaction. Cancellation or disconnect
before that boundary prevents the write; an already-started write may commit.
The native APIs disallow interactive unlock. This boundary does not claim to
interrupt an OS call, perfectly erase unavoidable Python/JSON/native copies, or
roll back an encrypted native item if a later catalog commit fails.

## Uncertain outcomes and cancellation

Preparation has its own owner operation ID and an immutable metadata receipt.
Its server-generated worker replacement operation ID is distinct. Retrying a
matching prepare never compares a submitted value, refills the input, renews
expiry or issues another handle. A prepare receipt proves authorization was
recorded; only the replacement receipt proves the value update committed.

Status reports absent, pending, unavailable, cancelled or committed. Missing
status alone is not permission to replay. The dedicated owner cancellation
serializes with preparation and replacement in the catalog writer transaction:

- Before preparation, it permanently fences the owner operation.
- After preparation, it fences both exact owner and worker operations and
  retires the private input.
- After replacement commits, it returns that exact committed receipt instead
  of claiming cancellation.

Cancellation does not require the worker to remain live or granted. An expired,
disconnected or restarted preparation needs a fresh explicit owner value after
the old operation is safely fenced. There is no automatic raw-value replay,
provider rollback, native-item enumeration, or deletion of old stored values.

## Verification scope

Tests use temporary real SQLite ledgers, real owner RPC dispatch and live
scheduler capsules with synthetic-only storage. They cover exact scope and
generation, duplicate preparation with different bytes, cancellation races,
native-write/commit uncertainty, grant revoke/regrant, lost memory, owner
disconnect, lease expiry during catalog lock contention, service-finally
retirement and a local model round deliberately echoing the non-bearer reference.
The synthetic value and actual input handle must remain absent from model wire
traffic, durable outputs, receipts, catalog files and captured logs.

The owner panel retains only an exact metadata intent and validated preparation
receipt. A confirmed preparation displays pending authorization. Absent or
unavailable status retains recovery controls; only an exact committed receipt or
fenced cancellation clears that intent. The password field is uncontrolled and
cleared on submission, dismissal, target/entry change and connection/owner change.

A separate native UI fixture keeps one real service request alive against a
UUID-bound numeric-loopback synthetic model while the owner submits the value.
Its script attempts an unknown reference, completes the exact replacement, and
replays the same reference without another write. Completion must retire the
Worker target. Dispatch is limited to that exact synthetic request; cleanup aborts
held model responses instead of releasing new work. This fixture uses in-memory
synthetic storage and does not establish OS storage persistence by itself.

Native backend isolation and supported-platform limits remain documented in
[organization credentials](organization-credentials.md). Autonomous provider
rotation requires a separately implemented and authorized provider adapter.

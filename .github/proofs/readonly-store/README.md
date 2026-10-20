# Temporary native readonly-store proof

## Revision 3

The only adapter change from revision 2 is the Windows borrowed-handle binding:
`CDLL(name.value, handle=handle)` supplies the exact filename already returned by
`GetModuleFileNameW`. Module enumeration, required exports, per-symbol owner
verification, borrowed handle, default winmode and all later native evidence
gates stay unchanged. No alternative SQLite library is loaded or searched for.

Hosted run `39358136126` reached this adapter on Windows Python 3.11.9 and
3.12.10 but failed before native case collection. Both official ctypes
constructors inspect the name before choosing the explicit-handle branch, so
`CDLL(None, handle=handle)` raised TypeError. This is an adapter failure and
does not establish a product or native-mechanism result.

Standalone controls replay the exact, hash-pinned official constructors with
`_dlopen` forbidden, and exercise the adapter with synthetic module-enumeration
and symbol-owner results. The original None call must fail; the observed exact
absolute filename must succeed with the same handle and no loader call.
These deterministic controls are separate from the hosted native case manifest
and do not claim Windows SQLite execution. The official sources are
[CPython 3.11.9](https://github.com/python/cpython/blob/v3.11.9/Lib/ctypes/__init__.py)
and [CPython 3.12.10](https://github.com/python/cpython/blob/v3.12.10/Lib/ctypes/__init__.py).

All earlier frozen source and evidence remain unchanged. The exact hosted
case IDs, suite CLI, timeouts, admission checks and skip/deselection rules are
unchanged. Revision-3 hosted native execution remains pending.

## Revision 2

Revision 2 closed the admission-receipt verifier gap in source
manifest `ee4fea6b84db97a144a614ffe73628fefae700c0a587bf67583ec51cebd48409`.
That original source and all its outputs remain unchanged. Revision 2 changed
only this README, `verify_evidence.py`, and `test_verifier.py` from the original
source inventory. Its native adapter, production-admission test, case IDs and
deadlines were unchanged.

Every one of the seven admission receipts now independently requires its exact
native lock events, read errors, acquired-lock count, readonly evidence,
refusal-pause count, coordinator waits and connection count. The uncoordinated
positive must show native EXCLUSIVE success and line-290 SQLITE_BUSY. Both
readonly receipts must show readonly MAIN_DB flags, query_only=0 DML refusal,
native SQLITE_IOERR_LOCK, one refusal pause and zero successful acquisitions.
The `closeLocksAtGuard` field counts successful close-lock acquisitions observed
by the time the guard returns; it does not measure locks still held then.
Coordinated and released closes legitimately report one after release.

Independent verifier controls remove and corrupt each mechanism field in every
mode while retaining green JUnit and store-comparison receipts. They also cover
each readonly evidence field and boolean/integer/float type confusion.

This harness is diagnostic-only. It changes no production module, native-host
eligibility, policy, debt, admission deadline, SQL retry, or connection lifetime.
It must run against an explicit immutable candidate checkout. Do not copy its
ctypes adapter into the product.

## Hosted commands

Use the candidate's actual `.venv` Python, including on Windows. Install the
candidate's locked dependencies by the canonical workflow first. The harness
must be a separate, hash-verified checkout.

```
<PYTHON> -B <HARNESS>/run.py \
  --source-root <SOURCE> --store-sha256 <STORE_SHA256> \
  --expected-head <SOURCE_COMMIT> --expected-tree <SOURCE_TREE> \
  --output <NEW_EMPTY_EVIDENCE_DIRECTORY> --suite all

<PYTHON> -B <HARNESS>/verify_evidence.py \
  --evidence <EVIDENCE_DIRECTORY> --source-root <SOURCE> \
  --store-sha256 <STORE_SHA256> --expected-head <SOURCE_COMMIT> \
  --expected-tree <SOURCE_TREE> --suite all --platform <darwin|win32>
```

Run macOS and Windows with Python 3.11 and 3.12. In the macOS 3.12 native journey
job, use the same installed interpreter for this proof and the unchanged native
journeys. Use `--suite identity` with fresh output directories before and after
the journeys, and compare runtime/library identity with this proof's receipt.

The expected IDs are frozen in `required-cases.json`. POSIX `all` requires 34
passed cases; Windows `all` requires 27 passed and exactly seven explicitly
deselected POSIX subscription-admission cases. No selected case may skip.
Windows is only a store-level native SQLite proof, not subscription eligibility.
The optional `portable` lane requires 25 passed/seven deselected; `admission`
requires 16 passed on POSIX; `permissions` requires two passed; `identity` has
no test cases. `portable` deliberately excludes permission tests, unlike `all`.

The wrapper bounds its child process to 60 seconds. Native pauses remain five
seconds. Exit 0 means the strict evidence gate passed; 124 means timeout; any
other nonzero result fails the proof. Output directories must be empty. Neither
test retries nor a replacement SQLite library are allowed.

## Required receipts

- `runtime.json`: actual Python, SQLite version/source ID/compile options,
  extension/library image hashes, original VFS, runner identity, candidate
  module hash, and a registered-VFS Python-open same-instance witness.
- `source-before.json` and `source-after.json`: unchanged candidate identity.
- `collection.json`, `reports.json`, `junit.xml`, `case-gate.json`: exact frozen
  IDs; exact selected/deselected sets; one successful setup, call and teardown
  per selected case; zero failures, skips, errors or duplicate executions.
- `vfs-cleanup.json`: original default restored, every diagnostic VFS restored,
  no live tracked handles, no pause failures, bounded callback retention.
- `store-close-comparison.json`: a genuine writable EXCLUSIVE-lock/BUSY positive
  must pass in the same test before the readonly negative can pass. The negative
  requires returned readonly MAIN_DB flags, query_only=0 mutation refusal,
  native EXCLUSIVE refusal and a successful timeout-zero observation. Filesystem
  identity selects the target even when native and caller path strings differ.
- Native permission JUnit properties: actual same-process create/write denial,
  closed-file hashes, and exact restored POSIX modes or Windows owner/group/DACL
  descriptors. Windows ACL changes apply only to the owned temporary directory
  and its direct single-link files. Restoration loss fails and preserves the
  fixture rather than concealing the failure through forced cleanup.
- Seven admission outcome receipts on POSIX retain stale-owner, debt, policy,
  coordinated-writer and replay assertions. No Windows platform bypass is used.
- `network.json`, `process.json`, `verified.json`, `test.log`: zero network
  attempts, successful bounded process exit, verified gate and full test output.

The ctypes adapter binds only to already-loaded SQLite. If exports, image bytes,
default VFS, ABI, same-instance witness or required native behavior cannot be
observed safely, the proof fails. It never loads another SQLite, adds immutable
mode, accepts a missing positive control, or silently skips unsupported evidence.
Method versions are checked before copying full tables. Callback error controls
require exactly one delegate call and preserve its exact return code. All
callbacks remain strongly held through the bounded process lifetime.

## Local validation and limits

Local Linux Python 3.13.5 / SQLite 3.46.1 passed 34/34 cases with zero skips and
network attempts. Strict collection/execution/cleanup verification passed.
The optional portable lane passed 25 cases with its exact seven deselections.
The 198 standalone verifier controls pass, rejecting missing receipts, duplicate
JSON keys, changed IDs, extra execution, skips, missing positive locks, lost
readonly flags, permission-restoration loss, source changes, timeout and network
activity, plus admission-specific mechanism loss/corruption and type confusion.

Linux Python 3.12.14 / SQLite 3.53.1 passed both native permission controls. Its
embedded SQLite exports are hidden, so instrumentation correctly refuses with
`Loaded extension does not expose sqlite3_libversion`; it is not a mechanism
pass. Python 3.11 syntax compatibility was checked, not runtime execution.
These local results do not establish macOS, Windows, or hosted Python 3.11/3.12
mechanism success for this revision.

The local candidate store has Git blob
`2bae74bfe45c476d46cc2cae5a1d9ca937a8eaa9` and SHA-256
`9c69661ccade2af564a0c84ad50f791b2384d4f90f0bc6b296b3c0a88f8efd2a`.
Local source receipts deliberately have null Git head/tree because validation
used a materialized candidate source tree. Hosted execution requires the full
immutable commit/tree arguments and a pristine checkout.

The immutable candidate for hosted execution is commit
`8bb3444382fe658e937830441b5e6195907c731b`, tree
`01aec73a193becd7c74f242cf97a3a816e190ba6`.
The Python 3.12 permission/preflight and earlier portable-lane results above
belong to the unchanged original adapter; their original evidence remains at
the revision-1 freeze. Revisions 2 and 3 each reran the full Linux Python 3.13
suite and verifier controls with new output directories.

`manifest.json` pins the source inventory. `local-evidence-manifest.json` pins
local validation receipts and their stated limitations. Neither proves the
historical macOS SQLITE_BUSY holder, and neither replaces the exact final nine
native journeys, eight teardown receipts, or required canonical checks.

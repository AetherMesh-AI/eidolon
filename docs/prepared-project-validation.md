# Prepared renderer validation (experimental)

This additive implementation is not registered as a production execution grant.
It does not activate an organization or provision a user's runtime. Existing
Python, dependency-free Node and fixed TypeScript recipes are unchanged.

## Intended useful scope

One versioned profile binds an actual renderer TypeScript project and explicit
Vitest UI files to the same immutable source and preprovisioned dependency tree.
Both mandatory steps must eventually have independently reviewed, current durable
receipts before an aggregate validation set can authorize delivery. The current
profile identity is a proposed scope identity, not a successful validation receipt.
Full UI-suite capacity, Electron builds, native desktop scenarios and GitHub
publication by Eidolon require their own implementation and acceptance evidence.

## Source and model context

Source selection is explicit and bounded, with descriptor-anchored root identity,
regular-file checks and final byte verification. It retains binary assets and
cross-workspace imports. Runtime paths are limited to 512 UTF-8 bytes even when
the generic snapshot policy permits longer paths. Protected paths, credentials,
links and special files fail closed. No omitted import is treated as a successful
source closure.

An optional owner-reviewed execution-only policy identifies ambiguous public
fixture bytes by exact path and full SHA-256. It cannot bypass protected paths or
approve changed bytes. Policy entries and execution-only flags contribute to
source identity. It is not a filename exception and does not change agent prompt
file-read redaction. Runtime diagnostics are redacted before being returned.
The tests-only Eidolon fixture has a fixed source baseline and reviewed public
examples; it is not a product default or authority for other repositories.

## Dependencies and authority

Preparation is separate from execution. The manifest binds exact lockfile and
package-manifest bytes, selected workspace links, each dependency file, explicit
native files, tool versions and platform/Node ABI metadata. Inventory copying
checks every file and rejects extra, missing or changed bytes. Registry metadata
and preparation records alone are not authenticated provenance proofs.

Runtime makes no install, image pull, shell/package-script dispatch, credential
lookup or network grant. The owner must explicitly authorize the exact pinned
local Docker runtime and dependency identity. Current authority is polled during
copying and attached execution; revocation is latched as cancellation. Cleanup
uses the exact verified container identity. Unknown cleanup retains staging and
must block replay.

The Linux x86-64 container runs a trusted UID0 supervisor with only SETUID/SETGID
capabilities and an unprivileged workload. Source/runtime are read-only; scratch,
CPU, memory, process count, logs and elapsed time are bounded. The first profile
supports exactly one CPU and 64 PIDs. Unsupported platforms fail closed.
Compiler diagnostics and test reports remain workload-origin claims, not proof
that assertions are meaningful. Independent review remains required.

## Evidence gates

Local normalization, malformed-input, descriptor, scope and protocol tests do not
establish native execution support. The hosted opt-in lane must prove actual
renderer typechecking and a real UI test, demonstrate that type and assertion
failures block success, and establish isolation/cancellation/cleanup behavior.
Missing native prerequisites are failures in that lane, never silent skips.
Only after this boundary is reviewed should the scheduler, durable validation-set
schema, acceptance and source-delivery hooks be enabled.

### Initial native evidence and retained failures

The immutable dependency preparation succeeded in run `38829761731`. The first
workload lanes (`38831264226`, diagnostic follow-up `38831801781`) remain failed:
renderer receipt framing was incomplete near 64 KiB, with cleanup confirmed.
A local pipe reproduction establishes that an initialized Node stdout can return
a partial count from a single `writeSync`; complete frames must be written within
bounded backpressure/deadline handling, never accepted after truncation.

The abuse lane passed 15 cases before the positive scratch-execution probe failed.
Its observed startup/isolation/cleanup checks passed, but this does not establish
successful Vitest execution. Retained failures must be replaced by actual passing
native evidence before support is claimed. Added capacity and uncertain-cleanup
cases are separate proof gates; local skips are not native evidence. A test-owned
identity-checked cleanup repair does not promote an uncertain execution receipt
or establish durable scheduler replay protection.

The native workflow checks out the exact `b79325580a3373ad6f5fac0169e2df03eaeb97a0`
renderer fixture separately from the runtime under test. This checkpoint includes
main `560e77317c86234bcfc52d570dce40d1a37e4246` and the separately reviewed ESM-safe
Vite configuration correction, plus the generic RPC mock and required typed
callback correction in the previously shadowed background-sync test. Its two
profile/backend refresh assertions and response behavior remain unchanged; a
dedicated native case requires both tests to pass. Source and package manifest
bytes must still match the complete reviewed inventory. The historical `ac637822`
and `47ea5eae` fixtures remain unchanged. The 53 reviewed execution-only hashes and
reasons are unchanged from `47ea5eae`; the corrected test needs no exception.
This does not claim validation of later main-branch changes. Updating the fixture
or its execution-only exception policy requires separate review. General authorized
source edits still need a practical owner-reviewed policy; this fixture's exact
public-byte exceptions are not a substitute for real-secret protections.

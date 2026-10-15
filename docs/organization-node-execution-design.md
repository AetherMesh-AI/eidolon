# Explicitly authorized Node test execution

The fixed Node test adapter requires an explicit owner-configured grant; capability
reporting alone is not a promise of host support. The existing Python isolation recipes and grant fingerprints stay
unchanged. Node package inspection remains inert.

## First useful recipe

`node_test` runs only explicitly selected `.test.js`, `.test.cjs` or `.test.mjs`
files through Node's built-in test runner over the exact managed snapshot. It
never runs package scripts or installs packages. Selected `package.json` files
may affect Node module interpretation and therefore remain part of the snapshot
identity. Missing imports fail; omitted dependencies are never inferred.

The owner must explicitly enable local Docker use in this recipe. There is no
default daemon probe or automatic permission change. Docker access is powerful:
the daemon is part of the trusted computing base, outside the project sandbox.
Only a preinstalled Linux x86-64 Docker client, local Unix socket, `runc`, cgroup v2 and
an already-present, reviewed official Node image pinned by digest are supported.
The runner never pulls images, installs software, accepts a remote endpoint, or
uses host Node as a fallback. ARM64/macOS/Windows remain unsupported for this backend.

## Isolation and bounds

- Copied selected source and trusted bootstrap are read-only bind mounts.
- Container root filesystem is read-only; no image-declared volumes are allowed.
- No host home, credentials, devices or daemon socket enter the container.
- Network and IPC are isolated; no network interface reaches another host.
- Unprivileged test UID with zero capabilities, no-new-privileges, an explicit
  reviewed digest-pinned seccomp profile and private PID/mount/cgroup namespaces.
  Supervisor/test UID separation is covered by the mandatory native CI lane.
- Fixed one-CPU quota plus an overall wall deadline, bounded memory/swap/PIDs,
  bounded writable tmpfs, and bounded captured output. CPU rate is not described
  as an aggregate CPU-seconds guarantee.
- A trusted PID 1 watchdog must bound running-container lifespan even if the
  client dies. Startup verifies actual kernel state before importing project code.
  Disable SIGUSR1 inspector activation on every trusted Node process and exclude
  all inspector options/environment. Test children must not signal, debug or write
  the supervisor through /proc descriptors.
- Disable daemon-side container logging; host capture limits alone do not bound
  daemon disk usage. Use bounded attached streaming with log-driver=none.
- Daemon inspection independently verifies the requested container configuration.
  Kernel evidence is consumed before untrusted tests start, not inferred from a
  project's stdout or its claimed test result.
- Cleanup targets only this run's recorded container identity and ownership
  label. Unknown daemon outcomes retain an intervention state rather than replay.

## Receipts and test evidence

Receipts bind the exact grant, snapshot, selected test files, runtime image,
bootstrap hash, container configuration, limits and terminal status. Only an
actual supported sandbox run may establish isolation. Missing runtime, failed
setup, cancellation, timeout and empty/fully skipped tests never become passes.

Node can report a successful wrapper file even when it contains no registered
tests. Require actual file-scoped test summaries and a positive non-skipped test
count as well as successful final summary/exit. Run tests in separate processes;
`isolation: none` is not an accepted shortcut. As with Python, project tests can
falsify assertions or reporting, so a pass still requires independent review and
is not cryptographic proof of correctness.

## Required native proof before enabling

CI must use the same production adapter with the pinned image, require sandbox
availability, and execute real passing/failing/empty/skipped/syntax-error tests.
Adversarial fixtures must cover read-only source and runtime, inaccessible host
sentinels/socket, denied external networking, privilege/namespace attempts,
resource exhaustion, timeout, cancellation, output flooding, child-process
cleanup, and changed image/configuration rejection. Unit doubles may exercise
parsers and failure handling, never substitute for successful isolation proof.

## Dependencies and TypeScript follow-on

A separately reviewed preparation path may create a bounded, content-addressed
dependency bundle from the exact lockfile and trusted registry artifacts. It must
record provenance, package integrity, platform/runtime identity and any approved
lifecycle scripts. Preparation cannot silently gain network or credentials from
the execution recipe. Runtime mounts a verified bundle read-only and remains
offline. Type checking and builds then use fixed tool entry points and bounded
output directories; generated artifacts need their own manifest and review.
General `npm test`/`npm run build` shell execution is not implied by this design.

## Supervisor trust boundary

The initial same-UID supervisor design is rejected. Node children can potentially
activate the supervisor inspector via SIGUSR1 or access its descriptors. The
implemented boundary is a minimal trusted root-owned PID 1 retaining
only SETUID/SETGID to launch tests as UID/GID 65534, with test capabilities zero.
No project code is imported into that supervisor. Its result pipe and watchdog
must remain inaccessible to the test UID. The native adversarial lane verifies this boundary. Runtime access still requires
the owner opt-in; installing the application does not enable Docker execution.

Node test events can themselves be forged by hostile tests. File-scoped counts
prevent accidental empty-file passes, not malicious falsification. Supervisor
evidence must distinguish kernel isolation/lifecycle observations from those
untrusted test claims, reject malformed/duplicate framing and independently
verify final container state. A count is never proof an assertion actually ran.

## Owner configuration and retained evidence

An execution grant selects recipe `node_test`, `allow_local_docker: true`, the
exact `runtime_image` value
`node@sha256:a5f4366e834695d5f363b920c104953a535d3a9864d1bdf74566a0ebe01daee5`, a read-root alias, test directory and bounded
filename pattern. The enclosing project grant lists every source/test file.
There is no implicit use of an installed daemon. The normal organization backend
records the immutable start before dispatch and an immutable outcome afterward.
Success additionally requires confirmed cleanup, matching source/grant/runtime
identities and the existing independent review. Changed grants, revoked authority,
source drift and unknown outcomes preserve the existing fail-closed gates.

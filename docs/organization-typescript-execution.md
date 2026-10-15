# Fixed TypeScript check boundary

The `typescript_check` recipe requires explicit owner opt-in and the trusted
prerequisites below. It supplies independently reviewed `project_typecheck`
evidence. Compiler success cannot satisfy `project_tests` or `project_build`.
Automatic source integration continues to require genuine test evidence.

The current scheduler retains one recipe/latest run per project. Requiring
both tests and type checking for the same project is unsupported and fails
closed; changing its grant invalidates earlier evidence rather than combining
incompatible receipts. Multi-check-per-project scheduling is future work.

## Trusted prerequisites

The owner must explicitly authorize the existing local Linux x86-64 Docker
runtime and the same digest-pinned Node image used by the Node test backend.
No runtime image pull, package install, daemon configuration, permission change,
network fallback, package script, or compiler plugin runs during execution.

The compiler is TypeScript 6.0.3, already selected by the desktop lockfile. Its
repository-pinned inventory binds the official npm archive SHA-512 and every
one of its 140 files (24,346,827 bytes). An owner-specified absolute compiler
folder is only a source of candidate bytes: its own manifest cannot authorize
those bytes. Descriptor-relative, no-follow reads verify and copy the entire
exact inventory into private staging. Links, extra files, non-regular entries,
changed streams and incomplete copies fail closed. Cancellation removes private
partial staging; a cleanup error remains explicit. The compiler is never
imported while verifying its package.

## Data-only compilation

The root-owned, fixed supervisor retains the reviewed Node isolation boundary.
The pinned compiler runs with UID/GID 65534, no capabilities, no network and
read-only mounts. The compiler host exposes only selected source paths and the
pinned compiler's standard library declarations. Project TypeScript is parsed
as data; project code, package scripts and dependencies are never executed.

The initial source roots are selected .ts files. The selected configuration must be strict JSON. Only boolean strict,
noUnusedLocals, noUnusedParameters, exactOptionalPropertyTypes and
noUncheckedIndexedAccess options are supported. The fixed target is ES2022,
ESNext modules and Bundler resolution. Emission, incremental state, composite
projects, noCheck, library replacement, plugins, extends and project references
are unavailable. Source-level @ts-nocheck is rejected. Standard libraries are
checked. Missing imports fail rather than reading from the host or installing
packages.

The maximum possible input descriptor frame is checked before runtime access and must fit within 49,152 UTF-8 bytes, below the separate 65,536-byte control limit. Source revisions cross the JavaScript boundary as exact decimal strings.

Compiler results use project_typecheck, inputCount and diagnosticCount. They
also bind the actual source/library reads, effective configuration, compiler
inventory, image, bootstrap, seccomp, container configuration, exact selected
snapshot and confirmed container cleanup. Compiler success is never counted as
a passed test. It does not prove the application's intended behavior.

## Remaining work

Native proof and independent review are required before this integration is
merged. The trusted verifier binds compiler evidence to its current project
grant/source; the typed completion-contract layer must explicitly use that
verifier before enabling compiler acceptance criteria. Real project dependency
support needs separately pinned, verified dependency closures. Build output
needs bounded supervisor-mediated artifact transfer and byte hashing through
cleanup; a writable host mount is not an acceptable substitute. Those features
remain unsupported in this slice.

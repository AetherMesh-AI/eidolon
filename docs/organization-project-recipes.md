# Project execution recipes

An organization works in explicitly selected folders. A Git repository is not
required for inspection or planning. Each independently selected project may
have an owner-selected execution recipe; selecting a folder or discovering a
manifest never creates that permission.

## Current execution and diagnostic boundary

The current runner accepts only `python_unittest` and `python_pytest`, with exact
selected UTF-8 files and revisions. Both require the existing Linux bubblewrap
and seccomp backend. The pytest recipe uses a fixed installed framework bundle;
neither recipe installs project dependencies. Unsupported hosts fail closed.

`describe_execution_recipe` is configuration-only metadata. A supported recipe
has state `runtime_unverified` until the actual isolated execution succeeds.
`inspect_project_recipes` examines only an already authorized selected snapshot.
It does not recursively scan a folder or open additional files, including files
referenced by a manifest. Its results are retained in execution receipts as
`recipeDiagnostics`, alongside the exact snapshot digest.

Node package `test` and `build` scripts and TypeScript configuration produce
explicitly **unsupported** candidates. Script text is not executed or included
in the diagnostic output. Manifest, selected lockfile and configuration hashes
bind the diagnostic to its inputs. A lockfile's presence does not prove installed
dependency integrity or completeness. A package with no declared dependencies
may still import missing or external modules. Missing selected manifests remain
unknown. Nested package directories are separate observations; package workspace
relationships are not inferred as authorized bindings.

Diagnostics are not test evidence, a capability grant, a successful build, or a
reason to skip independent review. Existing Python execution authority and its
source/test selection remain unchanged. No npm lifecycle hook, package manager,
shell command, downloaded compiler, or host fallback is invoked by discovery.

## Next execution adapter boundary

A future Node/TypeScript backend must remain separate from the Python runner's
single-process/no-thread sandbox. Before it can be declared supported it needs:

- An explicitly owner-configured local runtime and trusted image pinned by
  immutable digest, with verified runtime and dependency identities.
- No automatic daemon permission changes, image pulls or installation. Missing
  runtime/image/dependencies are actionable unsupported states.
- A read-only exact source snapshot, no network, no host home or credential
  mounts, no capabilities, no new privileges, bounded output and scratch space,
  and enforced memory, CPU, process and wall-clock limits.
- Locked, verified dependency packaging with no installation or lifecycle hook
  during inspection. Build artifacts need exact content identities and separate
  review/admission; an exit code alone does not establish the requested outcome.
- Native execution tests on the supported platform, including hostile projects,
  cancellation, timeouts, output flooding, source writes, network/process escape
  attempts, missing dependencies and unknown/interrupted effects.

This document describes prerequisites, not an implemented Node backend. No
container access or authority is granted by these diagnostic changes.

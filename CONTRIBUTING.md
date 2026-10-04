# Contributing to Eidolon

Eidolon is an independent project building a desktop home for persistent AI agents. Read the [project overview](README.md) for its direction and the distinction between existing code, prototypes, and verified behavior. Consult [Eidolon releases](https://github.com/AetherMesh-AI/Eidolon/releases) for build-specific verification scope and limitations; publication does not establish acceptance of every feature or platform.

## Before you start

- Search the [project issues](https://github.com/AetherMesh-AI/Eidolon/issues) and existing work before opening a duplicate. Discuss substantial changes before building them.
- Read [AGENTS.md](AGENTS.md) and the area-specific instruction file before changing code. Those files contain technical constraints; older branding or release claims elsewhere are not an Eidolon support commitment.
- Inspect the worktree before editing. Preserve other contributors' uncommitted changes, keep scope explicit, and retain authorship and applicable license notices.
- Prefer reproducible reliability fixes, clear agent interaction, and tested implementations of project goals. Do not present a simulated workflow as a live capability.

## Repository orientation

| Area | Starting point |
| --- | --- |
| Desktop application | [apps/desktop](apps/desktop) |
| Organization and memory prototypes | [apps/desktop/src/app/eidolon](apps/desktop/src/app/eidolon) |
| Agent runtime | [agent](agent) |
| CLI and configuration | [eidolon_cli](eidolon_cli) |
| Messaging and sessions | [gateway](gateway) |
| Tools and extensions | [tools](tools), [plugins](plugins), [skills](skills) |
| Tests | [tests](tests), [apps/desktop/package.json](apps/desktop/package.json) |
| Dependency requirements | [pyproject.toml](pyproject.toml), [package.json](package.json) |

Internal module names and compatibility identifiers are not product names. Do not rename them mechanically in a documentation or branding change.

## Development and verification

Use a disposable checkout or worktree and an isolated application home. Read the manifests and area instructions for prerequisites; this guide does not provide a tested installer recipe. Install JavaScript workspace dependencies from the repository root, not a nested desktop directory. Do not pipe a remote installer into a shell as a substitute for reviewing the source you intend to test.

The runtime retains the `HERMES_HOME` compatibility override. Point it to a temporary directory for tests and development; do not use a real profile or copy credentials into a fixture. A temporary data home alone does not isolate network access, subprocesses, or the rest of the host. Use a sanitized environment and an appropriate sandbox for the path being exercised.

Repository-defined verification entry points include:

- Python: `scripts/run_tests.sh`. Use this wrapper, not bare `pytest`; it isolates the application home and test processes and sanitizes the environment. See [AGENTS.md](AGENTS.md) for targeted selection and platform markers.
- Desktop, from the repository root: `npm run --workspace apps/desktop typecheck`, `npm run --workspace apps/desktop lint`, and `npm run --workspace apps/desktop test:ui` are defined in the desktop manifest.
- OS-sensitive changes: review [scripts/check-windows-footguns.py](scripts/check-windows-footguns.py), then exercise the relevant behavior on its actual target OS.

These are source-defined commands, not results from this documentation update. Review test setup and side effects before running them. Native, installer, updater, and end-to-end tests need an explicit isolation and cleanup plan; do not launch them against a live installation or assume a unit test proves native behavior.

For a fix, demonstrate the failure and the corrected behavior with focused invariant tests. Exercise real configuration and I/O paths in isolation when they matter. Record exact commands, exit status, platform, source revision and dirty state, fixture scope, and any retry or skipped coverage. Separate synthetic conversation persistence from actual AI responses, and a packaged build from installed-update acceptance. A hold remains a hold until the blocked scenario is verified.

## Engineering conventions

- Extend existing infrastructure before adding another manager or core tool. Prefer skills or plugins for capabilities that do not belong in the core; third-party product and memory-provider integrations should use plugin boundaries.
- Preserve per-conversation prompt caching, role alternation, and stable session context. Follow the cache-aware mutation rules in [AGENTS.md](AGENTS.md).
- Keep modules focused. Follow local Python and TypeScript style; comments should explain intent, not repeat code. Catch specific exceptions and retain useful diagnostics without secrets.
- Resolve application paths through the existing helpers, not hard-coded home directories. Resolve symlinks before path-based access checks. Prefer argument arrays; when shell syntax is necessary, use quoting appropriate to that shell rather than assuming POSIX quoting works everywhere.
- Verify process ownership before cleanup. Do not use name substrings or broad process-kill commands. Account for Windows process, path, quoting, encoding, and permission differences.
- Test OS-dependent behavior on that OS using the repository's platform markers. Do not use a patched platform string as proof of native support.
- Keep dependency upper bounds and lockfiles consistent. Pin Git dependencies and GitHub Actions to full commit SHAs; use exact versions for CI-only pip installs. Follow the detailed dependency policy in [AGENTS.md](AGENTS.md).
- Do not add outbound telemetry without explicit opt-in. Treat external content as untrusted and review changes to permissions, secret handling, IPC, file access, installers, and updates carefully.

## Pull requests and issue reports

Keep one logical change per PR. Include the problem, the intended behavior, related issues, reproduction steps, implementation scope, and verification evidence. State what was not tested; screenshots and green mocks are not substitutes for the relevant runtime path. Use descriptive commits, for example `fix(desktop): preserve selected agent` or `docs: clarify prototype scope`.

For non-sensitive bug reports, include OS, build/source revision, whether the source was dirty, expected versus observed behavior, and a minimal sanitized reproduction. Do not upload application homes, private conversations, raw environment dumps, access tokens, or unredacted logs. For possible vulnerabilities, follow [SECURITY.md](SECURITY.md) instead of posting exploit details publicly.

Review approval is separate from permission to publish. Release artifacts, tags, signing, installation instructions, and update claims require their own review and evidence.

## License

Contributions are made under the repository's [MIT license](LICENSE). Preserve the copyright and permission notice in copies or substantial portions of the software, along with applicable source and third-party notices.

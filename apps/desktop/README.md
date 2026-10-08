# Eidolon Desktop

The native desktop application for [Eidolon](../../README.md), an independent project building a home for persistent AI agents. The intended experience centers on ongoing agent conversations, understandable activity, and user control.

> **Early Alpha / Proof of Concept.** Existing routes and integrations are not evidence of complete AI conversation flows, reliable agent switching, or supported installers on every platform. See the [project overview](../../README.md#the-foundation-today) for verified boundaries and known holds.

## What is here

| Surface | Purpose and current boundary |
| --- | --- |
| Agent conversations | Canonical per-agent chats, with bounded synthetic persistence evidence. Actual AI conversation flows and reliable first-attempt switching still need acceptance. |
| Chat and previews | Streaming transcript, tool activity, file browser, and side-by-side previews are existing integration surfaces, not an end-to-end acceptance claim. |
| Voice and settings | Existing voice controls and provider, model, tool, and credential settings depend on runtime configuration and external providers. |
| Home, Objectives, Organization, Activity, Knowledge | Backend-owned objectives, typed requests, scoped worker claims, independent review and retained deliverables. Supports writing/analysis of submitted context and opt-in bounded local inspection/reviewed managed-workspace edits with configured staff and durable evidence; original-project merging remains intervention; unsupported capabilities need intervention. See [organization execution](../../docs/organization-flow.md). |
| Updates | Existing source-based update machinery. A successful local fixture does not establish a supported installed-update path for every platform. |

## Trying the desktop

Use disposable data for experimental builds. Consult [Eidolon releases](https://github.com/AetherMesh-AI/Eidolon/releases) for build-specific instructions, artifacts, verification scope, and limitations. Source availability and packaging targets are not a promise of tested or signed installers for macOS, Windows, or Linux.

The compatibility CLI command remains:

```bash
hermes desktop
```

It operates on the runtime selected by your shell. An upstream installation is not converted into Eidolon by this command. Verify the checkout and application home first; do not run it against an important existing profile as an installation experiment. First-launch connection and local-install paths remain in the code, but are not a guarantee of a ready-to-use Eidolon deployment.

## Updating

The built-in updater follows the official Eidolon `main` branch and rebuilds the
desktop locally. It does not download or install the latest release asset.
Archive installations first ask permission to prepare an updatable source runtime.
The CLI command for an already-selected Eidolon runtime is:

```bash
eidolon update
```

This updates the selected runtime, not an arbitrary Eidolon checkout. Review the repository, installed build, application home, and release instructions before using either update path. Do not treat upstream releases as Eidolon updates.

On macOS, Update checks for known replacement restrictions before closing the
app. A detached helper then updates the source, verifies the desktop build,
replaces the running `.app`, and verifies that a fresh native app process remains
running. The optional progress window appears only for a supported Chrome/Chromium
default browser; its absence does not establish a failure.

Replacement uses the current user's existing access only. If macOS denies replacing
an installed app, the helper preserves the previous app and new build and reports
that installation is incomplete. Use Finder to replace the app and approve any
macOS prompt, or ask the administrator responsible for the installation. The updater
does not change permissions, clear quarantine, or install a privileged helper.

Each swap preserves its previous app and transaction journal in a uniquely named
`.eidolon-update-*` sibling directory. These backups consume disk space and can be
removed by the user after validating the updated app. An unfinished
`.eidolon-update` directory (or legacy `.old`/`.new` bundle) blocks another swap;
inspect the retained journal and app copies before attempting recovery. The helper
never discards an interrupted transaction to make a retry proceed.

The release version can remain unchanged across source updates. Compare the
embedded commit shown in About, rather than the version number alone. A current
source checkout with a provably older desktop build keeps the update action
available so installation can be retried. Failures are recorded under the selected
`HERMES_HOME` in `logs/desktop-update-handoff.log` and `.eidolon-update-result.json`;
the next launch consumes the result and shows failures. These source-level checks
do not replace installed-app acceptance on the target OS.

## Requirements

Read the root and [desktop package manifests](package.json) for current dependencies and scripts. Installer/bootstrap code exists, but it is not evidence that every dependency is installed correctly on your platform. See [CONTRIBUTING.md](../../CONTRIBUTING.md#development-and-verification) for isolation and verification guidance.

---

## Development

Want to hack on the app itself? Install workspace deps from the repo root once, then run the dev server from this directory:

```bash
npm install          # from repo root — links apps/desktop, web, apps/shared
cd apps/desktop
npm run dev          # Vite renderer + Electron, which boots the Python backend
```

Point the app at a specific source checkout, or sandbox it away from your real config:

```bash
HERMES_DESKTOP_HERMES_ROOT=/path/to/clone npm run dev
HERMES_HOME=/tmp/throwaway npm run dev
npm run dev:fake-boot   # exercise the startup overlay with deterministic delays
```

### Building installers

```bash
npm run dist:mac     # DMG + zip
npm run dist:win     # NSIS + MSI
npm run dist:linux   # AppImage + deb + rpm
npm run pack         # unpacked app under release/ (no installer)
```

These are existing packaging commands, not verified release guarantees. Signing hooks use `CSC_LINK` / `CSC_KEY_PASSWORD` / `APPLE_*` for macOS and `WIN_CSC_*` for Windows. Building is separate from publishing; do not upload artifacts or assume signing/notarization succeeded without release-specific authorization and evidence.

### How it works

The packaged app ships the Electron shell and a native React chat surface.
The backend derives from Hermes Agent and retains its compatibility commands,
protocol, and environment variable names. Desktop home selection honors
`HERMES_HOME`; the desktop default in `electron/main.ts` is `~/.eidolon`.
Do not assume a separate CLI installation selects the same home. First-launch
bootstrap remains a distinct install path requiring its own verification.

The app has three boundaries:

- **Electron** resolves and validates a runnable backend, owns native
  filesystem/git/window capabilities, and exposes a narrow preload bridge.
- **React** owns the Desktop routes, panes, interaction state, and
  `@assistant-ui/react` transcript.
- **Hermes Agent** runs as a headless `hermes serve` process and exposes the
  `tui_gateway` JSON-RPC/WebSocket API. The renderer connects through
  [`apps/shared`](../shared/), which is also used by the browser dashboard.

Backend resolution is an ordered ladder:

1. `HERMES_DESKTOP_HERMES_ROOT`
2. the current source checkout during development
3. a completed managed install
4. `HERMES_DESKTOP_HERMES`, or `hermes` on `PATH`
5. a system Python that can import the Hermes runtime
6. the first-launch bootstrap installer

Candidates are probed before use; an existing shim or interpreter is not enough.
A runtime that predates `serve` falls back to headless
`dashboard --no-open`. This is compatibility for the backend command only and
does not launch or embed the dashboard UI.

The Electron orchestration entry point is `electron/main.ts`; pure resolution,
probe, hardening, and platform policies live in focused modules beside it. The
renderer is under `src/`, with shared atoms in `src/store` and transport/native
adapters in `src/lib`.

Before changing the app, read:

- [`AGENTS.md`](./AGENTS.md): architecture, state ownership, resolver/fallback,
  transport, performance, and testing rules.
- [`DESIGN.md`](./DESIGN.md): visual system, information architecture, motion,
  direct manipulation, and keyboard behavior.

### Connections, projects, and switching

Desktop supports a managed local backend, explicit remote gateways, and Hermes
Cloud connections. Remote and cloud modes use the same remote-capability path;
authentication and discovery differ, not the renderer feature model.

When no usable local runtime or saved remote connection exists, the first-run
screen offers **Connect to existing Hermes** before starting the local installer.
Desktop probes the gateway to discover token or OAuth authentication, requires a
successful HTTP and WebSocket connection test, and saves the connection using
the same encrypted Desktop configuration used by Settings. A saved remote
connection bypasses this choice on later launches. The regular Desktop build
still includes the local-install option; this is a remote operating mode, not a
separate client-only application.

In remote mode the gateway host is the execution boundary: agent tools,
terminal commands, and file operations run against the remote Hermes host, not
the computer displaying the Desktop UI.

Remote gateways that sit behind an access proxy may require extra headers on
every HTTP and WebSocket request. Configure them per connection in Settings →
Connections (Extra gateway headers), or add a `headers` object to Desktop's
Electron `userData/connection.json` remote block:

```json
{
  "mode": "remote",
  "remote": {
    "url": "https://hermes.example.com",
    "authMode": "token",
    "token": { "encoding": "safeStorage", "value": "..." },
    "headers": {
      "CF-Access-Client-Id": { "encoding": "safeStorage", "value": "..." },
      "CF-Access-Client-Secret": { "encoding": "safeStorage", "value": "..." }
    }
  }
}
```

Per-profile remote entries under `profiles[name].headers` use the same shape.
Desktop applies these headers only to matching remote gateway requests, treats
`https` and `wss` as the same gateway origin for WebSocket upgrades, and drops
transport- or Hermes-managed header names such as `Authorization`, `Cookie`,
`Host`, `Origin`, `Referer`, and `X-Hermes-Session-Token`.

Projects are the workspace abstraction. A project may own multiple folders,
repositories, worktrees, and sessions; a bare new chat remains detached unless
the user enters a project or configures a default project directory. Use the
Projects UI rather than adding a second per-session folder-picker workflow.

Changing profiles or connection modes is a soft workspace switch, not another
cold boot. The shell and current management overlay remain mounted while
gateway-bound nanostores are wiped, query-backed data is invalidated, and the
new connection repopulates skeletons. This prevents rows or transcripts from
the previous gateway bleeding into the next one. Switching changes only the
foreground view and request route: it does not cancel turns or stop a backend,
and retained background sockets continue receiving events from running jobs.

### Verification

Run before opening a PR (lint may surface pre-existing warnings but must exit cleanly):

```bash
npm run fix
npm run typecheck
npm run lint
npm run test:ui
npm run test:desktop:platforms
```

Run `npm run test:desktop:all` for install, boot, update, packaging, or other
release-path changes.

### Troubleshooting

**The reset examples below are inherited Hermes instructions, not Eidolon defaults.** They can delete an upstream installation's environment or reset its permissions. Do not copy them into an Eidolon session. First identify the selected `HERMES_HOME`, managed-install path, and installed bundle ID, and back up relevant data. Retained identifiers here describe the upstream layout; they have not been renamed into a new repair procedure.

For macOS development from a local checkout, the backend can use the checkout’s `.venv`, `venv`, or an explicitly selected Python interpreter instead of the managed installation below. Removing a bootstrap marker does not force setup when the selected runtime remains usable. These examples are not a local-checkout repair procedure; do not substitute an Eidolon path or bundle ID into them.

Boot logs land in `HERMES_HOME/logs/desktop.log` (includes backend output and recent Python tracebacks) — check it first if the app reports a boot failure.

**macOS / Linux:**

```bash
# Force a clean first-launch setup
rm "$HOME/.hermes/hermes-agent/.hermes-bootstrap-complete"
# Rebuild a broken Python venv
rm -rf "$HOME/.hermes/hermes-agent/venv"
# Reset a stuck macOS microphone prompt (macOS only)
tccutil reset Microphone com.nousresearch.hermes
```

**Windows (PowerShell):**

```powershell
# Force a clean first-launch setup
Remove-Item "$env:LOCALAPPDATA\hermes\hermes-agent\.hermes-bootstrap-complete"
# Rebuild a broken Python venv
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\hermes\hermes-agent\venv"
```

> `%LOCALAPPDATA%\hermes` in these examples refers to the inherited Windows layout, not the current Eidolon desktop default. Resolve the actual home before troubleshooting; `HERMES_HOME` overrides may select another location.

---

## Project and upstream resources

- [Eidolon repository](https://github.com/AetherMesh-AI/Eidolon) and [Eidolon issues](https://github.com/AetherMesh-AI/Eidolon/issues) — this project's development and bug reports.
- [Upstream Hermes documentation](https://hermes-agent.nousresearch.com/docs/) and [website](https://hermes-agent.nousresearch.com/) — runtime background, not an Eidolon installer or support promise.
- [Upstream Nous Research Discord](https://discord.gg/NousResearch) and [Hermes issues](https://github.com/NousResearch/hermes-agent/issues) — upstream community and runtime reports, not Eidolon support channels.
- [Upstream Hermes releases](https://github.com/NousResearch/hermes-agent/releases) and [upstream license](https://github.com/NousResearch/hermes-agent/blob/main/LICENSE) — upstream artifacts and provenance, not Eidolon distributions.

---

## License

MIT — see [LICENSE](../../LICENSE).

Eidolon builds on Hermes Agent, originally developed by [Nous Research](https://nousresearch.com) and its contributors. Original attribution, license, and history are retained.

# Eidolon internal-name migration

The application now uses the `eidolon-agent` Python distribution, the `eidolon`,
`eidolon-agent`, and `eidolon-acp` commands, and the `eidolon_cli` package plus
`eidolon_*` root modules. Update integrations that import the old internal Python
paths. This transition does not ship `hermes_cli` or `hermes_*` import shims.

## First update across the rename

An already-running upstream-named updater cannot load the renamed package in a
fresh subprocess after its source checkout changes. Do not use an old
`hermes update` process or the already-installed PR6 update button to perform this
first transition. Use the latest installer from this branch, or a newly built
Eidolon Setup that runs it, then verify startup and preserved data. The cleanup
is planned as one release, targeting `v0.2.0-alpha`; no intermediate transition
release is planned. This source note does not claim that release assets have
already been published or that every installed-platform migration is verified.

Use the latest official installer or newly built Setup for this release. The
source installers are [`scripts/install.sh`](../scripts/install.sh) (POSIX) and
[`scripts/install.ps1`](../scripts/install.ps1) (Windows). Review the installer
before execution. Do not rely on an old PR6 update button for this first upgrade.

## Existing messaging gateway services

The current installers pause **before changing the source checkout or virtual
environment** when an old gateway registration points to the selected Eidolon
home (or one of its profiles) and its runtime. This includes legacy
`hermes-gateway*.service` units, `ai.hermes.gateway*` launchd jobs, and Windows
`Hermes_Gateway*` Scheduled Tasks or Startup launchers. The check also runs during
noninteractive Setup; skipping the interactive gateway prompt does not bypass it.

A pause preserves the source, virtual environment, gateway registration, and
messaging/provider configuration. The installer prints the exact commands for
each affected service or task: stop its supervisor, back up and retire its old
registration, rerun the installer, then install and start the canonical gateway
for that same profile. Follow those selected-registration commands rather than
removing all Hermes services. Old services are not converted automatically, and
an unrelated `~/.hermes` installation is not part of this migration.

The isolated POSIX migration test runs the current repository, virtual-environment,
dependency, CLI, configuration, and completion stages against an old fork
checkout with no registered gateway service. It verifies the new CLI and
`eidolon-agent` distribution, removal of stale `hermes-agent` virtual-environment
artifacts, and byte-for-byte preservation of provider settings, credentials,
custom persona, and sessions. Separate unit/plist and Windows XML/VBS/CMD fixtures
exercise refusal before mutation and unrelated-install isolation. Native Windows
and macOS supervisor lifecycles still require their platform validation; these
fixtures do not claim to have migrated a live OS service.

## State and profiles

- Default application home: `~/.eidolon`.
- New managed source checkout: `~/.eidolon/eidolon-agent`.
- `EIDOLON_HOME` is the canonical launch input and takes precedence over
  `HERMES_HOME`. Startup normalizes and consumes it; `HERMES_HOME` remains the
  profile/subprocess transport so an explicit profile can select its own home.
- A verified existing fork checkout under `~/.eidolon/hermes-agent` is a migration
  fallback. An unrelated `~/.hermes` installation is never adopted automatically.

## Deep links

New links use `eidolon://` and development links use `eidolon-dev://`. The desktop
registers only those canonical schemes, so it does not take over a separate Hermes
installation. Inbound `hermes://` links (and `hermes-dev://` in development) remain
accepted when delivered to Eidolon for compatibility.

## Deliberately retained compatibility names

The plugin entry-point groups `hermes_agent.plugins`,
`hermes_agent.plugin_capabilities`, and `hermes_agent.memory_providers`, skill
`metadata.hermes` keys, and project-local `.hermes/plugins`, `.hermes.md`, and
`HERMES.md` discovery names remain compatibility contracts. Keeping those keys
does not make the old Python import package available.

Real provider/model IDs, OAuth client IDs, upstream source citations, license
notices, and original author credits retain their actual identities. The bundled
`hey_hermes` wake-word asset still detects its trained “hey hermes” phrase; a brand
rename does not retrain the model.

The desktop AetherMesh page is a **Coming soon** placeholder for the planned
AetherMesh P2P AI network and optional AetherMesh-core SDK. Existing provider
configuration remains separate and unchanged by that placeholder.

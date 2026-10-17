# Fresh organization workspaces (isolation version 1)

`organization.workspaceCreation` advertises a distinct capability. Creation does
not call the legacy `profiles.create` RPC, whose fresh-profile behavior includes
model seeding and backward-compatible credential inheritance. Older runtimes
therefore reject the new method before creating anything.

## User-visible scope

A new workspace has its own canonical profile, organization and owner IDs,
execution ledger, settings, empty credentials file, and workspace directories.
Creation copies no model configuration, credentials, history, tools, plugins,
skills, or source workspace content. It starts no service or model work and never
changes the active profile.

Version 1 supports **tool-free organization work using a profile-owned OpenAI API
key**. After explicit selection of the new workspace, its existing Keys settings
can save `OPENAI_API_KEY` through profile-scoped `PUT /api/env`. Its configuration
settings can select `model.provider: openai-api` and an explicit `model.default`
through profile-scoped `PUT /api/config`. These operations require the ordinary
credential/setup permissions; creation does not authorize or perform them.

The new key stays in that profile's `.env`; saving/reloading it does not publish
it into the shared process environment. Reveal, rotation and removal use the
same explicit profile scope; any auth-store bookkeeping must remain in that
profile's regular `auth.json` and `auth.lock` paths. The supported endpoint is
`https://api.openai.com/v1` with the chat-completions API. Existing bounded
organization execution, owner chat, and team-generation paths use that lane.

Normal agent chat, tools, auxiliary model calls, OAuth/subscription providers,
credential pools, SDK identity chains, custom endpoints, and external-process
providers are not supported in these version-1 workspaces. Unsupported routes
fail closed, including when the root profile is authenticated. The UI must state
these limits before creation and must not advertise those features as ready.
Shared/global provider access would require a separate explicit opt-in feature.

This is an **application credential boundary**, not a container, OS account, or
filesystem sandbox. It does not prevent the owner or other software running as
the same OS user from changing files. Existing unmarked profiles retain their
legacy behavior.

## RPC contract

- `organization.workspaceCreation({})` is read-only. An available response has
  `capabilityVersion: 1`, `isolationVersion: 1`,
  `credentialMode: profile-local-openai-api`,
  `executionMode: tool-free-organization`, `supportedProviders: [openai-api]`,
  `activation: explicit-client`, and an `origin` containing the served canonical
  profile and its organization/owner IDs. An unconfigured origin or a platform
  without atomic no-replace publication returns unavailable.
- `organization.createWorkspace` accepts exactly `capabilityVersion`,
  `expectedOrganizationId`, `expectedOwnerId`, `displayName`, `ownerDisplayName`,
  and `idempotencyKey`. No requested profile, arbitrary path, clone flag, provider,
  or secret is accepted. The transport's immutable launch home is the authority.
- `organization.workspaceCreationStatus` accepts the same version, origin IDs,
  and key without names. It is read-only and returns `unknown`, `pending`, or the
  original created result. A pending operation is completed by retrying the
  original create payload with the same key.
- The created result returns the server-selected canonical `profile`, independent
  `organizationId`/`ownerId`, isolation/mode fields, original metadata `descriptor`,
  and `activated: false`. The client must independently verify the new served
  `organization.descriptor`, including its `scope` and `isolation` identity,
  before an explicit switch. A UUID is never cross-profile authorization.

## Publication and recovery

The origin identity is checked before any write, under the publication lock, and
again immediately before publication. A journal binds the key to the complete
normalized payload and generated profile. Changed payloads conflict; a replay
cannot select a new profile or borrow a receipt from another origin.

The stage lives outside `profiles/`, under `.organization-workspaces/operations`.
A provisional isolation marker exists before profile files are initialized.
The fresh metadata and ledger are initialized without a service; SQLite
connections are closed and the WAL is checkpointed before publication. A ready
marker binds the canonical profile, organization/owner IDs, and operation hash.
The prepared result is persisted before publishing, so a lost response after the
rename recovers the original IDs/result.

A durable per-profile isolation registration is also written before publication.
Registered profiles with absent, malformed, mismatched, or unsupported markers
cannot fall back to legacy credentials. Symlinked marker, registration, journal,
profile, configuration, or credential paths are rejected. Native no-replace
directory publication refuses an existing target, even an empty one; ambiguous
targets are not removed. Partial stages remain outside profile enumeration and
are resumed by an exact retry. Even a prepared ready marker outside the published
canonical profile cannot enable inference. Runtime identity requires the matching
registration, durable creation receipt and published ledger.

## Provider boundary

The main runtime bypasses the legacy discovery ladder for marked profiles. It
requires an explicit local model declaration and obtains the key only from that
profile's regular `.env` file. The agent is bound to its home at initialization;
every SDK construction/rebuild checks that binding and revalidates the route.
The physical-send boundary also rechecks the binding and current key, including
when the request client was cached. An explicitly disabled provider stays disabled.
No credentials are taken from root auth/pools/OAuth, process environment,
Claude/Codex homes, external secret sources, or provider-supplied clients.
The OpenAI SDK receives explicit key,
endpoint, empty organization/project, zero SDK retries, and a private HTTP client
with environment proxy/TLS discovery and redirects disabled. Tools and the
environment probe are disabled before agent initialization side effects.
Both plugin-manager discovery and the separate model-provider plugin registry
stay inert for these profiles, including warmed global provider registries.
Profile-owned file logs reject cross-home records using the producer's stamped
home, even when a queued record is delivered under another profile's context.

Tests use synthetic homes/keys and mock HTTP transports. They cover independent
ledgers, RPC authority, explicit REST setup, inherited-credential suppression,
actual SDK requests and organization-agent completion, concurrent profile
contexts, corrupt markers/symlinks, and crash/replay recovery. Focused local
results do not replace canonical hosted regression and supported-OS validation.

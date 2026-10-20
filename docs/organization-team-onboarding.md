# Starter-team onboarding

The Home screen offers starter-team setup for an unused organization in the current
served profile. This configures that organization; it does not create or switch a
separate profile or erase an existing team.

1. Set the organization and owner names.
2. Connect a supported model using the existing provider setup.
3. Describe the jobs, desired outcomes, and optional naming instructions.
4. Generate a proposal, edit the persistent executive/manager/worker team, and
   review the validated result.
5. Explicitly create the team, then check model, allowance, and access readiness
   before submitting a first request.

Manual configuration is a secondary option and includes presets. It does not
require a model call. Model generation is a billable provider operation when the
selected provider charges for calls. Closing the dialog cannot refund a dispatched
call. Retrying the same generation or creation intent reconciles its retained
receipt rather than repeating the action. Unknown provider outcomes are retained
conservatively and are not replayed automatically.

## Trust and persistence

Generated JSON supplies names, purposes, responsibilities, reporting relationships,
teams, and bounded work capabilities only. It cannot grant credentials, tools,
repository access, budgets, or model routes, and it cannot start work. The root
executive retains its fixed identity and Fixed model selection. Existing independent
review and runtime support identities remain visible in the reviewed proposal.

Creation uses the served profile's owner transaction, binds the reviewed source and
proposal, and stores its idempotency receipt with the roster and executive identity
changes. Existing meaningful work, conversations, memory, or explicit staffing
prevent initial setup from silently replacing an organization. Untouched automatically
seeded identities are handled without deleting historical identities.

Concurrent creation retains the bounded writer wait. If another creator has
already committed, an identical intent can recover its receipt through a
read-only transaction while the first writer finishes connection cleanup.
Recovery checks the organization identity and exact idempotency key and input
hash. A different intent conflicts; an absent committed receipt leaves the
operation busy, with the original intent safe to retry. Creation does not
automatically retry writes or promise success under indefinite contention.

Generation uses the guarded direct chat-completions boundary and a durable setup
allowance of at most three physical sends and 120,000 reserved tokens. Complete
invalid proposals remain charged; editing the input starts a new explicit attempt
within that allowance. Unknown outcomes remain blocked rather than being replayed. Configured tighter ceilings and an enrolled shared allowance also apply.
It does not use unsupported Codex app-server execution or bypass that limitation
through another provider. A saved team is not evidence of working provider access,
repository permissions, or a running background organization.

Availability and readiness inspection are passive, including in a cold process: they
do not create profile files, initialize the ledger, migrate config, or fetch provider
catalogs. Initial setup is requested only after the explicit identity save; retries
remain bound to that same owner.

## Verification

Python tests cover the proposal parser, strict roster validation, scope and receipt
handling, persistence, cold-process read-only behavior, and physical localhost HTTP sends. Desktop tests cover the
scoped client and editable setup state. Native acceptance exercises Home, generation,
review, explicit creation, and retained names after reload against a local scripted
provider. No real credentials, paid inference, or user-profile activation is required
for these tests.

# Owner-selected projects and multi-repository objectives

An objective can coordinate work across several existing repositories. The owner
selects project IDs; the manager assigns each file task to one selected project,
with explicit dependencies between tasks. Independent file scopes can overlap in
time, including across repositories. Existing persistent managers and workers
retain their team, capability and reporting-line checks.

## Configure existing authority

`organization.projects` is an optional owner-configured registry. Each entry names
one existing `read_roots` alias, one existing exact `project_grants` recipe for that
alias, and the team permitted to handle that project's tasks:

```yaml
organization:
  read_roots: [/absolute/path/api, /absolute/path/web]
  project_grants:
    - id: api-tests
      files: [root0/api.py, root0/test_api.py]
      execution: {root: root0, recipe: python_unittest}
    - id: web-tests
      files: [root1/render.py, root1/test_render.py]
      execution: {root: root1, recipe: python_unittest}
  projects:
    - {id: api, root: root0, recipe: api-tests, team: backend}
    - {id: web, root: root1, recipe: web-tests, team: frontend}
```

The example illustrates bindings, not a complete runnable configuration. Existing
organization and individual worker grants (`read_file`, `patch`, `run_tests`,
`integrate_source` as needed), configured staff, and the runner's fixed recipe
requirements remain necessary. Registering a project grants none of them. The
runner remains the bounded Python unittest snapshot runner; it does not gain npm,
shell, arbitrary commands, networking, dependencies, or filesystem permissions.

Up to eight unique project IDs and distinct aliases are supported. Recipes remain
exact owner-maintained file lists. Alias positions are stable, including root1 and
later roots; a project's task cannot read, list, search or write another alias.
The owner may configure multiple aliases to the same underlying files, but the
existing canonical file ownership checks still serialize overlapping work.

## Submit and route

Home offers the configured project IDs when creating an objective. With a registry
configured, source-project delivery requires an explicit selection. Managed
writing can omit projects, but the manager cannot then create file work using the
registry. Existing installations without a registry retain their legacy behavior.
The `organization.create_objective` RPC accepts `projectIds`; the Python store
accepts `project_ids`. Retrying the same idempotency key with a different selection
is rejected.

A planned file task must use a selected `projectId`, its configured team, and, for
edits, exact `writePaths` inside its alias:

```json
{"title":"Update web client", "description":"Consume the reviewed API change",
 "type":"work.edit", "projectId":"web", "team":"frontend",
 "writePaths":["root1/render.py"], "dependsOn":[0]}
```

`dependsOn` refers to earlier task indexes. Dependency completion still requires
independent review and applicable managed edit checks. Different repositories do
not erase semantic dependencies. Cross-project context is supplied through the
reviewed dependency artifacts, not by giving every worker all repository roots.

Objective selections pin the exact root path, recipe definition and team in the
local ledger. Model context receives only IDs and aliases. Reconfiguring or
revoking a selected project fences continued execution rather than silently
retargeting old work. Restore the exact approved configuration or create a revised
objective. Staff transfers must preserve a bound task's project team.

## Per-project evidence and completion

When project tests or automatic source integration are required, every selected
project needs contributing reviewed work, its own exact snapshot run, independent
review of that run, and the required source receipt. A successful repository does
not stand in for another. The inspector shows each repository's pending state,
execution result, review, and source branch separately. Final acceptance receives
all required current evidence and still judges the objective's complete acceptance
criteria.

All project runs share the objective's existing `max_project_runs`, deadline,
model/stage and token budgets. Selecting repositories does not multiply those
budgets. Configure enough runs for the selected repositories and any approved
retries. An interrupted run remains unknown; it is not silently replayed.

Source integration publishes a separate output branch in each selected repository
and verifies its exact commit and reviewed bytes. Original HEAD, working tree and
index remain untouched. It does not merge, push or deploy. Publication across
repositories is not atomic: if a later repository fails, earlier retained branches
remain auditable and objective acceptance stays blocked until every required
outcome verifies. Removing or altering an output branch invalidates its receipt.

Actual functional execution requires supported Linux isolation. macOS and Windows
continue to fail closed where that isolation or safe filesystem primitives are
unsupported. A managed result or a model's approval alone is never proof that
project tests or source integration happened.

## Guided repository configuration drafts

The desktop Organization page can prepare a repository binding from the current
profile's existing root aliases, exact execution recipes, and configured teams.
Only unused root aliases and a new canonical project ID are valid; the registry
still has a maximum of eight bindings. The form never accepts a filesystem path
or creates a grant, recipe, team, credential, or background-execution setting.

`organization.projectSetup` exposes the bounded choices and a revision covering
the current file and durable policy. `organization.projectDraft` validates the
selection against that revision and returns one YAML list item for owner review.
It does **not** save, activate, or change any project or objective. Refresh after a
configuration conflict; the same-profile form retains its entered selection.
Switching profile clears the form's previous profile state. Open objectives and
managed project settings block preparing a change.

The owner can copy the list item into their profile's existing
`organization.projects` list after reviewing it, preserving other entries and
checking the configuration again before applying it. A draft is not a durable
approval or reservation of aliases, grants, teams, or runtime readiness. Its
choices may become stale after preparation. Existing objective bindings stay
immutable, and the existing runtime revocation fences remain authoritative.

### Why this first slice does not save automatically

Gateway discovery, cold service startup and ledger reopen now serialize source
reads and policy adoption through the ledger write transaction. Invalid profile
YAML durably pauses same-profile hosts; valid repair is idempotent and does not
replay unknown work or reset model reservations. These observer guarantees do
not make file replacement atomic with a ledger update: a process can still crash
between those two durable writes. File compensation alone does not solve that
crash boundary. Automatic setup requires a
separate design for durable intent, source revision validation, admission fencing,
and recovery across every file-backed configuration adoption path. Until then,
this surface deliberately has no configuration-write RPC.

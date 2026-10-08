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
runner supports fixed Python unittest and optional pytest snapshot recipes. It does
not gain npm, shell, arbitrary commands, networking, dependency installation, or
filesystem permissions.

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

## Reviewed repository registration

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
checking the configuration again before applying it. Choose either ledger activation
or manual YAML configuration for an identity, never both in the same profile.
A draft is not a durable
approval or reservation of aliases, grants, teams, or runtime readiness. Its
choices may become stale after preparation. Existing objective bindings stay
immutable, and the existing runtime revocation fences remain authoritative.

### Explicit ledger-only activation

After preparing a draft, the owner may review and confirm **Save and activate**.
The disclosure names the profile's organization ledger as the storage destination.
`organization.projectSave` requires `confirmSave: true`, the draft revision, and
an idempotency key. It does not edit `config.yaml`, start an objective or provider,
or create filesystem roots, recipes, teams, tool permissions or credentials.
Copying the draft remains available on older runtimes without this capability.

One SQLite writer transaction re-reads profile sources, validates the source and
policy revision, rejects open objectives/requests and managed configuration,
checks execution locks for cancelled calls still unwinding, records the identity
and its canonical-root/exact-recipe hash, adopts the effective policy, and records
an idempotent receipt. A process crash before commit leaves none of those changes;
a crash after commit leaves all of them. Retrying the same request returns its
receipt without adding a duplicate. The new tables retain only identity metadata,
binding hashes and receipts, never YAML bytes, private paths or provider secrets.

YAML remains the grant authority. Ledger definitions contribute an additional,
bounded identity layer; they never override a YAML identity or reuse its root
alias. Duplicate IDs/roots, changed concrete roots or recipes, or missing teams
retain the registered definition but durably fence organization admission. Every
policy adopter preserves this fence, including reopen and ordinary reload. Setup
shows the affected IDs and repair instructions. Restore the exact approved source
binding or remove the conflicting YAML identity and reload; recovery never
silently retargets an existing identity or replays unknown work. Owner staffing
changes that would remove a required project team are rejected transactionally.
Existing objective bindings, budgets and execution receipts remain unchanged.

Profile export retains reviewable identity metadata without exporting the ledger;
full backups and quick snapshots include consistent ledger copies. See
[Project registration portability](organization-project-portability.md).

### Source observation is not a file transaction

Gateway discovery, cold startup and file policy reload serialize source reads
and policy adoption through the ledger writer. Scoped hosts refresh local `.env`
values there. Unscoped process environment variables and external-source caches
retain their existing reload/restart lifecycles. An arbitrary external editor is
not synchronized by SQLite: its edit after a source read is observed on the next
normal file reload. No cross-resource or arbitrary-editor atomicity is claimed.

Automatic YAML replacement deliberately remains absent. Even a durable intent
followed by fsync and rename cannot prevent an independent editor changing the
file between the final digest check and replacement. Atomic replacement is not a
compare-and-swap. Keeping registration wholly in SQLite avoids that lost-update
window and leaves comments, secrets and unrelated YAML bytes untouched.


## Optional isolated pytest recipe

An owner may explicitly select `recipe: python_pytest` in an existing project's
execution grant. The default remains `python_unittest`; registration and model
requests cannot switch the recipe or grant files. The same exact selected UTF-8
snapshot, limits, independent test review, source preimage checks and unknown-run
recovery apply. The default filename pattern remains `test*.py`; select another
bounded `.py` pattern and test directory explicitly when needed.

This supports pure-Python tests with ordinary assertions, parametrization, and
in-memory fixtures, including explicitly selected `conftest.py` files. It is not
general pytest compatibility. Temporary-file fixtures, subprocesses, threads,
networking, unselected files, project-installed dependencies and arbitrary test
commands remain unavailable. The only writable file is `/scratch/work.dat`.
Pytest plugin autoload, project configuration/addopts, assertion rewriting and
cache writes and the logging plugin (`caplog`) are disabled; capture uses Python streams. Conftest and test hooks
are untrusted code inside the same sandbox and can falsify reported results,
just as unittest code can. Independent review remains mandatory.

The optional runner copies only Python source files from the application's own
installed pytest 9.1.1, pluggy 1.6.0, packaging 26.0, iniconfig 2.3.0 and pygments
2.20.0 packages. These versions match the repository lock. No complete virtualenv,
site-packages directory, `.pth`, host plugin, home directory or project runtime is
mounted. Missing, linked, unexpected or incompatible package payloads fail closed
with an unsupported receipt. The runner never installs or downloads anything.

System Python 3.10 additionally needs application-installed exceptiongroup 1.3.0,
the pure-Python tomli 2.2.1 wheel and typing_extensions 4.15.0; native CI explicitly supplies these
compatibility packages. They are not implied by installing the application dev
extra on Python 3.11 or newer. System Python must be at least 3.10. Receipts retain
system interpreter identity, installed dependency names/versions and hashes of
the copied package files and complete runtime. These hashes describe the actual
installed bytes; they are not upstream signatures or an authenticity guarantee.

Successful acceptance requires actual Linux bubblewrap/seccomp isolation. The
permanent Linux lane requires passing real pytest, failing/empty/incomplete test
receipts, hostile-environment and forbidden-operation checks, and an independent
review/source-integration loop. macOS and Windows retain their explicit unsupported
behavior; there is no host execution fallback.

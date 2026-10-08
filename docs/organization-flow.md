# Persistent organization, execution and reviewed workspace edits

For the current owner-resolution queue, integrated acceptance, validation and
legacy-surface behavior, see [Organization owner loop](organization-owner-loop.md).

The Organization workspace now submits durable work to the Python backend. It no
longer manufactures a team or task graph when an objective is entered. The
separate example preview remains explicitly fictional and does not contact a
provider.

For owner-selected repository bindings, cross-repository tasks and per-project evidence,
see [Multi-repository objectives](organization-projects.md).

## Persistent identities and reusable specialists

Open **Organization** before, during or after an objective to inspect the same
roster. Each identity has its own purpose, scoped responsibilities, reporting line,
capability routes and retained context. Idle is a work state. Disabled and retired
are roster lifecycle states. Stopping execution, completing an objective or
closing the desktop does not delete identities or their history. A disconnected
UI labels its last received snapshot; it does not claim the backend is running.

The roster is not a count of concurrently running model calls. Explicit roster
configuration is bounded by `max_members` (default 16, configurable from 1 to 64); `max_inflight` independently bounds occupied
execution slots. `max_workers` only seeds the initial default worker pool when
`roster` is omitted. A specialist can take successive compatible assignments without
being replaced by a new identity. Changing concurrency does not trim the roster.

The agent inspector shows a stable identity ID and creation time, purpose,
responsibilities, context summary, personal memory and the 12 most recent retained
history records. Memory has four bounded categories: facts, decisions, lessons and
open questions, each retaining at most 24 items of at most 1,000 characters, with a 4,000-character
total per category. A
successful, lease-owned finish records memory and history atomically with the work;
an invalid, stale or cancelled result cannot write them. History links request,
objective, task and evidence IDs. Model turns receive only four recent history
summaries, each capped at 300 characters, a context summary of up to 500
characters, and the four most recent items per memory category capped at 500
characters each. The inspector retains the broader bounded view. This is a bounded working memory with provenance,
not a copy of every model transcript or a replacement for exact evidence.

Managers may coordinate across domains through the existing task graph. Task
inspection distinguishes the planner (`assignedById`), the scoped managing identity
(`managingAgentId`) and the assigned worker. The worker must match the exact team,
capability and reporting scope. A requested specific worker must also be eligible.
Neither collaboration nor memory changes tool grants, provider isolation or review
requirements. Leaders remain tool-free.

Home lets the owner choose an existing active Executive and one of that Executive’s
active Managers for a new objective. Switching the Executive clears an incompatible
Manager selection. This selects responsibility for planning, integration and
acceptance; it does not create or activate staff. Existing callers that omit these
IDs keep the established default Executive and Manager.

Use **Organization → Manage organization** to create and edit members in the current profile. The equivalent initial configuration shape is:

```yaml
organization:
  max_inflight: 2
  capabilities: [work.draft, work.analyze]
  roster:
    - id: product-executive
      name: Product executive
      role: Executive
      manager_id: owner
      team: product
      responsibilities: [Evaluate integrated product outcomes]
    - id: research-manager
      name: Research manager
      role: Manager
      manager_id: product-executive
      team: research
      responsibilities: [Plan research and coordinate analysis]
    - id: writing-manager
      name: Writing manager
      role: Manager
      manager_id: product-executive
      team: writing
      responsibilities: [Integrate reviewed writing]
    - id: analyst
      name: Research analyst
      role: Worker
      manager_id: research-manager
      team: research
      capabilities: [work.analyze]
      purpose: Analyze supplied context and preserve reusable findings.
      responsibilities: [Identify evidence and unresolved questions]
    - id: writer
      name: Brief writer
      role: Worker
      manager_id: writing-manager
      team: writing
      capabilities: [work.draft]
      responsibilities: [Write briefs from reviewed supplied context]
```

`role` defaults to Worker. Each Manager must report to an Executive, each Worker
to a Manager, and each Executive to the human Owner. `responsibilities` accepts up
to 12 nonempty strings of up to 500 characters; `purpose` accepts up to 3,000
characters. Existing configured IDs are reconciled without changing their durable
identity or retained context. Removing an entry retires it; adding the same ID back
reuses its identity. Desktop roster and capacity saves apply transactionally to the
live profile; no file edit or restart is needed for these management actions.
The editor preserves untouched settings, validates role/reporting relationships,
and refuses stale-generation writes. Responsibility text describes a member's job;
it does not grant authority. Accepted `capabilities` routes and explicit `authority`
are separate requirements for handling a typed request.

### Typed requests and scoped management

A stage can pause its assignment with a bounded `requests` list. Each request
retains its type, requester identity, requested outcome, required authority, parent
request, dependency IDs and evidence IDs. Supported kinds are:

- `request.question`: peers require both this accepted type and `answer.question`.
- `request.decision`: peers require both this accepted type and `answer.decision`.
- `request.hire`: a Manager or Executive must accept this type and have explicit
  `staff.manage` authority. It can apply the exact proposed member upserts and
  explicitly selected work/memory transfers within its own team and `managed_teams`.
  `*` explicitly permits all teams. New autonomous members start tool-free and
  cannot introduce a new provider/model pair or authority their manager lacks.
- `request.permission`: always reaches the human owner. Its recorded decision
  cannot change credentials, tools, roots or execution grants.

No eligible handler means a visible Needs You action. Questions/decisions accept
an owner answer; hiring and permission present approve/deny actions. The staffing
proposal is complete and visible before approval, including any transfers.
Responses retain text, responder, decision and time. Parent requests remain
`waiting_response` until linked responses settle, then resume on the exact
persistent requester with those responses as context. Independent work continues.
Answers are not external-action evidence, and denied permission remains binding.

Request trees default to depth 4 and at most 4 child requests per stage. The ledger
also bounds typed requests to 24 per objective and rejects dependency cycles.
Exhaustion stays visible for human handling; responses do not erase execution
budgets. This does not add arbitrary project command execution.

**Manage organization** also supports explicit same-role handoffs. Select exact
open assignments and optionally bounded retained memory; completed work remains
attributed to the original identity. The runtime checks active execution,
capabilities, teams, reporting lines and objective leadership before applying the
configuration and transfers together. Recent changes show actor, linked request,
subject and before/after state. Provider credentials and global tool grants remain
outside these management actions.

## Try a supported objective

Configure a supported direct API model in the existing profile settings, open
**Home**, and submit:

> Draft a decision brief from these facts: option A costs $40 per month and needs
> two hours of setup; option B costs $70 per month and needs thirty minutes of
> setup. We have a $60 monthly budget. Compare the options, state the uncertainty,
> and recommend the next step. Do not research or purchase anything.

Submitting starts model work and can incur the configured provider's normal
charges. Current-profile provider selection is reused; the organization does not
create credentials, accounts, profiles, or external-agent connections.

The manager creates a task graph. Eligible workers claim requests by request
type, team and priority. Each deliverable is persisted before a separate reviewer
checks it. Dependencies unlock only after approval. Open **Requests**, **Tasks**,
**Activity**, and the artifact inspector to see real state and the full retained
text. A completed new objective requires every current task to have passed review,
required control work to have settled, and independent executive acceptance of an
integrated deliverable against the current criteria. Review is a model's assessment of supplied
context, not a guarantee that statements are true or an external action happened.

### Supported capabilities and visible limits

- `work.draft`: produce complete text from the context supplied by the owner.
- `work.analyze`: analyze that supplied context and retain the full analysis.
- `work.inspect`: inspect explicitly granted local project text with `read_file`,
  then retain an analysis and exact tool-result receipts. Disabled by default.
- `work.edit`: capture source files into an agent-owned workspace, read their
  exact revisions, and propose a bounded group of replacements. Disabled by default.
- `request.apply`: apply an independently approved proposal to the managed
  workspace only, with explicit organization and author `patch` grants.
- `request.validate`: check exact managed revisions using declarative validators.
  This does not execute project commands or establish that a test suite passed.
- `request.merge`: the default manual source-delivery intervention. After
  applying exported output, the owner can ask the backend to verify source bytes.
- `request.project_test`: runs an exact owner-configured Python unittest snapshot
  through the Linux-only isolated runner, with explicit author and organization grants.
- `request.test_review`: a distinct reviewer assesses the exact snapshot and run
  receipt. Failed tests or review remain visible as `request.project_failed`.
- `request.source_integrate`: with its separate grant, writes a new reviewed Git
  branch after successful tests and independent review, preserving the original
  HEAD, index and working tree.
- `request.plan`: the manager proposes an acyclic graph with explicit dependency
  indexes and acceptance criteria.
- `request.review`: a distinct logical reviewer assesses the exact persisted
  artifact bytes, identified by evidence IDs and SHA-256 hashes.
- `request.integrate`: the manager assembles a full objective deliverable.
- `request.accept`: a distinct executive checks current criteria, exact task and
  integrated evidence, conflicts, and explicit required checks.
- `request.hire`: legacy staffing demands activate eligible existing workers;
  linked typed staffing requests can create or update persistent members through
  exact scoped proposals and audited transfers. Without an explicit roster, the
  initial worker pool uses this profile's settings. An explicit empty roster has
  no fallback workers. Staffing does not create profiles, grant new tool access,
  install software, or mean real-world employment.

The human Owner sets direction for Executive → Manager → Worker. The old Director
is now a Manager; its stable `director` ID and historical request, event and
artifact references are preserved. The existing planning Manager reports directly
to its Executive, rather than through an extra Director tier. Employee roles become
Worker roles without discarding identity or work history. Staffing and backend
application/validation remain deterministic. Planning, working, reviewing,
integration and executive acceptance run as separate bounded AIAgent turns.

Browsing, source working-tree writes, sending, purchasing and arbitrary command
execution remain unavailable. The optional controlled runner supports only the
fixed Python unittest recipe below, and optional source integration writes a new
Git branch rather than modifying the original working tree. File reads require
the explicit inspection grant described below.
A request needing another tool or missing source material stays
**Pending intervention**. The manager must preserve the requested outcome rather
than pretend a text draft performed an external action. Valid unknown request
types/teams are preserved so the router can explain why no eligible agent exists.
There is no generic fallback that silently assigns an incompatible worker.

HTTP-based providers continue through the existing runtime resolver. Transports
that inherently expose external tools and cannot enforce the exact tool grant, such
as native app-server/ACP routes and search-enabled models, are blocked for this
organization execution. Other Eidolon chat/provider surfaces are unchanged. Organization admission also
requires a verifiable output cap, single-candidate semantics and bounded physical
transport attempts. Routes that drop the cap (such as consumer Codex Responses),
raise it later (such as native Gemini thinking), expose managed relay fan-out or
use an unbounded custom client remain pending before inference. Supported native
Anthropic model aliases are preserved; Bedrock uses an organization-local bounded
SDK client without changing shared chat clients. Existing provider configuration,
credentials and permission boundaries are not expanded.

When a shared backend executes another profile, missing credentials cannot fall
back to the launch profile. That isolated mode requires an explicit provider and
currently blocks AWS Bedrock/Mantle SDK identity chains, Azure Entra ID's default
credential chain, and Vertex identity resolution, which can consult process-wide
SDK credentials. Profile-scoped direct API-key/OAuth routes remain available;
the launch profile's existing SDK-provider behavior is unchanged. Nonbuilt-in
context engines remain blocked because they can retrieve ungranted material.
Configured model/session shell hooks, plugin prompt extensions and LLM middleware
require intervention. Inspection also rejects tool hooks/middleware that can
rewrite the dispatch. Built-in accounting remains available. Native app-server
permission descriptions are not enforceable tool grants, so those routes remain
pending rather than borrowing the interactive chat session’s authority.

## Controls, persistence and recovery

- The backend owns all objective, task, request, artifact and review states.
  Refreshing or closing the UI does not stop its scheduler while the backend runs.
- Cancelling fences all remaining request tokens immediately and interrupts live
  calls. Already-running provider calls can take time to unwind; their slots stay
  occupied until they actually exit. A cancelled result cannot be committed.
- Work survives a backend restart in the profile's `organization/state.db`.
  Queued requests resume. Interrupted/expired claims become visible interventions,
  not blindly replayed provider calls. Use **Retry** after inspecting the reason.
- Retry attempts, task count, concurrent calls, configured roster entries, open objectives,
  and review revisions are bounded. Exhausted budgets require a revised objective.
- Duplicate submissions use a persisted idempotency key; reuse with different
  input is rejected. Completed results and stale tokens cannot create duplicate
  tasks, reviews or artifacts.
- UI state is scoped to its connection/profile. Prototype local storage is not
  promoted into the runtime or silently replayed.
- The default snapshot includes every open objective and the latest 25 settled
  objectives. Artifacts have bounded previews; the inspector retrieves full
  retained content by its evidence ID. No client filesystem path is accepted.

Organization work resumes automatically for the launch profile and profiles the
backend is configured to multiplex. A different profile can be activated through
the existing authorized profile route; selecting it resumes only its previously
persisted queued or running work. Changes to organization policy take effect
when that profile's organization service restarts. A shared ledger records a grant
and routing generation. Adopting changed roots, tools, staff routes or worker
capacity fences in-flight claims from older instances, including backend apply
requests. A stale instance cannot admit, retry or finish work; reconnect/restart it.
Owner cancellation and read-only history inspection remain available. Operational
slot and timeout settings retain their existing per-process bounds while old
provider calls unwind.

## Configuration

The `organization` section of the existing profile `config.yaml` supports:

```yaml
organization:
  gateway_enabled: false # opt in to existing-ledger execution in a running messaging gateway
  max_workers: 2           # 1–8 initial default workers, only when roster is omitted
  max_inflight: 2          # 1–4 occupied execution slots, independent of roster size
  max_members: 16          # 1–64 persistent configured members
  max_request_depth: 4     # bounded linked-request ancestry
  max_requests_per_stage: 4 # bounded questions/decisions/hire/permission requests per stage
  max_tasks: 12            # 1–24 tasks per manager plan
  max_open_objectives: 20  # 1–100
  max_attempts: 2          # 1–3 attempts per request
  max_revisions: 2         # 0–3 review-requested revisions per task
  max_replans: 2           # 0–3 additional objective planning rounds
  max_stages: 120          # 4–300 admitted stages across the objective
  max_owner_resolutions: 12 # 1–24 durable owner resolutions per objective
  max_output_tokens: 8000  # 256–16000 per model call
  max_context_tokens: 128000 # 4096–2000000; also bounded by the actual model window
  max_model_calls: 120     # 1–1000 conservative physical-attempt reservations
  max_total_tokens: 8000000 # 4096–1000000000 reserved input + output tokens
  objective_timeout_seconds: 86400 # 60–604800, includes waiting for owner input
  max_cost_usd: null       # optional explicit configured-cost admission ceiling
  model_costs: []          # exact provider/model and conservative USD rates; no guessed prices
  lease_seconds: 45       # 15–300; live calls renew their leases
  timeout_seconds: 180    # 30–600 per stage
  team: general
  capabilities: [work.draft, work.analyze]
  tool_grants: []          # no tool is enabled implicitly
  read_roots: []           # up to 8 explicit local directories
  project_grants: []       # exact selected files and fixed test recipe; no inferred commands
  max_project_runs: 4      # 1–12 durable run admissions per objective
  max_tool_calls: 8        # 1–20 per request attempt
  max_tool_result_chars: 12000 # 1000–20000 per retained tool result
```

Limits do not authorize external actions. Staff, tools or providers are never
silently installed or broadened to satisfy a request. Disabling a capability
removes it from eligible worker routing. A provider turn is also token/iteration bounded; normal provider billing and limits
still apply. See [completion budgets and evidence](organization-owner-loop.md#keep-completion-within-an-explicit-budget)
for reservation accounting, deadlines and audited bounded evidence reads.

Each optional model_costs entry has exactly provider, model,
input_usd_per_million and output_usd_per_million. Rates are explicit nonnegative
USD decimal ceilings configured by the owner, not a bundled price catalog.
A non-null max_cost_usd requires a matching exact route before each send. No
example monetary rates are supplied because provider charges vary. Counters and
original objective ceilings survive configuration changes and restarts.

## Opt in to real local file inspection

The owner configures grants in the current profile’s existing config.yaml. Merely
submitting an objective, selecting a capability label, retrying, or hiring staff
cannot change these settings. For example, after reviewing the intended source
folder, an owner can configure:

```yaml
organization:
  capabilities: [work.draft, work.analyze, work.inspect]
  tool_grants: [read_file]
  read_roots: [/absolute/path/to/approved-project]
  max_workers: 2
  max_tool_calls: 8
  max_tool_result_chars: 12000
  roster:
    - id: project-reader
      name: Project analyst
      team: engineering
      capabilities: [work.inspect]
      tool_grants: [read_file]
    - id: brief-writer
      name: Brief writer
      team: communications
      capabilities: [work.draft, work.analyze]
```

Restart that profile’s backend after the configuration change. Submit:

> Have engineering inspect root0/README.md and summarize the documented setup
> steps, citing the file result. Then have communications write a concise setup
> checklist from the reviewed findings. Do not execute commands or change files.

Only the model-provider fixture was used in development tests. Running this
example with an actual configured provider incurs its normal charges and sends
the requested file results to that provider. Use a project directory whose
contents you intend the configured provider to receive.

The manager sees declared staffing routes and grants without gaining the reader’s
tool itself. The staffing manager activates matching configured workers. New
worker entries are available until activated; disabled or removed entries retain
their identity and history but never become generic fallback workers. Optional paired `provider` and `model` fields select an existing
same-profile provider route. The resolved identity must match; unavailable
providers become intervention instead of silently falling back. Staffing does
not create or connect separate profiles. Reviewers are independent logical turns,
scoped to each configured team, and have no tools.

Optional `list_files` and `search_files` grants provide bounded discovery and
literal search through the same source boundary. Both organization and staff
must explicitly grant each tool; `read_file` does not enable them implicitly.

The current reader supports POSIX hosts with no-follow directory-descriptor
operations (Linux/macOS). On unsupported hosts, including Windows, inspection
stays pending with an explicit reason; text-only work remains available. Each
configured root and path component is opened without following symlinks, and
roots stay pinned for the stage. Symlinks, multiply-linked files, special files,
protected credential paths, binary/document extraction and invalid UTF-8 are
blocked. Reads are capped at 1 MiB per source and 2,000 lines per call, with a
separate result-character budget. Large results report truncation; no completion
claim turns a partial read into proof of full-file inspection. No shell, remote
terminal, hosted OCR or arbitrary Python is invoked by this reader.

Every tool call writes a lease-fenced receipt before dispatch and finalizes its
redacted result and SHA-256 before the next model round. Duplicate tool IDs within
an attempt reuse a confirmed result only; an unresolved outcome is not replayed.
Cancellation, expiry or policy removal leaves an unfinished receipt `unknown`.
A successful inspection artifact must link actual successful read receipts, and
review checks those retained results alongside the artifact. Model-generated
claims cannot supply or replace tool receipts. An OS read is not a proof that
all conclusions drawn from it are correct.

Open a request inspector for its full audit, including earlier attempts and
failures. Snapshot polls carry at most 200 recent receipt previews; the exact
request read retrieves its complete bounded history. The full artifact inspector
shows the exact receipts linked to that artifact’s execution attempt. Restart
recovery retains both. These are authenticated current-profile reads and never
accept a client filesystem path.

## Propose and apply reviewed workspace edits

An edit works on an organization-owned **logical workspace** whose immutable file
revisions and current heads live in SQLite. The original configured project files
remain unchanged. This gives the application a real revision comparison and an
atomic content-plus-receipt commit without pretending an ordinary live folder has
compare-and-swap semantics. The artifact inspector can download the exact reviewed
file bytes or the unified diff; those exports are copies of a named revision.

Example configuration, after the owner reviews the source folder and intended
managed-workspace permission:

```yaml
organization:
  capabilities: [work.draft, work.analyze, work.inspect, work.edit]
  tool_grants: [read_file, patch]
  read_roots: [/absolute/path/to/approved-project]
  roster:
    - id: document-editor
      name: Document editor
      team: engineering
      capabilities: [work.inspect, work.edit]
      tool_grants: [read_file, patch]
```

`patch` is a separate explicit permission to update the managed workspace. It is
never exposed as a model tool, never inherited by synthesized legacy staff, and
never means permission to overwrite a source project file. With only `read_file`,
work.edit can still prepare and review a proposal; request.apply remains pending
until the current organization and explicit author roster both have the patch
grant. A reviewer’s approval does not grant permission.

A concrete objective:

> In engineering, edit root0/README.md to clarify the existing setup sentence.
> Replace “Run the app.” with “Run the app, then open Home to submit an objective.”
> Preserve all other file contents and line endings. Prepare the reviewed managed
> workspace output and leave original-project merging for me.

The model reads through the canonical read_file tool. For editing, the backend
captures complete raw UTF-8 source bytes once, with an exact hash and managed
revision number; later reads in that objective use its managed revision. BOM,
CRLF/LF, trailing spaces and final-newline presence are preserved. Source or
proposed content that requires secret redaction, contains disallowed control
bytes, is binary, or exceeds **32,768 UTF-8 bytes** is refused. No redacted text is
silently written back as a replacement.

One work.edit request proposes up to eight exact file operations. An existing-file
replacement requires a nonempty, unique oldText substring; newText may be empty to
remove that substring. A new file requires a successful read_file observation with
sourceExists=false, baseRevision=0 and the returned baseSha256, followed by an
explicit operation=create proposal with complete newText (and no oldText). An
existing empty file is not an absent source. Missing parent directories are allowed,
but symlink/non-directory traversal, protected credential/Git paths, overlapping
file/directory paths, binary content and invented absence are refused.

The backend checks a real completed read receipt for each exact target, source
presence, base hash and revision, and computes the new bytes and diff itself.
Absent revision-zero provenance is immutable and remains distinct from a created
empty file. Partial raw read pages are retained as partial observations; the
separate reviewer receives the complete pinned base, proposed content, operation
and generated diff. Each file retains the 32,768 UTF-8 byte limit; the whole proposal
is limited to 128 KiB. Deleting or moving whole files is not supported by this
boundary. Application creates only managed revisions; source files and parent
directories remain untouched. With explicit project-test/source-integration grants,
new files participate in the exact tested snapshot and new local Git branch. If a
create target appears in the working tree, index or pinned source commit, execution
or integration stops rather than replacing it.

The reviewer must identify the exact proposal ID and proposal hash when approving
or rejecting it. Rejected proposals create only bounded revised work, with no
application. Approved proposals queue request.apply, which checks the current
grant/routing generation, author authority, active lease, independent review and
unchanged managed base. Its new revision, head update, application receipt and
request completion commit in one SQLite transaction. Competing proposals against
the same base cannot both advance that head; a stale proposal needs a new read,
proposal and review. Repeated requests reuse the existing committed receipt.
Cancellation before the application transaction prevents the change; cancellation
after commit does not undo or conceal the retained application.

After a successful managed application, backend validation checks its exact
revision manifest and retains an immutable receipt. Without the separate source
integration grant, source-project delivery retains a visible request.merge
intervention until original bytes are verified.
An explicitly selected managed-artifact outcome does not require a source merge.
This distinguishes a
real editable output from an unperformed original-project change. No model,
review decision, retry or client-supplied path can silently merge into the shared
source folder. Download and inspect the output before merging it deliberately.
Unified diff paths use the root aliases; a root0-only patch can be checked in the
corresponding source root with strip level 2 (`git apply --check -p2`), after checking
for concurrent source changes. The manual verification path never invokes Git or
a shell. Source verification reads through the explicit no-follow boundary; it
never writes originals or accepts an owner's unverified success claim.

## Opt in to controlled project execution

This is a separate grant from inspection, managed edits and declarative syntax
validation. Extend the current profile's existing organization configuration only
after reviewing the exact files and intended source-branch permission:

```yaml
organization:
  capabilities: [work.inspect, work.edit]
  tool_grants: [read_file, patch, run_tests, integrate_source]
  read_roots: [/absolute/path/to/approved-project]
  max_project_runs: 4
  project_grants:
    - id: selected-unit-tests
      files: [root0/app.py, root0/test_app.py]
      execution:
        recipe: python_unittest
        root: root0
        test_directory: .
        pattern: test*.py
        timeout_seconds: 10
        cpu_seconds: 5
        memory_mb: 256
        scratch_mb: 16
        output_bytes: 16384
```

This is a configuration fragment, not a replacement roster. Every explicit worker
who authors this project's inspection or edit also needs `read_file`, `run_tests`
and, for automatic source delivery, `integrate_source` in its `tool_grants`.
Managed application still needs `patch`. Leaders remain tool-free. Omit
`integrate_source` at both levels to keep manual source delivery. Reload the
profile service after changing configuration. Neither models nor owner-response
forms can add these grants or infer a command/file set.

Each recipe names 1–64 exact alias paths in one read root; selected source and test
bytes are bounded to 512 KiB total. Include every reviewed changed file and every
file the suite needs. Missing imports do not trigger package installation or
additional file access. Only `python_unittest` is supported. The runner uses
system Python's standard library, one process, no shell, network, third-party
dependencies, subprocesses, threads or file creation. Only the precreated
`/scratch/work.dat` scratch file is writable. Timeout, CPU, memory, scratch and
captured-output limits are explicit. No command runs on the original working tree.

Required isolation currently exists only on Linux with modern bubblewrap, libseccomp, and an existing host policy that permits unprivileged user/network namespaces. Ubuntu 24.04 hosts with default AppArmor user-namespace restrictions can refuse setup; no security setting is changed or bypassed. Native verification uses Ubuntu 22.04 and the pinned official bubblewrap 0.12.0 build without setuid or file capabilities.
Unavailable namespaces or runtime prerequisites fail closed. macOS/Windows
execution remains Needs You, with no local host-command fallback. The desktop
shows configured grants and recipes separately from runtime availability.

The backend persists execution start, exact snapshot and grant before dispatch.
A nonempty successful run still requires a distinct review of its exact retained
bytes and receipt. The current snapshot, grants, source preimages and review are
rechecked before they can satisfy acceptance. Failed, timed-out, cancelled and
unconfirmed runs cannot be converted into passing evidence by model prose,
owner attestation, syntax validation or retry. Unknown runs require inspection
and an explicit bounded replan rather than automatic replay.

With the optional `integrate_source` grant, source delivery requires passing tests
and review, then creates a new local branch and exact Git objects without changing
HEAD, the index or working-tree files. This bounded backend does not run Git,
hooks, filters, signing programs or credential helpers. It supports an in-root
`.git` SHA-1 repository at `root0`; unsupported layouts, concurrent source changes
and changed reviewed preimages remain interventions. No merge, push, deployment
or arbitrary filesystem write is performed.

See [controlled tests and reviewed branches](organization-owner-loop.md#run-controlled-project-tests-and-deliver-a-reviewed-branch)
for the desktop receipt fields, retained evidence links, recovery semantics and
separation between test success, independent review, source delivery and final
acceptance.

## Developer verification and extension

Run offline behavior checks through the repository runner:

```bash
scripts/run_tests.sh tests/eidolon_cli/test_organization_store.py \
  tests/eidolon_cli/test_organization_executor.py \
  tests/eidolon_cli/test_organization_service.py \
  tests/eidolon_cli/test_organization_roster.py \
  tests/eidolon_cli/test_organization_tool_receipts.py \
  tests/eidolon_cli/test_organization_tool_executor.py \
  tests/eidolon_cli/test_organization_inspection_e2e.py \
  tests/eidolon_cli/test_organization_edit_executor.py \
  tests/eidolon_cli/test_organization_edit_e2e.py \
  tests/eidolon_cli/test_organization_edits.py \
  tests/eidolon_cli/test_organization_policy.py \
  tests/tools/test_organization_file_read.py \
  tests/tui_gateway/test_organization.py
cd apps/desktop
npm run test:ui -- src/app/eidolon
```

These tests use real SQLite connections, scheduler threads and registered RPC
handlers, replacing only the billable model boundary with deterministic fixtures.
The executor also runs the real AIAgent and provider resolver against a local HTTP
fixture, including streaming/nonstreaming cancellation that cannot abort the
provider. A full local-HTTP fixture also drives real manager, worker/tool and
review turns against SQLite and a temporary project file. The edit fixture covers both granted managed
application and reviewed proposals awaiting a grant; original source bytes stay
unchanged in both paths. Execution fences remain
occupied until the actual provider thread exits.
They do not establish live-provider quality or end-user installation success.

The ledger is intentionally distinct from Kanban: Kanban permits manual and
self-certified completion and default-assignee fallback. Reusing those transitions
would defeat organization review and team/type constraints. The existing AIAgent,
provider resolver, profile/secret context and authenticated gateway are reused.
No organization tool schema is added to normal chat sessions. Existing toolset
selection is schema filtering rather than an authorization boundary; a cwd anchors
paths but does not confine them. Inspection therefore adds an exact-name final
execution boundary and a context-local constrained backend for the existing
read_file handler, instead of enabling the broad file toolset or inheriting
noninteractive approval/YOLO defaults. Ordinary file-tool behavior is unchanged.

Adding an execution capability requires a real executor and explicit permission
policy, tests at the actual execution boundary, eligible roster routing, and
completion evidence/review semantics. Merely adding a type label or prompt is not
enough. Durable requests and unknown-type intervention are the extension path to
broader work; this is not a claim that arbitrary objectives are already solvable.

### Durable file ownership and parallel work

Managers can declare `writePaths` on each `work.edit` task, for example
`["root0/src/accounts.py", "root0/tests/test_accounts.py"]`. These are exact
configured-root file aliases, not glob patterns or additional permissions. The
entire set is reserved atomically before the worker starts. Edits outside the
set are rejected; ordinary reference reads may still use existing read grants.

Independent file sets and repositories can execute together within the existing
worker and in-flight limits. Conflicting sets wait without consuming attempts
or model-stage budget. Repository identity uses the canonical Git common
directory and repository-relative path, so aliases and linked worktrees cannot
claim the same logical file twice. Paths are conservatively case-folded and Unicode-normalized, including
on case-sensitive filesystems. Existing unscoped edit tasks require exclusive
edit ownership until a bounded replan supplies exact paths.

Ownership lasts through independent review, bounded revisions, managed
application and validation. Expired leases and uncertain outcomes retain it;
they still need the existing owner recovery decision. Completed, cancelled or
superseded tasks release ownership. A waiting task owns no partial set, and
request-cycle validation includes file-ownership waits. Queue aging adds one
priority level per waiting minute so newer high-priority work cannot indefinitely
starve an older ready request. Existing deadlines, call budgets and attempt
limits remain in force.

The work graph and persistent agents' assigned-work lists show coordination
separately from lifecycle status: declared paths, reservation state, dependency
or conflicting-ownership waits, and the blocking task. A reservation is not a
claim that a model is currently executing. Restarting the backend preserves the
same task, manager and agent identities and their retained evidence.

This coordinates the existing bounded organization executor. It does not grant
arbitrary writes, commands or providers, or widen project recipes. Source
integration still creates independently verified local Git branches through its
existing serialized controller and Git metadata locks; multi-root source
integration in one objective is not introduced by task-level parallelism.

## Keep settled organization history without filling the current workspace

On **Objectives**, open **Objective history** to search retained objective
names and submitted descriptions, switch between current and archived history,
and page through older records. Open any listed objective to inspect its exact
retained work, acceptance, artifacts and request audits, including objectives
outside the current workspace's latest-25 settled-history window.

**Archive history** is an explicit owner action for completed or cancelled work.
It removes the objective from ordinary workspace polling while retaining the
original objective, task and request IDs, dependency links, evidence bytes and
hashes, review and execution receipts, identity assignments, and agent context.
Nothing is moved to another database or deleted. The authenticated current-profile
boundary is the same one used for the organization's other owner controls.
There is no automatic archiving policy.

Archiving is refused while requests or tasks remain unfinished, an intervention
is unresolved, a tool/project outcome is unconfirmed, another objective has a
live dependency or child request, or a cancelled execution is still stopping in
this or another backend process. Cancelled work with unconfirmed execution stays
visible even when it is older than the normal settled-history window.

**Restore history** returns the same terminal objective to the current history
view. It does not resume execution, replenish budgets, change permissions or
reopen an accepted/cancelled objective. Archive and restore use a version check
plus immutable idempotency receipts: an old tab cannot overwrite a later change,
and retrying a lost response cannot duplicate or reverse the action.

Storage remains deliberately finite: at most 1,000 current objectives and 10,000
archived objectives, with at most 100 audited archive/restore transitions per
objective. Archiving settled work releases current-objective admission capacity;
it does not change the separate open-objective, task, stage, provider-call or
execution limits. Capacity exhaustion fails closed while retaining existing
records; it never silently evicts data. History reads return at most 50 entries
per page. Ordinary snapshots read current work rather than loading all archived
tasks, requests and receipt content into the UI.

These limits are local safety bounds, not a claim of unlimited disk retention.
Existing artifact download and request-audit inspectors remain available through
an exact history objective. Full-ledger backup/retention administration remains a
separate concern; this archive flow does not permanently purge old records.

## Revisit owner attention after being away

**Needs You → Attention inbox** lists the current profile's unresolved
interventions. The unread count identifies blockers the owner has not yet marked
seen. **Mark seen** records only that the displayed revision was inspected: the
request stays pending, remains in **Needs you**, and still requires its existing
answer, permission, retry, or resolution control. It never approves a proposal,
grants a tool, retries a request, or starts execution.

Seen state survives reconnects and backend restarts. A request becomes unread
again when it reenters pending intervention, its reason or meaningful payload
changes, or its owner-visible routing/contract changes. Repeated polling and
identical writes do not create new attention; JSON whitespace and object-key
ordering do not count as payload changes. A delayed mark-seen action must match
the exact observed revision and cannot acknowledge a newer blocker. Refresh the
list if that action reports the request changed.

Use **Unread** to find changed older blockers without paging through already-seen
ones. **All blockers** retains both seen and unread interventions. Resolution or
cancellation removes a request from attention automatically. Archive never hides
an unresolved intervention through the owner controls, and archived history is
excluded from current attention. Restoring settled history does not reopen work.
Attention is local owner metadata; no email, desktop notification, external
recipient, provider call, or new authority is introduced.

The authenticated organization RPC boundary exposes:

- `organization.snapshot` includes `attention: {items, unread, total, hasMore,
  nextCursor}`. Each item has `requestId`, `objectiveId`, positive integer
  `revision`, `seen`, `createdAt`, and `updatedAt`. `total` counts all current
  unarchived pending interventions; `unread` counts their unseen revisions.
  Pending request content carries `attentionRevision` from the same snapshot
  transaction. Clients must only mark a page item seen when its revision matches
  the displayed request's revision; otherwise refresh the full snapshot first.
- `organization.attention({limit?, before?, unreadOnly?, profile?})` reads at most
  100 entries per page (default 100). Pages use stable newest-request-first order;
  `before` is the prior page's `nextCursor`. Counts always cover the whole current
  profile, while `hasMore` and `nextCursor` describe the selected filter. A cursor
  remains valid when its request is resolved, seen, or archived. An unread filter
  makes new content on older requests discoverable independently of this order.
- `organization.markAttentionSeen({id, revision, profile?})` marks that exact
  current generation seen and returns a fresh snapshot. Repeating the same
  acknowledgement is safe. Missing, resolved, archived, or changed generations
  return a refresh-required invalid-parameters error. It cannot mark another
  profile's request or accept a client-supplied actor or request state.

Exact history-objective reads scope attention to that objective. The durable
metadata stores only one row per retained request, rather than an unbounded
notification stream. Initial migration makes existing pending requests unread;
for those requests the original creation time is the earliest known attention
time. Revision identity, rather than timestamps or a live event subscription,
controls acknowledgement and reopening.

Older backends without attention support continue to show the existing Requests
queue. They cannot offer durable mark-seen state until the backend is upgraded.

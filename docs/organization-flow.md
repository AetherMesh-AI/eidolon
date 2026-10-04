# Organization execution, inspection and reviewed workspace edits

The Organization workspace now submits durable work to the Python backend. It no
longer manufactures a team or task graph when an objective is entered. The
separate example preview remains explicitly fictional and does not contact a
provider.

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

The manager creates a task graph. Eligible employees claim requests by request
type, team and priority. Each deliverable is persisted before a separate reviewer
checks it. Dependencies unlock only after approval. Open **Requests**, **Tasks**,
**Activity**, and the artifact inspector to see real state and the full retained
text. A completed objective requires every planned task to have passed review and
every control request to have settled. Review is a model's assessment of supplied
context, not a guarantee that statements are true or an external action happened.

### Supported capabilities and visible limits

- `work.draft`: produce complete text from the context supplied by the owner.
- `work.analyze`: analyze that supplied context and retain the full analysis.
- `work.inspect`: inspect explicitly granted local project text with `read_file`,
  then retain an analysis and exact tool-result receipts. Disabled by default.
- `work.edit`: capture a known source file into an agent-owned workspace, read its
  exact revision, and propose one exact replacement. Disabled by default.
- `request.apply`: apply an independently approved proposal to the managed
  workspace only, with explicit organization and author `patch` grants.
- `request.merge`: visible intervention for merging reviewed workspace output
  into the original project. No source-project writer is enabled.
- `request.plan`: the manager proposes an acyclic graph with explicit dependency
  indexes and acceptance criteria.
- `request.review`: a distinct logical reviewer assesses the exact persisted
  artifact bytes, identified by evidence IDs and SHA-256 hashes.
- `request.hire`: the director activates entries from the configured roster within
  the worker limit. Its visible route demands preserve planned worker teams and
  request types; the request itself routes through the director’s control team.
  Without a roster, backward-compatible logical slots use this profile’s settings.
  An explicit empty roster has no fallback employees. Hiring does not create
  profiles, grant permissions, install software, or mean real-world employment.

Owner → Executive → Director → Manager → Employee is the authority hierarchy.
The executive and director routing roles are policy-controlled; their presence
is not evidence of a model call. The manager, worker and reviewer stages run as
separate bounded AIAgent turns. A new worker slot is available for actual claims,
not a simulated activity animation.

Browsing, writes to source-project files, sending, purchasing and code execution
are not enabled in this increment. File reads require the explicit inspection grant described below.
A request needing another tool or missing source material stays
**Pending intervention**. The manager must preserve the requested outcome rather
than pretend a text draft performed an external action. Valid unknown request
types/teams are preserved so the router can explain why no eligible agent exists.
There is no generic fallback that silently assigns an incompatible worker.

HTTP-based providers continue through the existing runtime resolver. Transports
that inherently expose external tools and cannot enforce the exact tool grant, such
as native app-server/ACP routes and search-enabled models, are blocked for this
organization execution. Other Eidolon chat/provider surfaces are unchanged. Existing
provider configuration, credentials and permission boundaries are not expanded.

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
- Retry attempts, task count, concurrent calls, logical employees, open objectives,
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
  max_workers: 2           # 1–8 active configured or legacy logical employees
  max_inflight: 2          # 1–4 occupied execution slots
  max_tasks: 12            # 1–24 tasks per manager plan
  max_open_objectives: 20  # 1–100
  max_attempts: 2          # 1–3 attempts per request
  max_revisions: 2         # 0–3 review-requested revisions per task
  lease_seconds: 45       # 15–300; live calls renew their leases
  timeout_seconds: 180    # 30–600 per stage
  team: general
  capabilities: [work.draft, work.analyze]
  tool_grants: []          # no tool is enabled implicitly
  read_roots: []           # up to 8 explicit local directories
  max_tool_calls: 8        # 1–20 per request attempt
  max_tool_result_chars: 12000 # 1000–20000 per retained tool result
```

Limits do not authorize external actions. Staff, tools or providers are never
silently installed or broadened to satisfy a request. Disabling a capability
removes it from eligible worker routing. A provider turn is also token/iteration
bounded; normal provider billing and limits still apply.

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
tool itself. The director activates matching configured entries. New entries are
available until hired; disabled or removed entries never become generic fallback
workers. Optional paired `provider` and `model` fields select an existing
same-profile provider route. The resolved identity must match; unavailable
providers become intervention instead of silently falling back. Staffing does
not create or connect separate profiles. Reviewers are independent logical turns,
scoped to each configured team, and have no tools.

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

One work.edit request proposes one exact, nonempty, unique oldText replacement in
one existing file. newText may be empty to remove that substring. The backend
checks a real completed read receipt for the target/base hash/revision and
computes the new bytes and diff itself. Partial raw read pages are retained as
partial observations; the separate reviewer receives the complete pinned base,
proposed content and generated diff. Creating, deleting or moving whole files is
not supported by this boundary.

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

After a successful managed application, the task reports the applied revision and
the objective retains a visible request.merge intervention. This distinguishes a
real editable output from an unperformed original-project change. No model,
review decision, retry or client-supplied path can silently merge into the shared
source folder. Download and inspect the output before merging it deliberately.
Unified diff paths use the root aliases; a root0-only patch can be checked in the
corresponding source root with strip level 2 (`git apply --check -p2`), after checking
for concurrent source changes. Product execution never invokes Git or a shell.

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

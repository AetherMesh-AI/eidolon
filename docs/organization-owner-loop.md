# Organization owner loop

Eidolon uses one durable organization ledger for new objectives. Requests, tasks,
reviewed evidence, final acceptance and owner resolutions are different records
with explicit links. A board, graph or attention badge is a view of those records,
not a second dispatcher or completion authority.

## Keep responsibility across objectives

Organization is a persistent Executive → Manager → Worker roster under the human
Owner. Identities survive idle periods, stopped execution and backend restarts.
Managers have scoped responsibilities and reuse eligible worker specialists across
assignments; cross-domain coordination still obeys exact team, capability,
reporting-line and tool-grant checks. Roster size is separate from execution
concurrency. Disabling or retiring staff preserves identity and history.

The Organization inspector exposes each identity’s own bounded memory, context
summary and recent request/task/evidence-linked history. These records are retained
in the profile ledger. They do not replace task evidence, grant permissions or imply
that a provider call is currently running. The legacy Director becomes a Manager
while keeping its `director` ID and historical references; legacy Employees become
Workers. Existing profile chats retain their separate canonical identity.

For configuration, memory bounds and a scoped roster example, see
[Persistent identities and reusable specialists](organization-flow.md#persistent-identities-and-reusable-specialists).

## Manage members without leaving the desktop

Organization → **Manage organization** edits the connected profile's persistent
roster. Create or select a member, choose its role and reporting leader, team,
purpose, scope and responsibilities, then select accepted request types and the
separate response/staffing authority. Provider and model overrides are a pair;
leave both empty to inherit the profile's configured provider/model. Worker tool
choices are restricted to tools already granted to this organization. Leaders
remain tool-free. A staffing manager's scope includes its own team and any explicit
managed teams; `*` deliberately includes every team.

The persistent member limit is distinct from concurrent execution slots. Disabling
assignments preserves the identity, memory and historical work. Configuration
saves use a generation check and idempotency receipt. A stale editor asks the owner
to review current settings instead of overwriting another change. A lost response
can be retried safely without duplicating the change.

For reorganization, add an explicit handoff, choose source and destination members
with the same role, and select exact open assignments and optionally bounded memory.
The runtime validates reporting lines, teams, capabilities and active execution
before applying the complete change atomically. Completed history stays with its
original identity. Recent organization changes expose the actor, linked request,
subject, before/after configuration and transfer receipt.

## Answer linked agent requests

A worker or leader can pause its assignment with a `request.question`,
`request.decision`, `request.hire` or `request.permission`. The parent request is
`waiting_response`; other independent work can continue. Each request retains the
requester, requested outcome, required authority, parent, dependencies and evidence
links. Authorized persistent peers answer questions and decisions only when both
the accepted request type and explicit authority match. Staffing additionally
requires `staff.manage` and a matching team scope. Permission always reaches the
human owner and records a scoped decision without changing credentials or grants.

Needs You shows unhandled requests with the backend's permitted answer, approve or
deny actions. Hiring displays the exact proposed member configurations and selected
assignment/memory transfers before approval. Approval applies that recorded proposal;
it does not accept arbitrary new tool access. Answers and decisions are retained
with responder identity and time. Once all linked requests are answered, the exact
requesting assignment resumes with those responses in context. Reloading the desktop
retains member identities, responses, work and the audit trail. Linked request trees
default to depth 4 and at most 4 children per stage, with at most 24 typed requests
per objective. Cycles and exhausted limits remain visible for human handling.
These bounded request controls do not add arbitrary project execution.

## Submit an outcome and its criteria

Home accepts the objective, supplied context, acceptance criteria, priority,
delivery mode and explicit required checks. On a persistent-identity runtime, choose
an existing active Executive and one of its active Managers as the objective’s
responsible leaders. Selection does not provision staff or expand permission. Choose managed-artifact delivery when
the intended result is a reviewed downloadable patch or document. Choose
source-project delivery when the result must also be verified in the original
project. A delivery choice never grants file or account permissions.

Required checks are structured declarations:

- `managed_validation`: the exact applied managed output needs passing retained
  validation evidence.
- `source_integration`: the original source must be verified against the final
  reviewed and validated managed output.
- `project_tests`: functional project tests must have actually executed. The
  current organization runtime has no authorized command runner. This check
  therefore remains pending, even when syntax checks pass or a model approves.

The manager may add requirements derived from the objective but cannot silently
remove declared checks. Natural-language intent and judgment still depend on the
model; explicit criteria and check declarations make the intended outcome easier
to inspect. A model review is not an external execution receipt.

## Resolve the cause of an intervention

Requests / Needs You shows the global organization queue with team, priority,
type and state filters. Its default attention view highlights requests that need
an owner decision. Each request exposes only actions currently allowed by the
backend:

- **Provide missing input** retains an answer and resumes that exact request
  within its existing attempt budget.
- **Amend scope** records a changed scope and starts a bounded planning round.
  Existing required checks are preserved by default. The owner can explicitly
  replace the checklist and acceptance criteria for the amended scope; removing
  a requirement is shown before submission and retained in a before/after audit.
  Models and ordinary replans cannot remove requirements. A project-tests
  requirement still blocks completion unless the owner explicitly changes it.
- **Request a replan** preserves the objective and supplies feedback for a new
  bounded round.
- **Retry configuration** rechecks the request after the owner fixes its provider,
  roster or explicit grants. It does not add authority.
- **Verify source handoff** checks original project bytes against the exact
  reviewed, applied and validated managed output. An owner's statement or a
  checked box cannot replace source verification.

Every resolution retains its action, text, evidence references and idempotency
digest. Retrying the same submission after a lost response cannot create another
effect. Reusing its key for changed input is rejected. Budget exhaustion remains
visible; owner input does not reset attempt, replan or total stage limits.

A live or still-unwinding call must exit before a resolution changes its
objective. This also applies across backend processes. No resolution implicitly
changes credentials, source roots, tools, profiles or execution permissions.
There is no generic Mark done action.

## Accept the whole objective

Task evidence is saved before independent review. Once current tasks and required
control requests settle, the manager creates one integrated deliverable. A
distinct executive acceptance turn receives the exact current task artifacts and
integrated result. It evaluates every current criterion, cites retained evidence
and reports conflicts.

Acceptance requires all criteria to be satisfied, no unresolved conflicts, and
the backend's explicit required-check gates. Rejection creates a bounded planning
round or an intervention when its budget is exhausted. A revised round retains
the previous work, reviews and decisions as history without letting old approvals
certify new output. Previously completed legacy objectives keep their historical
meaning and are not reopened simply by upgrading.

The role hierarchy does not require a model call for every organizational box.
Staff activation, lease ownership, permission checks and revision application are
deterministic. Planning, production, independent review, integration and executive
judgment use separate bounded model turns.

## Inspect and validate a real project artifact

The existing explicit `read_file` grant supports known local text paths. Optional
`list_files` and `search_files` grants add bounded source discovery and literal
search. Both the organization and eligible staff must explicitly grant each tool.
All three use the same configured roots, no-follow path checks, protected-path
rules, regular-file restrictions and receipt boundary. Windows inspection remains
unsupported until it has an equivalent safe implementation.

A project edit can group at most eight exact replacements against observed
managed revisions. Each file stays within 32,768 UTF-8 bytes; the project proposal
has a combined byte limit. A distinct reviewer sees the pinned base, proposed
bytes, diff and validation plan. With a separate explicit `patch` grant, the
backend applies the reviewed group atomically to the managed workspace.

Backend validation then checks the exact applied manifest. Supported declarative
checks include SHA-256 equality, text presence or absence, strict JSON parsing and
JSON pointer equality, and explicitly labeled Python syntax parsing. Receipts
identify exact revisions, hashes, checks and outcomes. These validators never
execute project code, launch a shell, use credentials or access the network.
Their success must not be reported as passing a project's test suite.

For source-project delivery, export and inspect the reviewed output, then apply
it deliberately through the owner's normal tools. The verification action reads
the original files anew and checks the current final manifest. Missing grants,
conflicts, changed source, invalid validation or unavailable hosts remain visible.
Eidolon does not write the original project at this boundary.

## Runtime lifetime and cost visibility

The shell observes organization attention while the owner visits chat or other
pages. Desktop quit warnings include queued/running organization work as well as
chat turns. Quitting the app's local runtime can interrupt live execution. Queued
work and evidence persist; queued work resumes when that runtime restarts and
unconfirmed calls require review before retry. A separately running remote
backend may continue. Closing a window is not a promise of always-on execution.

Per-objective usage reports admitted stages and provider-reported token counts.
Unreported or interrupted token usage stays unknown. `max_stages` bounds admission
across the objective, `max_replans` bounds additional planning rounds,
`max_owner_resolutions` bounds owner interventions and `max_output_tokens` bounds
each model response. These are not a monetary spending cap.

## Existing work and history

Organization roles and capability routes remain distinct from execution profiles
and conversations. Profiles retain provider/credential isolation; canonical
profile chats and ordinary session history stay accessible. A profile-chat pane
has one plugin-controlled owner, preserving its identity and saved layout.

Workspace makes organization evidence and session-file artifacts discoverable in
one area while keeping their sources and review/application status clear. Existing
Knowledge links retain compatible navigation. Artifact views remain evidence browsers. Per-agent bounded memory and identity
history live in the Organization inspector, separate from session-file artifacts.

Old prototype records remain read/export-only. They are never automatically
submitted as live work. Static examples remain available for illustration.

Legacy Kanban remains clearly identified with its existing tasks, dispatcher,
dependencies and artifacts. There is no automatic migration or deletion of active
work. New organization objectives use the organization ledger. Any later
retirement needs a tested migration, rollback and history-access path. Kanban's
fallback assignment and manual completion semantics must not weaken organization
type/team eligibility or evidence-bound completion.

Providers, model identities, established runtime transports, contributor credit
and legal attribution remain intact. This increment does not add VM/OS selection,
remote web deployment, a P2P dependency or agent-pet infrastructure.

## Keep completion within an explicit budget

Every objective captures durable call, token, deadline and optional configured USD
ceilings. Retries, linked answers, scope amendments, replans and backend restarts
continue the same budget. A stopped runtime rejects new owner mutations before
writing their idempotency receipts; reconnecting can safely submit the same key.
A cancelled, expired or superseded lease can never publish a late completion.

The usage panel distinguishes observed provider token totals from conservative
admission reservations. Before a provider dispatch, the runtime reserves its whole
input/output allowance and all bounded transport attempts. Unreported usage,
interruption and unused fallback attempts do not refund that reservation. This
can stop earlier than an invoice would suggest, but it cannot manufacture a zero
cost from missing usage. Old ledgers retain an explicit unknown-history marker. An active objective with
prior unbudgeted model calls requires a separately budgeted objective before more
calls; earlier work is never silently treated as free. Never-executed queued work
can resume, and settled history keeps its original completed meaning.

Optional USD admission uses only owner-configured exact provider/model rates.
Eidolon does not fetch, guess or fabricate token prices. These rates must be
chosen to conservatively cover the intended route; the resulting number is a
configured admission ceiling, not a provider invoice or a promise about current
pricing, taxes, cache tiers or externally billed charges. Missing exact-route
rates under a USD ceiling block dispatch. Changing providers does not bypass the
original objective ceiling. No billing account or credential is created.

The objective deadline includes time waiting for owner answers. Execution receives
only the remaining time, and expired work cannot commit a result. Exhausted
budgets show an intervention with no ineffective retry controls. A separately
submitted revised objective is needed to authorize another budget.

## Inspect exact evidence without overfilling the model

Artifact, dependency and edit-file bodies are deduplicated by SHA-256. Exact
identity references remain attached to every source; the full originals remain
available in the evidence inspector. The actual configured model window, output
reserve and protocol headroom determine each input allowance. The conservative
text bound uses UTF-8 bytes instead of assuming that every four characters are
one token. Submitted wire inputs are checked again before dispatch.

When all exact evidence will not fit together, evidence-only stages use bounded
hierarchical reads. Each read retains its original source hash, exact contiguous
UTF-8 range, chunk hash, submitted input hash and explicit findings/conflicts.
The backend verifies complete coverage with no omitted bytes, gaps or overlaps.
The final synthesis labels these findings as model summaries rather than original
proof. Final acceptance compares every original range with the complete integrated
candidate. Any negative source review or unresolved conflict prevents approval;
partial coverage cannot establish completion. A maximum number of passes, the
same request deadline and the objective call/token/cost budgets still apply.
If the full candidate, required metadata or final findings cannot fit, the request
stops with an actionable scope/model intervention instead of silently dropping
material. Replanning alone is not presented as a cure for an unchanged oversized
proof set.

Open a request and choose **Inspect model and evidence audit** to inspect retained
context modes, exact hashes/ranges, read findings and model reservations across
attempts. This is read-only and scoped to the connected profile.

Duplicate tasks in one plan, duplicate linked requests and repeated answered
requests are rejected without creating extra work. An unanswered question can
escalate to a different specialist team, but sending the same question back around
its ancestry is stopped as no-progress ping-pong. Existing dependency-cycle,
request-depth, revision, replan and stage limits remain enforced. A denial remains
binding context; repeated requests do not create permission.

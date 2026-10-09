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
For managers and executives, select objective leadership separately from task
assignments. This works before planning and after all tasks finish; delegated
tasks keep their own managers unless explicitly selected for transfer.
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
deny actions. The request inspector links to its parent assignment, including the
recorded task scope, even when the parent is outside the current queue filter.
Back preserves an unsubmitted response; Close or Escape closes the complete
inspector trail. Navigation alone does not answer a request or resume work.
If a linked question, decision, permission or staffing request cannot be resolved
within the current scope, the owner can amend scope or request a bounded replan.
This cancels the old request tree without inventing an answer or approval, retains
its history, and requires fresh work and independent acceptance. Existing execution
locks and objective, replan and owner-resolution limits still apply.
Hiring displays the exact proposed member configurations and selected
assignment/memory transfers before approval. Approval applies that recorded proposal;
it does not accept arbitrary new tool access. Answers and decisions are retained
with responder identity and time. Once all linked requests are answered, the exact
requesting assignment resumes with those responses in context. Reloading the desktop
retains member identities, responses, work and the audit trail. Linked request trees
default to depth 4 and at most 4 children per stage, with at most 24 typed requests
per objective. Cycles and exhausted limits remain visible for human handling.
These bounded request controls do not add arbitrary project execution.

Exact answered questions, decisions and permission requests also accompany later
work, independent task review, integration and final acceptance for that same
objective. The organization retains the question, answer, requester, responder,
time, originating assignment and planning round; a planner does not need to copy
the answer into every task description. Answers from earlier rounds or cancelled
assignments are explicitly historical. They remain available for context but do
not override the current amended scope, acceptance checklist or later owner input.
Conflicting answers require clarification rather than silently selecting one.
Neither an answer nor a permission approval expands tool or credential grants.

Question and answer bodies use the existing lossless, hash-deduplicated context
projection. Oversized tool-free stages use mandatory audited reads of every exact
source range, including clarification text. Tool-using stages must fit the complete
context or stop visibly for a larger configured context or narrower scope. No
clarification is silently shortened to make a prompt fit. These reads establish
what context the execution received, not whether a real model understood or
faithfully applied the owner's intent.


## Submit an outcome and its criteria

Home accepts the objective, supplied context, acceptance criteria, priority,
delivery mode and explicit required checks. On a persistent-identity runtime, choose
an existing active Executive and one of its active Managers as the objective’s
responsible leaders. Selection does not provision staff or expand permission. Choose managed-artifact delivery when
the intended result is a reviewed downloadable patch or document. Choose
source-project delivery when the result needs a verified source handoff or an
explicitly authorized new Git branch. A delivery choice never grants file or account permissions.

Required checks are structured declarations:

- `managed_validation`: the exact applied managed output needs passing retained
  validation evidence.
- `source_integration`: requires exact source-delivery evidence. The default
  path verifies the owner's external handoff. An explicit `integrate_source`
  grant instead enables a new reviewed Git branch after isolated tests and an
  independent review of the exact tested snapshot. Reviewed inspection-only
  snapshots can satisfy this check without an edit when every selected project
  has its own verified current-round test, review and source-branch receipts and
  the required grants remain current. Source-project delivery for selected
  repositories requires the same proof; an explicit `managed_validation` check
  still requires applied managed edits.
- `project_tests`: requires an actual successful, nonempty isolated test run over
  the selected snapshot and a distinct review of its exact retained evidence.
  The supported recipe is Python stdlib unittest on Linux with the required OS
  isolation. Missing grants, unsupported hosts, failed or unconfirmed runs remain
  pending even when syntax checks pass or a model approves.

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
  requirement blocks completion until its execution and review gates pass, or the
  owner explicitly changes the requirement.
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

Owner replans, scope amendments and automatic review rejections supply the new
tool-free planner with exact retained project-run and source-receipt artifacts,
linked to their project, run and original round. Earlier attempts remain available
even when another replan happens before a new execution. Interrupted starts are
explicitly unknown and have no invented result. This is historical planning
context: the new round still requires its own current execution and review proof.

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

Without an explicit source-integration grant, source-project delivery keeps the
existing manual path: export and inspect the reviewed output, then apply it
through the owner's normal tools. The verification action reads the original
files anew and checks the current final manifest. Missing grants, conflicts,
changed source, invalid validation or unavailable hosts remain visible. This
manual verification never writes the original project or counts as a test run.

## Run controlled project tests and deliver a reviewed branch

The owner can separately configure `run_tests` and `integrate_source` grants and
an exact `project_grants` recipe in the current profile. Every project author
needs the matching explicit staff grants; a delivery choice, reviewer approval,
owner-response form or retry does not create them. See the
[configuration example](organization-flow.md#opt-in-to-controlled-project-execution).

The initial test runner is deliberately narrow: copied selected files, system
Python's stdlib unittest, one process, no third-party dependencies, shell,
network, subprocesses, threads or file creation. It permits one bounded writable
scratch file at `/scratch/work.dat`. Linux bubblewrap namespaces, read-only
source/runtime mounts and libseccomp are required. macOS and Windows project
execution is unavailable; it stays in Needs You without falling back to a host
command. A grant shown as configured is not proof that this host can establish
the required isolation. This does not enable arbitrary local-Mac execution.

The backend records `request.project_test`, then a separate `request.test_review`
for a distinct reviewer. A failed test or negative review produces a visible
`request.project_failed` intervention. Interrupted or unconfirmed execution stays
unknown and cannot satisfy required tests or silently replay. Configured per-run
resource limits, the objective deadline and the durable project-run limit bound
execution; model review and final acceptance retain their existing budgets.

Open an objective's **Latest project test execution** section for the latest run
in the current round. It shows the exact snapshot digest, selected file hashes
and revisions, reported test count, exit code, elapsed seconds, command arguments
and recorded isolation scope. **Read exact test evidence** opens the full retained
snapshot bytes, execution receipt, pinned source base and grant by evidence ID.
No terminal receipt means no successful execution is inferred. Syntax validation,
test-process success, independent test review and final acceptance are visibly
separate facts. Tests can be inadequate or self-report success; a passing process
is not independent proof of correctness.

With `integrate_source`, `request.source_integrate` rechecks the exact reviewed
and tested bytes, grant, source base and current preimages before delivery. It
writes a new Git branch and the necessary Git objects while leaving the original
HEAD, index and working tree unchanged. It does not merge, push, publish or deploy,
and does not invoke repository hooks, filters, credential helpers or shell
commands. The initial source integrator supports a bounded SHA-1 repository with
an in-root `.git` directory at `root0`; unsupported repository layouts and
conflicting source/index changes are refused.

**Reviewed source branch** shows the exact branch ref, source-base commit,
integration commit, tree and manifest digest. **Read exact source integration
evidence** opens its separately retained proof. Test success alone never implies
that source delivery occurred. Refreshing, reopening or restarting the backend
preserves these ledger records; final acceptance still requires the current
reviewed evidence and the objective's explicit checks.

## Runtime lifetime and cost visibility

The shell observes organization attention while the owner visits chat or other
pages. Desktop quit warnings include queued/running organization work as well as
chat turns. Quitting the app's local runtime can interrupt live execution. Queued
work and evidence persist; queued work resumes when that runtime restarts and
unconfirmed calls require review before retry. A separately running remote
backend may continue. Closing a window is not a promise of always-on execution.

An existing messaging gateway can also host the organization without the desktop.
This requires explicit `organization.gateway_enabled: true` in that profile's
`config.yaml` and an existing organization ledger. The default is off. This option
does not install/start an OS service, enable login startup, create credentials or
add execution grants. The gateway process must already be running on an awake
host; it is not cloud failover.

The gateway discovers existing ledgers every five seconds, including an objective
created after gateway launch. It serves the launch profile and only the named
profiles allowed by that gateway's multiplex configuration. Each profile must opt
in separately and retains its own credentials and budget. Changes to the gateway's
multiplex mode/allowlist require a gateway restart; newly created profiles already
covered by that configuration are discovered automatically.

Desktop and gateway schedulers share durable claims and process-level execution
fences. Active organization calls count toward gateway drain/restart waiting and
prevent idle suspension. Gateway drain pauses new admissions without spending
attempts; clearing an external drain permits queued work to resume. Stopping one
host never cancels another host's calls. Closing the desktop
may still interrupt a call it owns: the gateway continues remaining queued work,
but that interrupted call stays unconfirmed and needs review. Disabling the
profile's gateway option stops only its gateway-owned scheduler on the next
discovery pass. The same pass validates live organization settings and persists
grant or project changes for every scheduler sharing that profile's ledger.
Owner-managed roster and capacity overrides are retained. Revoked executions are
cancelled and fenced; their tool outcomes remain unconfirmed, and their execution
locks and capacity stay occupied until the calls actually exit. Restoring the
old grants never silently retries those calls.

Invalid configuration pauses admission durably for that profile, including an
existing desktop scheduler. Other profiles continue independently. Reading the
ledger or refreshing a stale desktop cannot clear the pause: validated profile
configuration must be restored. Queued work and evidence are retained, and late
results after revocation or shutdown admission closes are rejected.

Recovery failures back off to at most a sixty-second discovery delay without
blocking other profiles or messaging. Provider execution failures remain bounded
interventions; background discovery cannot replay an unknown call, refund its
reservation or reset its attempt/deadline budget. Needs You remains the place to
resolve these interventions. This option adds no automatic external notifications.

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

## Return to an outcome after time away

The Objectives page includes a separate **Outcomes** inbox. It is a bounded,
profile-local projection of the existing organization ledger, including archived
objectives. The Objectives navigation indicates unread outcomes. A completed
objective is labelled accepted only when the authoritative acceptance state says
so; cancelled and older legacy completions have distinct labels. Failed work and
requests needing input remain in **Needs You**, not disguised as completion.

Opening an outcome does not mark it seen. **Mark seen** acknowledges the exact
version shown, independently of acceptance, execution permissions or blocker
resolution. This acknowledgement survives a desktop or gateway restart and
archive/restore. A materially changed outcome becomes unread again; a stale
acknowledgement is rejected. Replanning or reopening removes the old terminal
projection until the ledger has a terminal result again. The inbox stores bounded
current receipt metadata per objective rather than a second deliverable or
acceptance history. The existing objective ledger remains the source of history.

Inspect the retained objective to read the integrated deliverable and its exact
acceptance evidence. For source-project work, the existing per-project sections
show the test execution ID and command, tested snapshot and file digests,
independent reviewer, and retained source branch, commit and tree when those
actually exist. A local branch receipt does not mean the branch was pushed,
merged or released, and retained evidence is not a fresh revalidation of external
Git refs or files that changed later. Managed-artifact results and legacy completions do not gain
project-test or source-integration claims by appearing in the inbox. Cancellation
is a stopping decision, not proof that every in-flight side effect was settled;
retained execution receipts and archive blockers remain inspectable.

This is an in-app inbox only. It sends no external messages, desktop notifications
or new OS permission requests. Deterministic loopback-model tests exercise the
persistence and delivery mechanics, not live-model judgment quality.

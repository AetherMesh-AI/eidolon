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

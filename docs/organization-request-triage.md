# Plain-language organization intake

## Goal and current boundary

The owner should submit an outcome in ordinary language. The organization identifies
the responsible existing project and manager, assesses importance against current
work, investigates available context, and asks only questions whose answers are
indispensable to proceed. Folder-based projects and multi-repository projects are
equally valid. A new request does not create filesystem access or execution grants.

The current backend slice provides a tool-free proposal contract, organization-local
context, durable intake ledger, single-send inference reservation, authenticated
submit/read/answer RPCs, clarification continuation, and transactional objective
admission. A supported configured chat-completions provider is required. Existing
manual submission remains available. The desktop intake form exposes inferred routing,
priority and criteria, with a read-only recent history for restoring questions after
restart. Explicit answer submission creates one separately budgeted continuation.
UI client tests cover profile changes, reconnects and uncertain idempotent retries;
a native installed-app visual check remains separate.

Inference is limited to 20 calls and 800,000 reserved tokens per profile (or stricter
organization settings), with exact configured prices required under a dollar cap.
Every clarification consumes its own allowance; reconnect never dispatches again.
Installation enrollment and shared pacing apply to intake as ordinary work. Unknown
charges remain reserved and block unsafe allowance/policy changes. There is no
silent renewal after exhaustion.

A read can reconcile an abandoned pending record after acquiring its free execution
lock, marking it failed or uncertain; it never replays inference. Responses with
stale context, duplicate candidates, dependencies or missing execution recipes
require review. A proposal classified as a project change is never downgraded to a report during admission.
The inferred delivery classification itself still requires semantic evaluation.

This slice does not yet inspect project files/logs, verify current outage evidence,
resolve duplicate/dependency proposals automatically, or replace global priority
aging. Thus a reported startup failure can receive provisional P2, while inferred
P1 needs verified evidence that this context builder does not yet produce. Explicit
owner priority is preserved. Model-generated semantic judgments remain provisional;
fixture and boundary tests do not establish arbitrary-language reliability.

## Existing foundations to retain

At main 613a47dc, organization_store.create_objective already persists idempotent
objectives with explicit project scope, acceptance criteria and leadership.
organization_packages supports executive decomposition into manager packages with
criteria coverage and dependencies. organization_question_routing routes authorized
questions through existing leadership. organization_priority supports versioned
owner changes without changing running claims. claim_next respects dependencies,
permissions, pauses, budgets and capacity. None should be replaced by a second
independent scheduler or chat-only task list.

## Integration slices and gates

1. Durable intake and bounded inference. Persist original request, profile identity,
   snapshot revision, idempotency key, answers and lifecycle before any model send.
   Reserve the call against the actual profile/installation allowance; record exact
   provider/model and usage with a single physical-send receipt. Unknown outcomes
   retain debit and are reconciled; reconnect never silently issues another call.
   Team generation illustrates a bounded single-send boundary but is not itself an
   intake allowance. No free auxiliary calls and no charge to another objective.
2. Context and validated proposal. Supply enabled leadership/responsibilities,
   already-authorized project routes, relevant open work, deadlines/owner overrides,
   and provenance-labeled observations. Context builder, not model, determines
   eligible routes and which evidence proves a current blocker for a route. Names
   alone never create ownership or access. Record snapshot digest; reject stale
   proposal on policy/roster/project/backlog change before admission. The parser
   resolves manager/executive/project IDs from that exact trusted route.
3. Admission and clarification. If scope and routing are unambiguous and allowed,
   transactionally link intake to exactly one objective. Revalidate ordinary
   create_objective rules and budgets. An indispensable question waits durably;
   unrelated work continues. Reuse existing answers, and collect missing evidence
   internally when possible. Provisional duplicate/dependency candidates cannot
   cancel, merge or modify existing work until separately validated. Preserve the
   original outcome and user edits rather than silently narrowing scope.
4. Visible rationale and controls. Show inferred route, priority, reason, assumptions,
   required question and proposed success criteria, with owner override. Default is
   a simple text input; advanced manual submission remains available. Keep six
   locales, interruption/reconnect, profile isolation and accessible controls.
5. Reviewed scheduling integration. Existing global aging adds one priority point
   per minute. That can eventually put old cosmetic work above a fresh blocker.
   Introduce bounded aging within classes and an explicit fair-service allowance
   for ordinary work. Existing owner priorities/deadlines take precedence. Yield
   only at a safe stage boundary; never interrupt a write, discard a claim, replay
   uncertain effects or exceed execution capacity/budgets. Review with recovery
   and goal-budget owners before touching shared claim logic.

## Behavioral acceptance matrix

- "Add emojis to Eidolon similar to Discord" routes to the configured Eidolon
  manager, proposes ordinary enhancement priority and reversible style assumptions.
  It asks only if reaction picker versus text rendering is genuinely indispensable
  and cannot be resolved from existing context.
- "Eidolon doesn't load in the termal at all" interprets the likely terminal typo,
  routes to the same project, and gives investigation higher provisional priority.
  It uses available logs/context before asking which command or installation.
- A verified current startup outage outranks routine emoji work. An unrelated
  project's outage evidence cannot promote this project's request to critical.
- "Write release notes about last month's crash" is not a current outage. A
  deadline-bound accessibility fix can outrank an ordinary enhancement regardless
  of wording. Test semantics with controlled model fixtures and later measured
  live evaluation, separately from parser tests.
- Two matching Eidolon installations produce one essential routing question; no
  invented project relation. An unknown folder yields missing-access status, never
  an access grant. An empty non-Git folder remains a valid configured route.
- Repeated identical message delivery returns the same intake and objective.
  Similar new phrasing proposes a duplicate candidate, never erases either request.
- Owner priority wins over inference. Stale context/owner changes reject admission
  or reprioritization until reconciled. No cross-profile references or leakage.
- Dependency candidates are cycle-checked against current graph and accepted
  outputs; unrelated work still runs in parallel where capacity permits.
- Reprioritization preserves live write/lease/receipt. Routine work eventually gets
  service without using unbounded aging to override verified active blockers.
- Provider failure/unknown send/reconnect/cancel cannot double-charge or silently
  retry; budgets include triage itself. No actual provider call in unit tests.

## Rollout evidence required

Contract tests alone do not establish completion. Require ledger/RPC integration,
real temporary-profile admission and interruption tests, scheduler fairness and
safe-boundary tests, native UI tests, independent review, exact-head CI and
post-merge checks. Installed-provider acceptance is a separate explicit step.

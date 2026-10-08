# Executive manager work packages

An executive can decompose one objective into durable manager-owned work packages.
These are delegation records inside the existing objective, not child objectives
or new permission grants. See [organization flow](organization-flow.md) for the
underlying request, review, project execution and acceptance controls.

## Planning and execution

A new objective whose selected executive accepts `request.decompose` starts with
that executive. Its tool-free response supplies `workPackages`, each containing:

- `title`, `description` and the exact `managerId`
- `criterionIndexes`: zero-based positions in the root acceptance criteria
- `projectIds`: a subset of the owner's selected repository identities
- `dependsOn`: indexes of earlier packages in this proposal
- `maxTasks`: the manager's task allowance

The runtime validates manager identity and reporting to the exact selected
executive, complete root-criterion and selected-project coverage, acyclic package
dependencies, and the total allocation against the objective's task ceiling.
It creates the complete package set transactionally, assigns durable IDs and
queues an exact `request.plan` for each responsible manager.

Manager plans may run independently. A package's actual work waits for its
prerequisite packages to complete. Every resulting task retains its
`workPackageId`; package membership does not replace explicit task dependencies,
write-path coordination, team ownership, tool grants or independent review.
The root stage, model-call, token, deadline, cost and replan limits remain shared.
A new package does not create a fresh objective budget.

The snapshot exposes `planningMode` and all `workPackages`, including earlier
rounds. Package records contain ownership, scope, durable prerequisite IDs,
planning-request identity, task IDs and backend-owned state: `planning`, `planned`,
`working`, `completed`, `blocked` or `cancelled`. Package completion is not root
acceptance. Integration, required-check gates and independent executive acceptance
still decide whether the objective has a reviewed final result.

## Recovery and handoffs

A replan retains the previous package/task records and creates a new round.
Each package retains its original criterion text alongside the root indexes;
historical meaning is not relabelled using amended current criteria. The desktop
falls back to numbered indexes for older records without the text snapshot.
Cancellation, restart and lease recovery retain the same request, package and task identities. They do not erase
reserved model usage or authorize replay of uncertain external effects.

A management transfer can explicitly select `workPackageIds` to move current
package ownership between managers in an open objective, including before any
tasks exist or while the objective awaits acceptance. The runtime validates the
final reporting line, planning capability, authority and in-flight-work fences.
Package handoff does not implicitly transfer task assignments, objective
leadership or memory; those require their own explicit selections. Retained
plans and evidence keep their provenance.

## Rollout and desktop behavior

Planning mode is persisted when an objective is admitted. Objectives present
before this migration remain `legacy` through restart and subsequent replans.
Existing explicit accept-only executive capability lists are respected. Newly
default-configured executives include decomposition; merely reopening an old
objective does not upgrade its workflow.

Before submission, the desktop identifies whether the selected executive uses
executive delegation or legacy manager planning. Setup reports the same distinction
per executive. Work and objective inspection display package ownership, scope,
prerequisites, task references and current/historical state. Legacy records are
labelled, and older runtimes without package fields remain usable.

Manage Organization provides explicit package-handoff checkboxes. Staffing
approval previews display the exact selected package IDs. Changing a transfer's
source clears the old selection; empty optional fields are omitted for older
runtime compatibility. The backend remains authoritative on whether a transfer
is permitted.

## Verification

Backend contracts cover decomposition, durable routing, shared limits, dependent
execution, recovery, migration and explicit package handoffs. Desktop contracts
cover ownership/provenance, historical scope, acceptance separation, management
field preservation and legacy admission guidance. Synthetic loopback Electron
fixtures include the executive stage and retain budget, cancellation and replan
assertions. Their providers are scripted; they do not establish model quality.

Use the commands in [organization regression CI](organization-ci.md). UI/unit and
type checks are not native Electron acceptance: native scenarios need a built app,
an Electron executable and the supported host/display setup. Report each stage's
actual result rather than treating fixture changes as proof that it passed.

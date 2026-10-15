# Owner goal controls

The organization panel exposes shared allowance review and finite continuation planning.
The owner explicitly confirms each allowance change, reviews a finite ordered plan,
approves that exact preview, and separately enables continuation. Approval stores a
disabled plan. Enabling can allow an already running organization to dispatch approved
work; the RPC itself does not start the service or launch an objective.

Allowance totals are durable reservations, including prior objective work. Configured
USD reservations are price-based limits, not actual provider billing. Unknown values
remain unknown. Calls, tokens, stages, project runs, deadline and completion reserve
remain subject to the existing installation, organization and objective ceilings.

Waiting reasons expose pending exact milestone/template prerequisites and workspace
identity changes. A waiting dependency does not prevent explicit enablement. The plan
retains its original finite scope, deadline and launch history. Changed authority or
workspace identity blocks continuation. A separate owner preview can refresh budget/policy authorization only when the
original finite scope, deadline, workspace bindings, dependency revisions and all
launched objective scope fingerprints remain unchanged. Approval appends an
authorization revision and disables continuation; enabling is a separate action.
Changed scope or workspace identity cannot be refreshed through this flow.
The immutable plan, launch history, membership and lifetime spending remain intact.

An uncertain write keeps its exact arguments and idempotency key. Same-owner receipt
reconciliation can rebind a new connection epoch. Different owner/profile/organization
contexts cannot retry that intent. Without a pending write, explicitly loading a new
scope clears the previous draft. Passive reads and previews do not provision services.

Tests use synthetic ledger state and mocked transports. No paid provider calls, real
owner enablement, or installed desktop acceptance are established by those tests.

Local validation must use scripts/run_tests.sh with explicitly named synthetic
organization test files. Its per-file isolation and clean environment are required.
Do not run broad provider/authentication directories to validate these controls.
Broad repository validation belongs to the required hosted regression gates.

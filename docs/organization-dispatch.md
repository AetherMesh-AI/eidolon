# Per-objective owner pause/resume

Pause closes a durable dispatch gate for one objective. SQLite serializes a pause
against claims: a claim committed first is already in flight; a pause committed
first prevents that claim. Other objectives keep their normal capacity and routing.
A database trigger also rejects transitions into running while the gate is closed.

The safe boundary is an already-claimed request, not a provider call or tool write.
Its existing lease, heartbeat, provider/tool calls, result, and failure handling
continue under the existing authority and budget checks. Pause does not cancel
providers, undo writes, or guarantee an immediate quiet objective. The UI keeps
its ordinary status and request list and explicitly reports the paused gate plus
already-claimed stage count. A final acceptance already in flight may complete.

Children, replies and retries can enqueue normally while paused, but cannot be
claimed. Lease recovery and restarts retain the gate. Unresolved questions remain
visible. Pause does not mutate identities, context, tasks, requests, reservations,
queue age, admission keys, evidence, budgets or the original deadline.

Resume checks the current policy, deadline, call/token/cost and stage allowances,
project bindings, and authority/attempt limits for dependency-ready queued work
under the same write lock. Unmet dependencies stay waiting. Every actual claim
rechecks dependency, coordination, permission, project, capacity and budget gates;
resume does not create grants, resolve interventions, or reset attempts. Running
work can consume the remaining allowance before the next claim, which will then
remain blocked. The action itself does not start a provider; the ordinary service
and snapshot polling apply the reopened gate.

Authenticated profile-scoped actions use an expected dispatch revision and an
idempotency key. An immutable receipt and event record each successful transition.
There are at most 100 alternating transitions (the last is necessarily a resume).
A denied action leaves the gate and revision intact. Terminal/archived objectives
reject new actions, but exact receipt recovery survives those transitions and
active-list omission. A duplicate old receipt never reapplies its old state.
The adapter validates exact-objective provenance and both profile fields. The UI
retains uncertain intent and offers exact retry or explicit review of current state;
connection, profile and navigation changes fence late responses.

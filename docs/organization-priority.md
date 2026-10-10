# Owner priority changes

An authenticated owner can change P1–P5 priority on an existing nonterminal,
unarchived objective. P1 is highest. The save includes the objective ID, expected
priority revision, and an idempotency key scoped to the profile ledger. Revisions
begin at zero; at most 100 changes are retained per objective. An unchanged value
is not another transition. Each successful change records old/new priority,
revision, owner identity, timestamp, and an immutable receipt plus activity event.

The SQLite write transaction changes the objective, unfinished task metadata,
and pending requests together. Running requests keep their priority, token, lease,
status and availability. New tasks/requests inherit the latest objective priority,
including children emitted by an old running claim. A running request that later
requeues adopts the latest priority. Finished/cancelled task and request records
are unchanged. Existing creation hashes/keys, identity, context, budgets, evidence,
acceptance, assignments, dependencies and queue timestamps are retained.

Selection remains priority plus elapsed queued minutes, then original creation time
and request ID, subject to all existing permission, dependency, ownership and
capacity gates. Saving does not start or interrupt a service, reset queue age,
grant access, expand allowance, or promise immediate execution.

Two simultaneous changes at the same revision cannot both succeed. An exact
retry returns the original receipt even after a newer change or completion;
it never reapplies the old priority. Reusing its key with different arguments is
rejected. Creation replay still compares the original admission input, including
its original priority, and returns the retained objective's current state.

The UI shows current and proposed values and the saved transition/revision.
After an uncertain reply, Retry uses the same key and arguments, even if the
objective subsequently completes, is archived, or reaches revision 100. This
recovers the original receipt without enabling a new change. Review current
priority discards that local intent and adopts the latest observed revision;
a later stale save still fails at the server. Profile/connection changes fence
callbacks. Older runtimes without priority revisions expose no writable fallback.

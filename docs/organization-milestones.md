# Persistent project milestones (backend foundation)

Milestones are bounded planning records spanning one or more explicitly registered
workspace projects. A milestone can link existing objectives and depend on other
milestones in the same organization. Each edit creates an immutable revision;
exact retries replay their receipt, and stale edits or dependency cycles fail.
There are at most 128 milestones, eight projects, 64 linked objectives and 32
prerequisites per milestone in this first backend slice.

The store's `revise_milestone` operation never creates objectives or requests,
starts scheduling, calls providers, expands permissions, or allocates spending.
An objective must already be bound within the milestone's selected project scope.
Milestones do not create independent budget pools. Existing objective budgets and
organization dispatch restrictions continue to govern work; a shared project
spending ceiling and automatic continuation are not implemented by this layer.

`milestones()` reads a bounded SQLite snapshot and derives current status:
- planned: no objective links yet
- blocked: a prerequisite is incomplete
- in_progress: linked work has not all been accepted
- needs_attention: objective cancellation/review/changed scope, or a changed
  prerequisite revision requires review
- complete: every linked objective is currently accepted and every pinned
  prerequisite revision is complete

Completion is a projection of accepted objective evidence, not fresh filesystem
verification or a cached milestone flag. Links pin objective requirements and
project bindings, including the current typed contract identity, revision and hash.
Changed requirements invalidate the old link until an explicit
milestone revision reviews the new scope; changed dependency revisions likewise
invalidate downstream completion. Archived objectives retain historical links.
No free-form milestone title becomes an additional unverified acceptance claim.

The pure `milestone_projection(connection)` supports old ledgers with all three
milestone tables absent without migrations. Partial tables fail closed. Normal
Store initialization creates the optional tables additively. The Organization page exposes a bounded owner-only milestone panel. Load current
milestones, choose existing project-bound objectives and prerequisites, review,
and explicitly save. Existing milestone edits use expected revision checks. A
failed or uncertain save keeps its exact intent and idempotency key for retry;
changing the authenticated owner/socket makes the old panel ineligible. An explicit
receipt check can recover after reconnection to the same stable owner route,
profile, organization and owner identity. It never resends a write: a matching
receipt confirms the save; an absent receipt retains the original intent for an
explicit retry. Different owner routes cannot recover or replay that intent.

The served-profile RPC accepts no caller-selected profile or actor. Reads use a
read-only SQLite snapshot without constructing the service. Saving changes only
planning records and never calls service.start. Trusted automatic continuation
and a shared multi-objective spending ceiling remain future integrations.

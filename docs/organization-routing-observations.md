# Local routing observations

Run `python -m eidolon_cli.organization_model_routing_cli observations --limit 200`
for the current profile's existing ledger. This command opens SQLite read-only,
uses one committed snapshot, does not provision or migrate an organization, does
not load provider credentials, and never sends a model request. It returns at
most 1,000 recent routed reservations and explicitly reports truncation.

For newly executed assigned direct chat-completion routes, the transport records
provider-reported input/output counts when present, monotonic elapsed seconds,
and response/error status against the immutable physical-call reservation ID.
The SDK's existing zero-retry policy is unchanged. These measurements exclude
provider setup and queue time. Missing/malformed usage remains null. A returned
response does not prove valid output, correct work, or objective acceptance.
Observation persistence failure cannot trigger another send or replace an
already returned provider result. Its reservation remains retained; the report
shows unobserved measurement fields. A process crash can likewise leave a
reservation without an observation. Such a reservation is not proof of a send.

The report separates:

- Owner-configured reserved USD ceilings from actual billed cost (currently null).
- Per-physical-call provider usage from existing request-attempt usage totals.
- Request-stage completion from the objective's current acceptance state/round.
- Owner retry epoch and reserved-call count from physical SDK retries.
- Provider/model/reasoning/configuration fingerprints from observed quality.

Old ledgers without the optional measurement table remain readable. Observation
rows are immutable and bound to the original request/token/call identity. Late
transport completion may record facts after claim expiry; it grants no execution
authority and cannot refund budget, change routing, or authorize a retry.

## What this does not claim

This is a measurement foundation, not adaptive production optimization.
`shadowRanking` is null with an explicit insufficient-evidence reason. Fixed
assignments, eligible Auto routes, configured evidence/context requirements,
budgets, reserves, and fallback limits are unchanged. Objective acceptance is
reported as an association, never attributed as causal success of each model
call. Local HTTP fixtures validate transport accounting only; they are not real
model quality benchmarks. No live provider calls are used by these tests.

Before activating cost-per-success selection, collect comparable independently
verified outcomes across task difficulty/stage and configuration, account for
failed/retried work and censored/unknown results, and evaluate a shadow policy
against the unchanged authorized baseline. Token usage multiplied by a configured
ceiling is not a provider invoice; this report never labels it actual spending.

# Shared goal allowance accounting

This backend foundation groups existing objectives under an explicitly confirmed, revisioned goal allowance. It does not launch objectives, enable continuation, call providers, create grants, or expose a user-facing enable switch yet.

## Limits and accounting

The owner supplies finite model-call, reserved-token, stage, native-project-run, and deadline limits. An optional USD ceiling uses conservative configured-price reservations. These amounts are not provider billing. Unknown historical usage prevents further admission; an unknown price cannot satisfy a configured money ceiling. Optional completion reserves protect model calls, tokens, and configured money from ordinary work.

There is no second wallet or refundable balance. Read projections join immutable goal membership to the existing canonical model-call, stage-usage, and native-run-start records. Each physical reservation counts once. Existing installation, organization, objective, provider-route, policy, and workspace controls still apply. Goal checks run in the existing write transaction before each corresponding admission or reservation.

A goal may contain objectives spanning multiple registered projects. Membership is permanent and single-goal. Explicit revisions may pause or change ceilings but never reset historical usage. All previous members must remain in later revisions. Existing replacement lineages must be selected together; future replacements inherit membership atomically. Cancellation, archival, recovery, and replacement do not erase spent reservations. The records and exact retry receipts survive restart; passive legacy reads do not create tables, and partial goal storage is rejected.

## Verification and remaining work

Offline tests exercise concurrent reservations, competing goal assignment, historical spend, exact configured money, unknown cost, protected completion reserves, pause/deadline, restart, transactional rollback, replacement ancestry, crash recovery, cancellation, and archival. Fixtures do not represent paid provider usage.

Owner-facing preview/confirmation and bounded continuation remain subsequent work. Continuation must use finite, explicitly approved templates with preserved acceptance criteria and workspace bindings, current policy/grant checks, atomic launch receipts, and these cumulative limits. Implementing this feature does not authorize enabling it for a real organization or spending on provider calls.

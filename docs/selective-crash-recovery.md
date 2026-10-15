# Selective organization crash recovery

The scheduler journals each admitted request before dispatch. When a lease expires,
recovery keeps the request ID, accumulated attempts, stage usage, and financial
reservations. It never treats an absent model receipt as proof that no call occurred.

* A scheduler-journaled attempt that has not crossed dispatch can return to the
  queue within its original attempt limit. A changed policy or payload prevents
  this. Model, tool, or project-run admission records also prevent this recovery.
* An exact durable source-integration receipt can return to the queue for
  reconciliation only. Its request, tested run, receipt digest, grants, and source
  are checked again. A persistent reconcile-only marker prevents publication when
  the required receipt is missing or changed.
* Legacy claims, unknown provider sends, unconfirmed effects, and exhausted
  attempts require intervention. Completed requests are never dispatched again.

Queue admission still checks cancellation, dependencies, routing, project source,
write scope, deadlines, token/cost limits and policy generations. An old process
cannot renew or complete the superseded lease. Parallel coordinators serialize
recovery through the existing SQLite write transaction.

This is not a general automatic retry facility. Arbitrary tools, provider calls,
and filesystem operations are not classified as repeatable merely from their
names or apparently read-only intent. General durable result replay and explicit
repeatability contracts remain future work.

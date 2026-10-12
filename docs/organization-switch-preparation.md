# Organization switch outcomes and draft preservation: review preparation

This is preparation against PR92 head `2da498adb4b05f202213ec092142daf7f5cebef5`, not an enabled organization selector or an implemented outcome API. PR93's observation API/client is separately reviewed at `89b72ffe3bbf85639c9b66e78bef4d139aa0dea8` and is not wired into the mounted provider. Neither reviewed branch is changed by this preparation.

## Findings from the actual paths

`store/connections.ts:selectConnection` returns `Promise<void>` for several materially different results: unknown destination, duplicate pending request, already-active destination, superseded dial/activation/bookkeeping, and a committed destination. A timeout after publication is swallowed when the target is active. A rejected dial or failed activation is thrown only while its intent remains current. A caller cannot distinguish these outcomes from the resolved promise alone.

PR92 supplies the shared intent generation needed to recognize cross-door A → B → A, plus the independent reset ownership needed to repaint wiped lists even after a newer browse-only intent. These are separate authorities and must stay separate in an outcome API.

`app/contrib/organization-work-picker.tsx` already rechecks committed scope before opening Requests. This protects a changed destination, but final route equality alone cannot establish that its original click still owns navigation after A → B → A. Explicit supersession would let that caller decline navigation even if the requested route happens to be current again. This is a source-audit finding; a real-store-to-picker ABA integration reproduction remains required.

The existing stores already retain intake fields/submission keys, owner-chat text/send intent and renewal intent outside the mounted panes. Intake keys include the organization owner route; chat keys also include immutable member identity. `beginGatewaySwitch` invokes `startFreshSessionDraft({preserveRoute: true, workspaceTarget: null})` through the registered lifecycle, then wipes machine-bound session state. Neither operation clears those organization stores. Do not add a second copy/cache or new disk persistence simply to satisfy a presumed gap.

## Proposed result contract for review

Introduce an internal result-returning operation beneath the existing Sessions-facing API. Preserve the current wrapper's error behavior while migrating callers deliberately. Every outcome reports the requested canonical route and the observed authenticated route at settlement (or null if no route can be verified). The observed route is a fact at settlement, not a promise that later navigation still owns the foreground.

| Outcome | Meaning | Caller behavior |
| --- | --- | --- |
| `committed` | This intent published its target and still owns the foreground at settlement. | Show the authenticated committed route; never roll back after publication. Surface a recoverable post-publication timeout/refresh error separately. |
| `unchanged` | This request did not publish a different route and was not superseded. | Distinguish already-active from invalid target, dial failure and pre-publication activation failure. A failed reset may still have required repainting the old source. |
| `superseded` | A newer shared intent owns the foreground, regardless of route equality. | Do not navigate, toast an obsolete failure, repaint, or publish a requested destination label. |

Use a discriminated result with an explicit reason and optional recoverable error phase (`dial`, `activation`, `bookkeeping`, `refresh`). Include whether this request performed the reset and whether it published, so a superseded request that briefly published cannot be described as never having changed state. Keep raw transport errors out of labels and sanitize user-facing errors through the established path.

A duplicate request must join the pending result or return an explicit pending/superseded reason; it must not be reported as a completed switch merely because duplicate suppression returned early. Re-evaluate shared intent after every asynchronous boundary, including remember/refresh. A timeout after publication produces a committed route with recovery information when still current, rather than a failed dial or rollback. Reset recovery remains governed by the reset token, even when the result is superseded.

Do not broaden the API into a selector, registry discovery, profile creation, chat creation, sending or allowance renewal. Do not use it as permission to reconnect while listing destinations.

## Executable evidence in this preparation

`app/session/hooks/organization-switch-drafts.test.tsx` uses the real `useSessionActions` reset, real `beginGatewaySwitch` barrier/wipe and real draft stores:

- It proves the reset ran by checking session cleanup and the barrier. Intake, uncertain send and uncertain renewal values remain unchanged both on success and when a later lifecycle cleanup throws.
- It uses separate routes with copied member/thread identities and identical per-ledger idempotency keys. Late acknowledgements for route A resolve only A's intent and leave B's values intact after reset.
- It asserts no navigation and no calls through the supplied backend request function.

These are passing baseline invariants, not red-to-green product fixes. The fixture registers the same session-reset callback used by production, but does not mount the entire shell, project-tree cleanup, terminals, an authenticated socket, or the future selector. It does not prove native switching, that all transports avoid requests, or that all selection-triggered observation is non-starting.

## Required next regression matrix

1. Real store outcomes: unknown target, already-active route, duplicate pending request, dial failure with no reset, failed activation after reset, non-landing activation, and timeout before versus after publication.
2. Cross-door overlap: A → B → C and A → B → A during dial, serialized activation, remember and refresh. Assert result authority, current descriptor, reset recovery and navigation ownership separately.
3. Actual organization UI: intake editing and uncertain submission, owner-chat editing/uncertain send, and uncertain renewal survive A → B → A at the real reset boundary. Late admission/read/acknowledgement from A never mutates B; returning to A restores only A's intent. Route replacement invalidates mismatched ledger identity.
4. Mounted provider: every selection-caused read uses non-starting observation. A stopped scheduler with queued work never starts; independently authorized background work continues. Unsupported and mismatched shared-profile sockets remain unavailable without legacy fallback.
5. Hosted native: successful switch/return, pre-publication failure, post-publication timeout, rapid overlap, keyboard activation and draft recovery. Count session creation/send/renewal effects separately from existing background work.

## Dependencies and safe ordering

Preparation and isolated tests do not require merging either reviewed PR. Production outcome integration must retain PR92's shared intent and reset-recovery semantics; those fixes cannot safely be replaced by route equality or a connection-local counter. Once PR92's approval is resolved, verify the combined base before advancing implementation. PR93 is required before wiring non-starting reads, and its current client alone does not make the mounted provider safe.

Keep organization selection disabled until outcome semantics, source-owned draft UI evidence, authorized discovery and all resulting observation refreshes pass review and native verification. This document is a proposed contract, not a publication or merge approval.

## Local validation

189 tests passed in eight files: the three new reset/draft invariants, session actions, gateway reset, connection selection, cross-door intent, intake drafts, owner chat and organization shell. All three TypeScript configurations, focused ESLint and whitespace checks passed. No hosted CI or native UI verification was run for this test/document-only preparation; no production source behavior changed.

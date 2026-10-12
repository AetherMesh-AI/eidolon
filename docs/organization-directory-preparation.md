# Authorized organization directory and cache — review preparation

This runtime-wiring branch depends on unmerged PR95 head `d31a5527cbbe60e98b5eb5479c01c001e81a9ffd`, tree `14624f8db3843681873aba6f6179284b91e26d3b`, which itself depends on independently reviewed PR94 head `219dd58173f9d5c75fd8b6b0ed5294f10e696ba0`. Both heads and their evidence remain preserved. All main merges remain held, including the unresolved PR92 approval.

## Implemented behavior

The bounded display cache from PR95 now has a runtime source adapter observing the existing gateway, profile, connection, registry and observation stores. It performs no registry initialization, enumeration or dialing. Registry object changes advance an independent generation, so credential/query/auth edits revoke freshness even when their redacted endpoint is unchanged. Tests cover those edits with late old responses and verify secrets remain absent from cache snapshots.

The workspace header opens localized organization inspection. Opening, searching, highlighting, Enter and closing perform no requests or destination activation. Explicit Refresh reads only the eligible captured current descriptor. The cache is created lazily and disposed on header teardown; no directory data is written to disk. Search preserves duplicate routed identities and labels stale names. All six supported locales have copy. Destination activation is disabled and has no UI callback.

A separate, unconnected activation coordinator requires an explicit prepare/confirm action and has a closed default release gate. Tests with an injected switch exercise cancellation before admission, changed owners/endpoints, unchanged/superseded outcomes, committed results with trailing errors, destination verification failure, late verification and synthetic A→B→A return preserving original uncertain intake/send/renewal ownership and keys. It never navigates or rolls back a committed route. Cancellation after admission is deliberately unsupported because the reviewed switch API cannot undo publication; this remains a release-gate limitation, not a successful cancellation claim.

The prior same-route metadata subscription fix is retained. Focus tests now explicitly flush deferred dialog teardown, with a positive restoration control and a negative owner-loss control. Native fixture coverage exercises inspection without activation, explicit refresh, current draft DOM/route preservation, keyboard highlighting/cancellation/focus, and narrow/200% English/Arabic layouts. These cases do not prove native cross-destination return to uncertain drafts.

## Remaining activation gates

Activation remains disconnected until independent source review, dedicated/shared profile authority, native switching and failure before/after publication, uncertain intake/send/renewal return, and stopped-scheduler-with-queued-work proof pass. The current coordinator's injected switch tests and inherited real-reset draft tests are complementary evidence, not a combined native activation journey. Display records never grant write authority. No new RPC, migration, credentials or main merge is included. The original design contract below remains the boundary for further work.

## Bounded first delivery

Build a window-local directory of previously verified routed descriptors plus the currently authenticated served profile. The registry supplies possible connection labels, not permission to enumerate profiles. A directory may show an unknown connection as unavailable with its local label, but must not claim that it contains an organization, dial it for discovery, or call `profiles.list`.

The first delivery does not add backend-wide enumeration. Such enumeration requires a separately reviewed explicit grant with allowed profiles filtered before opening metadata. An open socket alone is insufficient authority to search unrelated profile paths. Inactive, unreachable and shared-profile destinations remain unavailable until verified through their own supported served-profile authority.

## Proposed entry and cache contract

A display entry carries registry connection ID, canonical profile, redacted endpoint identity, last verified organization ID/name/revision, observation generation, and a timestamp for the last successful descriptor read. The route key is connection ID plus canonical profile; endpoint identity is an invalidation condition. Organization UUIDs and names are labels/consistency checks, never deduplication or authorization keys. Copied ledgers with the same organization UUID remain separate destinations.

Retain only display descriptors in a bounded, window-local cache (proposed maximum 100 entries, least-recently-verified eviction). Do not persist owner names or metadata to disk in this slice. Do not copy execution snapshots, conversation contents, draft payloads, pending mutation intents or credentials into the directory. Reuse the existing owner-scoped draft stores and metadata intent store.

Separate last-known display data from active verified authority. On disconnect, changed endpoint, removed registry entry, profile mismatch, auth failure or socket-generation change, revoke freshness immediately. Retained names may appear only with an explicit stale/unavailable label; they cannot enable writes. A failed descriptor read never becomes unconfigured. Unconfigured requires a successful scoped response with absent metadata. Unsupported remains distinct from transport/storage failure.

A successful current descriptor read replaces the display record even if revision decreases after restoring a backup. Changed organization identity invalidates that route's editor and uncertain metadata reconciliation. Do not rotate copied IDs, migrate profiles, replay uncertain writes, or infer owner authority from equal IDs. Removed routes must be evicted; re-adding a route must verify it afresh.

## Read and interaction boundaries

Opening, closing, searching, filtering and keyboard highlight use the display cache only. An explicit refresh may call `organization.descriptor` only on the captured eligible current socket. No reconnect helper, profile enumeration, service initialization, snapshot, Save, send, renewal or conversation-open calls are allowed in discovery.

An explicit user activation is a separate action. It invokes the reviewed result-returning organization switch, retains the committed source label while pending, and verifies the new scope before displaying fresh destination content. A superseded result must not navigate or toast. An unchanged failure retains the old source and exposes its error. A committed result with a trailing error must not roll back. Shared-profile destinations stay disabled until the backend supplies an explicit supported scope.

The menu should search verified or clearly stale organization names together with connection/profile labels, retain duplicate names with secondary source text, and use existing localized controls. Search and highlight must never select. Focus returns to the trigger only when the originating owner and mounted trigger remain valid. Cached active labels cannot override the committed descriptor.

## Existing evidence versus future tests

The new preparation tests exercise actual metadata-client and owner-key behavior: copied ledger IDs on different routes do not transfer uncertain intents or cached authority; ineligible/foreign/unavailable reads cannot become a verified destination or trigger alternate requests. These are baseline contracts, not evidence that the proposed directory/cache exists.

Implementation tests must additionally prove bounded eviction, route removal/re-addition, endpoint edits, redacted cache serialization, no requests during menu/search/highlight, stale labels, fresh validation after return, out-of-order reads and A→B→A/C. Native tests must cover explicit activation and return with uncertain intake/send/renewal, failure before/after publication, keyboard/focus, narrow/200%/RTL layouts, and a stopped scheduler with queued work. Attribute side effects to selection independently from existing authorized background work.

## Source audit concerns to review before implementation

The same-route observation subscription gap is now fixed with a reproducing regression. Before activation wiring, reconcile dedicated transport profile names with logical UI aliases before accepting descriptor scope. These are review questions, not reasons to broaden discovery or change PR94 during independent review.

The original design-only preparation is superseded by the implemented work and remaining gates above. Inspection is wired; destination activation and all main merges remain held.

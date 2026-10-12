# Authorized organization directory and cache — review preparation

This proposal and its baseline tests are separate from PR94 head `97e61d3afd3cfca0863ce464bc6ba0c2ef3d94a7`. They do not enable a selector or alter production behavior. PR92/PR93/PR94 acceptance and native verification remain prerequisites.

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

The metadata hook currently derives eligibility through the runtime scope reader but memoizes/subscribes to gateway/profile/pending stores directly. PR94 adds observation epochs and an organization-switching atom to that scope reader. Review whether a same-route observation-generation change reliably causes a descriptor re-read; a cached memo must not leave naming controls unavailable or reuse stale authority. Also reconcile dedicated transport profile names with logical UI aliases before accepting descriptor scope. These are review questions, not reasons to broaden discovery or change PR94 during independent review.

No production patch, new RPC, cache store, migration, credentials, selector enablement or merge is included in this preparation.

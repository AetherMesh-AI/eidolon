# Organization identity and owner controls — proposed implementation contract

Status: proposal for independent review. This document does not describe shipped functionality.
Baseline: `e9e795b451cd32f7f4abfebd5a8621b6fba84863` (PR84, PR87 and PR88 merged; postmerge verification in progress when drafted).

## User outcome

An owner can name the persistent organization, set a separate human-owner display name, and select an existing organization without starting a chat or moving its history. The header displays the organization's saved name and a distinct owner control. Runtime connection/profile details remain inspectable, with clear source labels in the selector.

This is one organization per existing runtime profile for this milestone. It is not a new multi-organization database, account/login system, membership service, or provider restriction. Selection and naming never grant execution authority.

## Current constraints verified in source

- `eidolon_cli/organization_store.py` owns a profile-scoped ledger. `organization_identity.py` preserves agent identities, context and assignment history, but there is no organization-level name/identity record or separate human-owner presentation record.
- `organization_service.get_service()` can provision a missing ledger. Browsing an organization directory must not call it for inactive profiles merely to obtain names.
- `apps/desktop/src/app/eidolon/workspace-header.tsx` currently shows the runtime profile and connection switcher. `organization-owner.ts` derives a routing key from connection/profile/endpoint; that key is not a human identity.
- `store/profile.ts`'s `selectProfile` can call `requestFreshSession`. It is not the entry point for organization selection.
- `store/connections.ts` already provides staged dialing, scoped commit/reset, stale-switch rejection and `preserveRoute`. That option avoids the new-chat route but still resets machine-bound state. Its draft behavior must be tested, not assumed.
- Owner chat drafts and uncertain send/renewal intents are keyed by existing owner scope and immutable member identity. Renaming the organization must not change these keys.

## Backend record and authority

Use a standalone metadata-only reader/writer for small, separate tables in the existing `organization/state.db`, without constructing `OrganizationStore`, running its migrations, seeding its records or obtaining an organization service: a singleton organization ID, organization display name, a scoped human-owner display record, metadata revision and timestamps. Do not modify agent identity IDs, the historical `owner` actor, objective/task IDs, conversations, acceptance receipts or evidence hashes.

The returned record is `{ organizationId, displayName, owner: { ownerId, displayName }, revision, createdAt, updatedAt }`. Both IDs are stable ledger metadata, not account IDs or routing authorities. An initialized record starts at revision 1; an absent record is represented explicitly rather than by a fake ID. First Save requires the absent-record precondition, so two concurrent initializations cannot silently rename each other's organization. Names never enter an execution prompt or provider configuration solely because this metadata was edited.

Use an independent metadata revision. Display-name edits must not change execution policy generation, grants, budgets, provider selection, roster configuration or background opt-in. A scoped human-owner record is display metadata for the existing owner authority; it is not a verified account, authenticated-person claim, or replacement for transport authorization.

Read descriptors through a narrowly scoped read-only path that does not initialize a missing database, migrate inactive profiles, adopt policy, start services or create identities. A missing database, metadata table or row returns an explicit unconfigured descriptor. An unsupported schema or capability disables the editor; locked, corrupt or unreadable storage returns a visible error, never an unconfigured result. Do not silently invent a saved name from a profile name. The UI may show a clearly labeled fallback while offering setup.

An explicit owner Save initializes the metadata record if absent, or updates it using expected organization ID, expected metadata revision and an idempotency key. Commit the absence check or compare-and-swap, metadata update, metadata audit entry and idempotency receipt atomically. Look up an identical retry receipt before rejecting a fresh revision precondition; return that recorded result. Reusing an idempotency key with a different payload rejects. Concurrent/stale new intents fail visibly. Explicit metadata initialization may create only its own compatible tables; a metadata-only ledger must remain compatible with later normal service initialization. Validate bounded nonempty display text and reject control characters. Record an auditable metadata change without treating it as approval for work. Opening the owner control and canceling it are read-only.

Existing authenticated transport and strict profile routing remain the authority. By default a descriptor is scoped to the currently authenticated served profile. The renderer connection registry is not permission to enumerate profiles or read their owner metadata. Any intentionally backend-wide credential grant must be explicit in the backend authorization contract and tested; filter its allowed profiles before opening their metadata. Never aggregate another connection under the active connection's credentials. A caller cannot use an organization UUID or display name to address another profile. Never expose credentials or unredacted endpoint query strings in descriptors, cache keys or selector labels.

## Directory and selection

The local connection/profile registry supplies possible route labels, not backend authorization. Use a dedicated minimal authorized metadata descriptor path, never `profiles.list`: its canonical-chat summary path can unarchive recoverable chats. Read metadata only for the authenticated served profile or an explicitly granted backend-wide scope. Directory reads must not open chats, spawn inactive backends, initialize services or modify profiles. Do not dial an unreachable connection to populate a menu; cached entries remain clearly stale/unavailable. Show unconfigured and unavailable states distinctly; do not convert discovery failures into a new organization.

Use a route-qualified selector key (connection ID plus canonical profile), with the organization ID as a consistency check. Names are labels, not keys. Recovery backups retain the metadata ID with the ledger. Existing metadata-only profile export/import stays unchanged and must not silently transport an organization ledger or activate owner authority. Copied ledgers can share an ID; they remain distinct routed destinations and must not be deduplicated or treated as the same authority.

Generate IDs with a standard random UUID implementation and persist them transactionally only on explicit initialization. Never infer shared ownership from UUID equality. Do not silently rotate an ID after a copy or suspected collision. If a routed ledger is replaced and returns a different organization ID, invalidate its open metadata editor and pending intent rather than retargeting either. Fence descriptor responses and Save intents by authenticated connection/profile scope, switch generation, organization ID and metadata revision; a stale response may not overwrite the active descriptor. Idempotency receipts are scoped to that ledger and identity, never reused solely because another destination reports the same UUID.

The selector must use the established source-switch scope/reset barriers, preserving organization-page navigation intent, but `selectConnection`'s current `Promise<void>` and `preserveRoute` are not a reversible transaction. Add explicit committed-route, unchanged and superseded outcomes, including failures before publication versus timeouts after publication. Derive the active label from the committed authenticated route, never from a pending selection or resolution of a void promise. Do not call `selectProfile`, `newSessionInProfile`, profile creation/clone/import, or conversation-open/renew/send functions. Do not bypass the existing source reset barrier merely to avoid its side effects.

Before the actual machine-context reset, preserve source-owned organization intake, owner-chat draft and uncertain mutation intent. After publication, show only the committed destination's records; returning restores the original source's drafts. Do not create a blank chat or change historical chat ownership. A failed dial leaves the old organization active. Reset has no implicit rollback: never roll back an already-published destination when a later step times out. Report its committed route and recoverable error honestly. An overlapping newer selection supersedes the old result, including its failures. Reconcile an uncertain Save only against its original authenticated route and organization identity; do not replay it against the newly active destination.

Selection-driven observation must not call `organization.snapshot` or `get_service`: the existing snapshot path can initialize a service and start queued work, and the always-mounted runtime adapter refreshes across route changes. Provide non-provisioning, non-starting observation for selection and its resulting refreshes. Leave independently authorized background work running. Tests must attribute dispatch to the selection path rather than assert zero activity in a busy organization; with a stopped scheduler and queued work, selection must cause no startup or dispatch. Any change to existing recovery semantics needs explicit review and regression coverage.

Keep organization selection on organization pages in this first milestone. Existing session navigation and its own source ownership remain unchanged. Runtime profile/connection administration remains accessible separately; this selector does not rename filesystem profiles or change launch configuration.

## UI behavior

- Organization button: saved organization name, a searchable list by organization name, explicit source/profile secondary text, selected destination, loading/error state and a route-scoped setup action for unconfigured metadata. Duplicate names and UUIDs remain route-qualified. Keyboard highlight and search only navigate/filter the menu; switch only on explicit activation.
- Owner button: saved human-owner display name or an honest unconfigured label. Opens one scoped metadata panel with organization name and owner display name, Save and Cancel, and visible revision/conflict feedback.
- Rename: retain the active page, current conversation, focused form where possible and all execution state. Changing a label must not remount the organization runtime provider or reset drafts.
- Switching: show a pending destination while retaining the current authoritative label until activation commits. Disable stale metadata writes and close/re-scope open owner editors when source ownership changes.
- Compatibility: an older runtime without the descriptor capability keeps explicit profile/connection controls and an unavailable identity editor. Do not imply persistent naming succeeded or silently store it only in the renderer.

Reuse current controls, themes and localization patterns. The milestone is complete through its working persistence, selection and owner controls; artwork and decorative revisions are not part of it.

## Acceptance evidence required

1. Backend: explicit initialization, restart persistence, same-ledger backup/restore retention, separate profiles, concurrent CAS, idempotent retries, invalid input and strict transport/profile authorization. Directory reads against missing/older databases leave files and schemas unchanged.
2. Preservation: before/after equality of agent identity IDs, conversation/thread IDs, messages, work/evidence/acceptance records, grants, execution policy generation and finite allowances after metadata edits. No provider calls, scheduler startup or new queue entries caused by naming, browsing or selection, measured independently from previously authorized background activity.
3. Routing: successful selection, failed dial, failure before route publication, timeout after publication, rapid A→B→C selection, late read/write responses, remote/shared connections and duplicate labels/IDs on distinct routes. Assert explicit committed-route/unchanged/superseded outcomes, no rollback of a published destination and no switching on search/highlight. No cross-source record, draft or receipt reuse.
4. Interaction: real native clicks for opening/canceling/saving/reopening owner metadata, switching existing destinations and returning. Assert no session creation, chat opening, send, renewal or profile migration. Verify draft and uncertain-intent retention at the actual reset boundary.
5. UI: actual persisted names after relaunch; full native dark/light, narrow, 200% and RTL captures; keyboard/focus/overflow checks; unavailable and stale-revision states. Hosted scripted-provider evidence is not a live-model reliability claim.

## Delivery sequence

First implement standalone metadata/CAS and scoped RPC tests. Next add the authorized directory/cache, then persisted names and the scoped owner editor, then searchable selection with explicit route outcomes and native failure tests. Resolve non-starting observation before enabling selection. Keep these under one focused milestone with independent source review and final combined native verification. Do not declare the milestone complete after only adding labels or a metadata table.

Before implementation, independent review should settle the explicit-initialization contract and selector scope above. Cross-profile migration, account authentication, multiple organizations within one profile, invite/member permissions and active-session source switching remain separate work.

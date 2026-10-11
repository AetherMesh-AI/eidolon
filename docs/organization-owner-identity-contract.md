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

Add small, separate metadata storage in the existing `organization/state.db`: a singleton organization ID, organization display name, a scoped human-owner display record, metadata revision and timestamps. Do not modify agent identity IDs, the historical `owner` actor, objective/task IDs, conversations, acceptance receipts or evidence hashes.

The returned record is `{ organizationId, displayName, owner: { ownerId, displayName }, revision, createdAt, updatedAt }`. Both IDs are stable ledger metadata, not account IDs or routing authorities. An initialized record starts at revision 1; an absent record is represented explicitly rather than by a fake ID. First Save requires the absent-record precondition, so two concurrent initializations cannot silently rename each other's organization. Names never enter an execution prompt or provider configuration solely because this metadata was edited.

Use an independent metadata revision. Display-name edits must not change execution policy generation, grants, budgets, provider selection, roster configuration or background opt-in. A scoped human-owner record is display metadata for the existing owner authority; it is not a verified account, authenticated-person claim, or replacement for transport authorization.

Read descriptors through a narrowly scoped read-only path that does not initialize a missing database, migrate inactive profiles, adopt policy, start services or create identities. A missing record returns an explicit unconfigured descriptor. Do not silently invent a saved name from a profile name. The UI may show a clearly labeled fallback while offering setup.

An explicit owner Save initializes the metadata record if absent, or updates it using expected organization ID, expected metadata revision and an idempotency key. Concurrent/stale writes fail visibly; replaying the same intent returns its recorded result. Validate bounded nonempty display text and reject control characters. Record an auditable metadata change without treating it as approval for work. Opening the owner control and canceling it are read-only.

Existing authenticated transport and strict profile routing remain the authority. A caller cannot use an organization UUID or display name to address another profile. Never expose credentials or unredacted endpoint query strings in descriptors, cache keys or selector labels.

## Directory and selection

List only destinations already available through the existing connection/profile registry and its authorization. Directory reads may inspect existing metadata without opening chats, spawning inactive backends, initializing organization services or modifying profiles. Show unconfigured/unavailable entries honestly; do not convert discovery failures into a new organization.

Use a route-qualified selector key (connection ID plus canonical profile), with the organization ID as a consistency check. Names are labels, not keys. Recovery backups retain the metadata ID with the ledger. Existing metadata-only profile export/import stays unchanged and must not silently transport an organization ledger or activate owner authority. Copied ledgers can share an ID; they remain distinct routed destinations and must not be deduplicated or treated as the same authority.

Generate IDs with a standard random UUID implementation and persist them transactionally only on explicit initialization. Never infer shared ownership from UUID equality. Do not silently rotate an ID after a copy or suspected collision. If a routed ledger is replaced and returns a different organization ID, invalidate its open metadata editor and pending intent rather than retargeting either. Fence descriptor responses and Save intents by authenticated connection/profile scope, switch generation, organization ID and metadata revision; a stale response may not overwrite the active descriptor. Idempotency receipts are scoped to that ledger and identity, never reused solely because another destination reports the same UUID.

The selector must use the established staged source-switch mechanism, preserving organization-page navigation intent. Do not call `selectProfile`, `newSessionInProfile`, profile creation/clone/import, or conversation-open/renew/send functions. Do not bypass the existing source reset barrier merely to avoid its side effects.

Before committing a destination, preserve source-owned organization intake, owner-chat draft and uncertain mutation intent. After switching, show only the destination's records; returning restores the original source's drafts. Do not create a blank chat or change historical chat ownership. A failed dial leaves the old organization active. A commit failure and an overlapping newer selection must not publish stale labels, late results or mutations into the wrong source.

Keep organization selection on organization pages in this first milestone. Existing session navigation and its own source ownership remain unchanged. Runtime profile/connection administration remains accessible separately; this selector does not rename filesystem profiles or change launch configuration.

## UI behavior

- Organization button: saved organization name, explicit source/profile secondary text in its menu, selected destination, loading/error state and a route-scoped setup action for unconfigured metadata.
- Owner button: saved human-owner display name or an honest unconfigured label. Opens one scoped metadata panel with organization name and owner display name, Save and Cancel, and visible revision/conflict feedback.
- Rename: retain the active page, current conversation, focused form where possible and all execution state. Changing a label must not remount the organization runtime provider or reset drafts.
- Switching: show a pending destination while retaining the current authoritative label until activation commits. Disable stale metadata writes and close/re-scope open owner editors when source ownership changes.
- Compatibility: an older runtime without the descriptor capability keeps explicit profile/connection controls and an unavailable identity editor. Do not imply persistent naming succeeded or silently store it only in the renderer.

Reuse current controls, themes and localization patterns. The milestone is complete through its working persistence, selection and owner controls; artwork and decorative revisions are not part of it.

## Acceptance evidence required

1. Backend: explicit initialization, restart persistence, same-ledger backup/restore retention, separate profiles, concurrent CAS, idempotent retries, invalid input and strict transport/profile authorization. Directory reads against missing/older databases leave files and schemas unchanged.
2. Preservation: before/after equality of agent identity IDs, conversation/thread IDs, messages, work/evidence/acceptance records, grants, execution policy generation and finite allowances after metadata edits. Zero provider calls or new queue entries from naming, browsing and selection.
3. Routing: successful selection, failed dial, failed commit, rapid A→B→C selection, late read/write responses, remote/shared connections and duplicate labels/IDs on distinct routes. No cross-source record, draft or receipt reuse.
4. Interaction: real native clicks for opening/canceling/saving/reopening owner metadata, switching existing destinations and returning. Assert no session creation, chat opening, send, renewal or profile migration. Verify draft and uncertain-intent retention at the actual reset boundary.
5. UI: actual persisted names after relaunch; full native dark/light, narrow, 200% and RTL captures; keyboard/focus/overflow checks; unavailable and stale-revision states. Hosted scripted-provider evidence is not a live-model reliability claim.

## Delivery sequence

First land the backend metadata contract and its read/write invariants. Then connect the existing header to those records, add the scoped owner editor, and implement the organization-page selector through the existing switch lifecycle. Keep these under one focused milestone with independent source review and final combined native verification. Do not declare the milestone complete after only adding labels or a metadata table.

Before implementation, independent review should settle the explicit-initialization contract and selector scope above. Cross-profile migration, account authentication, multiple organizations within one profile, invite/member permissions and active-session source switching remain separate work.

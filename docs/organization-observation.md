# Non-starting organization observation

`organization.snapshot` acquires an organization service. Its existing startup and recovery semantics remain unchanged. Calling it to browse or select an organization can therefore initialize execution storage and start queued work. The always-mounted runtime adapter also refreshes after source changes.

`organization.observe {}` provides a separate, explicit read for the authenticated backend's launch profile. It never acquires a service, starts a scheduler, adopts policy, migrates a ledger, hydrates secret sources, creates identities, or calls a provider. An independently authorized scheduler may continue working while a read occurs.

## RPC contract

The caller must have a live transport. No parameters are accepted: profile, path, connection ID and organization UUID are not routing authority. The handler uses the immutable served home, replaces inherited home context, confines the database path to that home, and uses the served profile's configuration expansion scope. This endpoint cannot enumerate or read other profiles on a shared connection.

A successful result contains `capabilityVersion: 1`, `scope: {kind: "served-profile", profile}`, and one of:

- `state: "available"`, `snapshot`: the existing organization projection, including durable identities, requests, evidence references, conversations, management, attention and outcomes.
- `state: "unconfigured"`, `snapshot: null`, `reason: "database_missing"` or `"execution_not_initialized"`. A metadata-only ledger is not initialized.
- `state: "unsupported"`, `snapshot: null`, `reason: "schema"`. Required tables or columns are absent. The read does not migrate them.

Storage failures return error `5073` with a sanitized `data.reason`: `locked`, `corrupt`, `unreadable`, or `configuration_invalid`. They never become an empty organization. Invalid parameters return `-32602`; absent/dead transport or an out-of-scope database path returns `4001`. Older backends return normal method-not-found `-32601`.

The reader opens a WAL-aware SQLite URI with `mode=ro`, enables `query_only`, and projects within one read transaction. It checks required schema against the same DDL used for initialization, compiled only in memory. Missing policy or corrupt persisted configuration fails visibly. SQLite may update transient WAL read-lock bookkeeping; there are no row, schema or journal-mode writes. File configuration and database state are not one atomic resource. The projection compares current configuration to recorded policy without adopting it; subsequent mutations must still perform their normal policy validation.

## Client scope and rollout

`createOrganizationObservationClient` performs explicit, bounded, single-flight reads using only the captured transport. Its scope includes the authenticated socket generation, registry connection and profile. The gateway must emit every scope transition, including A → B → A, and mark disconnected, switching, or mismatched shared-profile sockets ineligible. Scope changes abort the old request, clear old records, and fence both late success and failure. No automatic polling, reconnection, legacy fallback, mutation or session operation is provided. Consumers must dispose the client when its owner ends.

This API and client are prerequisites, not a completed selector. The active runtime adapter still calls `organization.snapshot`; the organization selector remains disabled. Before enabling selection, route all selection-caused reads and mounted refreshes through non-starting observation, preserve the established reset barrier and source-owned drafts, and verify committed-route outcomes and real native switching. See [the owner identity contract](organization-owner-identity-contract.md).

Regression evidence uses temporary profiles, real SQLite ledgers and real RPC dispatch. Scripted executor tests distinguish stopped queued work from an already-running scheduler. They do not establish live-model reliability.

## Passive detail reads (PR94 review correction)

Selection can mount attention, outcomes, history, a retained owner conversation, project setup or an evidence inspector. Protecting only the snapshot poll is insufficient: the legacy attention/outcome/detail endpoints may acquire `get_service`, construct a store or refresh policy. Scheduler startup from attention alone was not established.

`organization.observeRead` accepts exactly `{method, params}` on the authenticated served-profile transport. Its fixed allowlist covers attention, outcomes, history/historyObjective, ownerChat.read, evidence, toolReceipts, executionAudit and previewReplacement through an explicitly read-only projection context. The ordinary store constructor/connector/writer are not used; each detail transaction opens SQLite in `mode=ro` with `query_only=ON`. Existing read-only projectSetup/projectDraft and checkSubmission functions are separately allowlisted. No arbitrary method dispatch, caller profile/path, service acquisition or execution mutation is accepted. Results use capabilityVersion/state/scope and `data`; unavailable schema/state is not an empty success.

All twelve passive adapter operations use this captured endpoint after a source intent, with scope cancellation and their existing result validators. Unsupported backends remain unavailable without reconnect or legacy fallback. Explicit mutations retain their existing authorization path. The mounted `/requests` test includes attention content and checks the actual default inbox's transport, rather than only loading `/objectives`.

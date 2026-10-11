# Organization display metadata RPCs

This backend slice implements the reviewed [identity contract](organization-owner-identity-contract.md). It does not enable a directory, owner editor, selector, profile migration, accounts or membership. Organization-name search and non-starting selection observation remain later slices.

`organization.descriptor` takes `{}` and returns `{capabilityVersion: 1, state, metadata, scope}`. Scope is `{kind: "served-profile", profile: "<served profile label>"}`. A client must pair that label with its authenticated connection identity; neither this label nor the organization UUID is a globally unique route or authority. Copied ledgers retain UUIDs and must remain distinct destinations.

Both new endpoints require the existing live authenticated WebSocket or local stdio transport and always use that backend's launch profile. They reject **all** `profile`, path, connection, owner-ID or organization-ID routing parameters, including an explicit launch-profile parameter. No backend-wide metadata grant is introduced. Shared-backend clients cannot obtain sibling metadata through these endpoints. `expectedOrganizationId` is a Save precondition, never a lookup key. An inherited profile ContextVar cannot redirect either endpoint.

Descriptor states:

- `unconfigured`, reason `database_missing`, `table_missing` or `row_missing`: explicit setup is available; reading creates no tables, database or organization service.
- `ready`: metadata is `{organizationId, displayName, owner: {ownerId, displayName}, revision, createdAt, updatedAt}`.
- `unsupported`, reason `schema_version`, `schema` or `incomplete_schema`: disable editing; never overwrite the unknown schema.
- Storage errors use RPC code `5072` with `data.reason` of `locked`, `corrupt` or `unreadable`. They are not reported as missing organizations. No local paths or credentials are returned.

`organization.saveMetadata` requires exactly `displayName`, `ownerDisplayName`, `expectedOrganizationId`, `expectedRevision`, and `idempotencyKey`. Initial Save requires both expected values to be null. Updates require the existing UUID and a positive integer revision. Display names are trimmed, nonempty, at most 160 characters and exclude control/format characters. Idempotency keys are nonempty text up to 200 characters. UUIDs are generated and persisted only by explicit initialization; the human-owner UUID is display metadata and does not replace the historical `owner` actor or confer authority.

A `BEGIN IMMEDIATE` transaction serializes initialization/update, metadata-only audit and the receipt. Identical retry payloads return the original receipt before checking an otherwise stale revision, including after a later rename. A changed payload with the same key rejects with `idempotency_conflict`; an identity or revision mismatch rejects with `identity_conflict` or `revision_conflict`. Absence with an update precondition rejects with `initialization_conflict`. Unsupported writes reject with `unsupported_schema`. These use code `5072`; malformed parameters use `-32602`, and missing/dead transport or an escaping ledger path uses `4001`.

The standalone module owns only `organization_metadata`, `organization_metadata_audit` and `organization_metadata_receipts`. It never initializes `OrganizationStore`, adopts execution policy, seeds agents, opens chat, starts a scheduler, grants budget or sends to a provider. The audit records metadata revisions, not work approvals. Normal later service initialization and existing SQLite recovery backups preserve these tables.

Reads use SQLite `mode=ro` and a consistent read transaction, preserving committed WAL visibility. They do not use `immutable=1`, which could hide committed WAL updates. No row/schema/journal-mode writes occur. SQLite may maintain transient read-lock bookkeeping in WAL sidecars; this is not a promise that shared-memory lock bytes remain unchanged while other connections are active. Tests check absence paths create nothing, closed-database reads preserve file contents/mtimes, and live WAL reads preserve main/WAL durable state while observing committed rather than uncommitted changes.

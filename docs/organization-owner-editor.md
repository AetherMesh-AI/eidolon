# Organization and owner names

The workspace header reads the active runtime's persisted organization name and
separate human-owner display name. The owner control opens a scoped editor with
explicit Save, Cancel, and Refresh actions. Names are display metadata; saving
them does not grant permissions, rename filesystem profiles, select a provider,
or send agent messages.

The editor uses only the already-open authenticated socket and the
`organization.descriptor` / `organization.saveMetadata` contract described in
`organization-metadata-rpc.md`. It does not use the reconnect helper or execution
adapter. Shared registry sockets remain ineligible until a separately authorized
served route is available. Older runtimes show unavailable metadata rather than
pretending that renderer-only names were saved.

Save captures the original route, organization ID, metadata revision, names, and
idempotency key. An uncertain result freezes that request. Its original payload
is retained in window-scoped session storage before transmission, survives a
renderer reload, and is retried only through **Check original Save**. Failure to
retain a new request prevents transmission. Neither reconnect nor reopening the
editor automatically retries a write. A missing or different organization blocks
replay, and an old receipt cannot replace a newer displayed revision. Corrupt
retained intent remains blocked rather than being silently discarded.

Pending connection changes close the editor and invalidate stale responses.
Returning to the original source restores its draft or uncertain request.
Confirmed conflicts require explicit refresh and review before a new Save.
Cancel sends no request. Closing the editor while a Save is pending does not
cancel the backend mutation or lose its uncertain intent.

This slice does not activate organization selection. The existing profile and
connection controls remain separate. Foundation copy uses the established
English fallback; complete localization and the broader selector contract remain
separate work. Session storage is window-scoped recovery state, not a new durable
organization identity store or a cross-device pending-write registry.

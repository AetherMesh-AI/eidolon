# Organization environment and credentials

The organization catalog is separate from profile environment variables and the host
process environment. It never imports existing values, changes existing desktop
secret-storage settings, or injects catalog items into commands. Both configuration
values and secrets use the secure value path; catalog metadata is not a runtime
configuration instruction.

## Naming and identity

An entry has three independently validated fields: `service`, `label`, and `type`.
Each starts with a lowercase letter and contains letters/digits only (camelCase is
supported). Its display name joins them with exactly two underscores:
`github_eidolonReleases_token`, `ssh_dl380_password`, or
`discord_statusAlerts_webhookUrl`. Description carries purpose and context.

The immutable entry ID is authoritative. Renaming never transfers grants or
retargets a use. Names, descriptions, and type labels are untrusted metadata, not
instructions, host verification, destination authority, or environment mappings.
Names/descriptions may themselves be sensitive, so agents only discover entries
with a current exact discovery grant.

## Owner surface and secure input

Settings → Organization → Environment & credentials shows metadata, backend mode,
value availability, revisions, grants, and status-only receipts. There is no reveal
endpoint, masked-value prefix/suffix, or value hash. Owners review mutations and
exact agent/scope permission changes before submission. Granting persistent access
is an owner action; agents cannot grant or broaden their own authority.

The value field is an uncontrolled password input, cleared on submission,
cancellation, disconnect, profile/owner change, and unmount. Raw values and handles
are never placed in the renderer's durable intent cache. A dedicated secure-input
request creates a process-local, single-use, 90-second handle bound to the exact
organization, entry, next value version, actor, operation, and connection. A timer
clears the owned buffer even if no later request arrives; disconnect also retires
pending input. Python, JSON, browser and OS APIs may create copies, so this is not a
claim of perfect memory erasure.

A method name alone is not a secure channel. The server issues a capability only
**after** existing local route token/origin checks, on an explicitly non-shared
loopback listener with matching loopback peer/server binding and no forwarding
headers. Missing/unknown auth state fails closed. The capability binds the immutable
served profile; client-supplied owner/profile/transport claims cannot mint it. Remote,
shared, forwarded, and unknown transports are unsupported for these owner RPCs.
Malformed-frame diagnostics include only error class and character count, never raw
payload or exception text. Credential handlers sanitize backend/provider failures.

An uncertain mutation is reconciled using its operation receipt. Raw value input is
never automatically replayed. A missing receipt alone does not permit retry. The
owner can explicitly cancel the pending operation: a writer-serialized cancellation
returns the already committed receipt or creates an immutable actor+operation
tombstone that blocks delayed old calls. Only after that fence may the owner submit
a fresh operation and fresh value. A native
write whose SQLite commit failed may leave an inaccessible encrypted orphan; this
release does not silently enumerate or remove vault items to reconcile it.

## Native storage

- **macOS:** Security.framework generic-password items, with a profile-namespaced
  service and full immutable binding as account. Reads are restricted to the exact
  selected file keychain. The production adapter uses the default keychain without
  changing it or its search list. Interactive unlocking is forbidden; a locked or
  unavailable keychain fails closed. No plaintext fallback.
- **Windows:** current-user Credential Manager generic credentials, with a fixed
  Eidolon prefix, profile namespace, and full immutable binding target. Values are
  bounded to the documented 2,560-byte generic-credential blob limit. Exact-target
  reads only; no enumeration/import. Writes refuse existing targets. Owned native
  read/write buffers are cleared before release where supported.
- **Other platforms:** explicitly unavailable. The UI never labels an in-memory
  test double or unavailable interface as encrypted storage.

These OS stores isolate credentials using the operating-system user's boundary.
They do not defend against arbitrary malicious code already executing as that same
user, debuggers, process memory access, or a compromised trusted renderer/backend.
Credential Manager's existence-check/write sequence is serialized by this catalog's
transaction, not atomic against arbitrary unrelated same-user applications. Metadata
and audit IDs are stored in a separate SQLite database, without value bytes; metadata
is not claimed to be encrypted. Existing host filesystem access policy remains the
boundary for that database.

## Permissions, execution, and revocation

The catalog independently checks `discover`, `use`, `create`, `edit_metadata`,
`replace_value`, `revoke`, and `remove`. Create grants name one exact
`create:service:label:type:kind` scope. Other grants identify one stable entry ID.
Use additionally binds an exact registered tool and destination; an entry label
cannot expand these bindings. Cross-organization moves and agent grant editing are
unsupported. Metadata editors cannot change classification or extend expiry.

The scheduler can issue metadata/management tools only to active persistent worker
identities with explicit grants, preserving the existing tool-free leader/control
boundaries. Every invocation checks the live request token, lease, policy generation,
objective cancellation, stop flag, active worker identity, current organization and
current grant. The lease transaction serializes catalog operations with ledger
revocation/cancellation. Tool schemas do not accept secret text, grant edits, runtime
environment maps, or arbitrary process/network commands. Creation, metadata edits,
revocation, and removal have durable CAS/idempotency receipts and metadata-only audit.

Secure replacement is implemented by the owner bridge and core handle contract.
An owner may prepare a value for an already-running Worker with the exact entry's
current replacement grant. A separate model tool accepts only its non-bearer
handoff reference; the private single-use input remains bound to the exact owner
connection, Worker generation, live request, entry/version and reviewed grant
revision. Preparation is not replacement success. Lost acknowledgments use status
and explicit fenced cancellation, never automatic value replay. See
[owner-prepared replacement](organization-credential-handoffs.md) for the precise
lifetime and native-start boundary. There is no raw-value agent API. Provider-side
credential creation or autonomous rotation requires a separate authorized adapter
and is not implemented.

The internal broker checks exact tool/destination grants, version, status, and expiry
when queuing and again immediately before exposing bytes to a trusted adapter. It
returns status only and discards adapter return values. **No production GitHub, SSH,
or generic credential-bearing execution adapter is registered in this release.** The
owner surface therefore cannot grant `use` or invent destination bindings. The project
runner stays credential-free and network-isolated; arbitrary project code is never
given catalog values.

Revoke blocks new uses and cancels queued/resolving uses. A use whose exposure already
started is reported as in flight; revoke does not pretend to recall those bytes.
Removal requires prior revocation and retains a tombstone. Local revoke/remove does
not revoke the provider credential or delete old stored native values. Expiry stops
new catalog uses, not provider-side validity. Audit receipts state these limits.

## Verification

Platform-neutral tests use synthetic values only. They exercise real SQLite state,
owner RPC dispatch, wrong organization/owner/transport, independent grants, stable
identity, CAS and idempotency, queued revocation and rotation races, in-flight truth,
lease/stop fences, malformed-frame logging and model-output leak checks. A real local
HTTP model fixture exercises actual agent tool rounds without a live provider.

Native tests carry `macos_only`/`windows_only` markers and run in the existing hosted
OS-specific CI lanes. macOS creates only a fresh non-login temporary keychain, checks
read-only default/search-list snapshots before/after setup and cleanup, and locks/
unlocks/deletes only that temporary keychain. Windows writes a fresh unique synthetic
target, verifies fresh-process persistence, and deletes only the exact target it
created. No test enumerates, imports, or configures existing user credentials.

Native fixture/transport review and passing hosted execution are separate gates;
a source review alone does not establish native success. UI acceptance likewise
needs its actual renderer/gateway fixture, in addition to focused client/DOM tests.

## Primary API references

- [Windows generic credential structure and blob limit](https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw)
- [CredWriteW semantics](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credwritew)
- [Apple keychain selection](https://developer.apple.com/documentation/security/ksecusekeychain)
- [Apple Security keychain creation implementation](https://github.com/apple-oss-distributions/Security/blob/main/OSX/libsecurity_keychain/lib/StorageManager.cpp)

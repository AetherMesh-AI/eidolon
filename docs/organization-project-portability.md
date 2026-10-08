# Back up or transfer saved organization projects

Project identities saved through the owner-confirmed organization setup live in
the active profile's `organization/state.db`. Saving an identity does not rewrite
`config.yaml` or create filesystem, tool, recipe, team or provider grants.

## Recovery backups

Both full `eidolon backup` archives and quick snapshots (`eidolon backup --quick`,
`/snapshot`, and pre-update snapshots) retain `organization/state.db`. SQLite's
backup API captures committed WAL data consistently; raw WAL/SHM sidecars are not
paired with that copy. The existing snapshot size limits and incomplete-backup
warnings still apply.

These are private recovery artifacts. The database contains durable identities,
binding fingerprints, policy, approval receipts and organization history. A full
backup also contains other profile credentials and state. Protect recovery files
accordingly; use profile export when transferring portable identity metadata.

## Portable profile exports

`eidolon profile export NAME` reads saved identities in one read-only SQLite
transaction. It never opens the organization service, adopts policy, creates the
registry, or runs a schema migration. The default profile and named profiles use
the same metadata export. A missing database or older database without the registry
needs no migration; an unreadable registry fails the export visibly.

The archive contains `organization-projects.json`, a versioned document with
`requiresOwnerReview: true` and a `projects` list. Each entry contains only `id`,
`root`, `recipe` and `team`. Root and recipe values are aliases/identifiers, not
host paths or executable recipes. Binding fingerprints, timestamps, policy,
approvals, work artifacts and ledger history are omitted. The raw `organization`
subtree, managed `backups`, and `state-snapshots` are excluded so recovery copies
cannot reintroduce the ledger. Existing staged text secret redaction still applies.
No export metadata is written into the live profile.

## Import requires review before activation

`eidolon profile import ARCHIVE --name NAME` retains exported JSON as a reviewable
file. These metadata-only exports do not turn those entries into active projects,
write them into YAML, restore the organization database, or add grants. Existing
profile configuration transfer behavior is unchanged; the JSON itself is never a
source of runtime authority.
If there are no locally saved identities, a later profile export keeps this review
file so an interrupted transfer does not discard it.

Review the file against the destination profile's actual root paths, exact recipe
file lists and teams. Then use that profile's guided project setup to select the
existing choices and explicitly confirm each intended identity. Matching alias
names alone do not establish that a different computer has the same authority or
repository. Configure any missing authority separately through the existing owner
configuration flow. Exported JSON is not an approval receipt or an automatic
restore mechanism. Use a private recovery backup when the intention is to restore
the original ledger and its complete history.

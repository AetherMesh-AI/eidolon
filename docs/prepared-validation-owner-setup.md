# Prepared validation owner setup

This is an explicit served-owner configuration flow. Reading setup and preparing a
review never start work. Saving appends a separate registration; it does not change
the legacy project `{id, root, recipe, team}`, its binding digest, tools, staff grants,
existing objectives, or service state. A project with `recipe: null` remains edit-denied.

## Owner flow

1. Select an existing project in project setup.
2. Supply the exact project-relative source files and source capture limits. Include
   the lockfile, package manifests, both configurations, and required source/test files.
   Execution-only exceptions require exact reviewed path/SHA-256 pairs. Protected
   paths remain excluded; exception bytes are not source-review evidence.
3. Supply an already provisioned dependency directory and its complete manifest.
   Select the two-step profile: `workspace_typescript` and `workspace_vitest_ui`.
   The profile has one CPU and 64 PIDs, plus explicit other resource bounds.
4. Explicitly enable the proposed registration, verify bytes, and review the exact
   paths, exceptions, profile, directory identities, hashes, and observed counts.
5. Explicitly confirm the save. Each **new** objective must separately select the
   returned `{projectId, revision, registrationSha256}`. Existing objectives never
   adopt a later registration or re-enable automatically. Replacing a registration
   makes older selected revisions unavailable; existing objectives do not adopt
   the replacement.

Setup displays and copies at most eight exact `{projectId, revision,
registrationSha256}` references from the verified current descriptor. Each must
match an enabled, non-revoked project revision/hash. After a confirmed save or
revocation, a new setup read must succeed at or beyond the acknowledged sidecar
revision before references are shown. A failed refresh preserves the acknowledged
mutation result and offers refresh, not a second save. Uncertain mutations and
changed owner/socket scopes hide references. A displayed reference reflects that
read; objective creation still rechecks its current authority and runtime limits.

Every future validation set reserves two execution units, including interruptions.
The existing objective and owner ceilings and read/test/staff grants still apply.

## Explicit new-objective selection

The manual new-objective form has a separate, initially off prepared-validation
mode. Enabling it deliberately selects managed-artifact delivery and both
`project_typecheck` and `project_tests`, and removes incompatible source integration.
The owner then reads currently available registrations and checks the exact
revision/hash for every selected project. Availability never checks those boxes.
Partial, stale, missing, unsupported, or mismatched selections block submission;
they cannot silently become a legacy objective. A detected owner, socket, or project-binding change
clears references while keeping requested mode blocked until re-review or explicit
disable. The registration save-history capacity limit alone does not prevent reuse
of already enabled exact references.

Create requests carry `preparedValidations` only for this explicit selection. Legacy
requests omit it entirely. Existing draft idempotency keys and submitted intents
remain retained after uncertain replies, including across re-review. The backend
rechecks every selected registration in objective admission. Goal continuation,
assisted intake, and existing objectives do not infer or acquire these selections.
The existing restrictions on managed validation for planning-only folders remain.

## What preparation proves

The backend captures source through no-follow descriptors and copies/verifies every
manifest dependency into private staging. Source root and dependency root device/inode
identities are pinned, including the actual dependency descriptor opened for copying.
No command, package installation, provider request, image pull, or network access runs.
Temporary staging is cleaned before a successful byte-verification observation returns.

Source capture stays within owner-reviewed limits and the existing snapshot ceilings.
Dependency inventory stays within the existing 100,000-file, 4-GiB total, 256-MiB per-file
bounds. One fixed 120-second preparation deadline and fresh owner/policy checks bound
capture and copying. A failure, deadline, changed authority, or unconfirmed cleanup
cannot produce a saved registration.

`verified_bytes_runtime_unprobed` means the selected bytes were verified. It does not
prove Docker availability, platform compatibility of packages, meaningful test
assertions, successful execution, independent review, or acceptance. The only supported
host lane is Linux x64 using the fixed pinned Docker runtime. Other hosts report
`unsupported_platform`; no local-command fallback exists.

The full dependency inventory is retained in the reviewed candidate. The UI shows its
hashes and count/byte totals without turning tens of thousands of entries into DOM nodes.
Collapsed source/profile review is lazy and bounded. It labels partial source/hash
previews explicitly; the complete supplied selection remains in the form and the
retained candidate. The summary does not claim omitted entries were reviewed.

## Owner RPC and persistence boundary

- `organization.preparedValidationSetup`: read the served owner's descriptor, projects,
  global sidecar revision, platform limitations, and currently selectable references.
- `organization.previewPreparedValidation`: owner, organization, policy generation,
  source revision, and sidecar compare-and-swap plus the exact selection.
- `organization.savePreparedValidation`: the same compare-and-swap, candidate,
  candidate hash, preview version, explicit confirmation, and idempotency key.
- `organization.revokePreparedValidation`: explicit owner confirmation of the exact
  project revision/hash and current policy/sidecar revisions. It never reads source or
  dependency directories, so missing folders cannot prevent disabling a registration.

Read and revoke retain the normal 15-second owner request timeout. Only preview/save
use a 180-second client timeout to cover the bounded 120-second preparation plus
serialization/transport margin. This does not extend runtime/test deadlines or retry
an execution.

Append-only tables reject update, delete, and duplicate-key `INSERT OR REPLACE`, even
with recursive triggers disabled. Reads first fetch bounded scalar metadata and SQL
blob lengths, then load only the selected record. Historical manifest blobs are not
materialized together. Stored receipts do not duplicate candidates; exact retry replies
reconstruct the original receipt. Revoke events are lightweight references and remain
available when save capacity is reached. Re-enable creates a new revision and hash;
older objective references cannot revive.

`current_prepared_registration` resolves in the caller's database transaction and
compares the actual owner, policy, source revision, legacy project binding, and live
root/dependency identities. A disabled/missing registration returns no authority.
Runtime dispatch must still recapture bytes and apply existing grants and ceilings.

`check_current_prepared_registration` is the lighter repeated checkpoint fence for
an unmodified result already fully validated by that helper. Each invocation in a
fresh transaction checks the current served owner, policy, source, exact selected
row sequence/revision/hash/enabled state, project binding, and directory identities.
It reads scalar sidecar fields only, never candidate/receipt blobs or the full
dependency inventory. The selected row's sequence is independent of unrelated
project registrations. Full validation remains mandatory at admission and dispatch;
this fence is not an arbitrary candidate validator or a runtime readiness probe.

## Verification scope

The retained native foundation artifact 12022311886 has ZIP SHA-256
`4516a6832fcbdb3f94287dd416aa39b6e32a6dc6b49716d8e2ab6abf2610dadd`.
Its exact 92,160-file dependency manifest is 17,397,350 canonical JSON bytes for
904,208,820 dependency bytes. A reconstructed owner envelope with the checked-in
2,264-file/53-exception renderer selection serializes to approximately 35.1 MB,
below the 80-MiB owner input bound and the existing 384-MiB gateway frame bound.
This is a serialization measurement, not a live owner preparation or native UI pass.
Full-fixture copy timing and the complete Electron owner review flow require the
hosted prepared fixture/native acceptance lane.

## Recorded prepared activity

Prepared objectives retain their exact immutable registration references in the
objective view. The separate prepared-activity section reads the snapshot's scalar
status projection, with at most eight current projects and twenty retained-history
rows. Those history rows can include the current generations. Current sets never
fall back to an older apparently successful generation.

Unit totals are explicitly recorded, unverified display counts. Each recorded set
contributes two units, including an undispatched or interrupted pair; this view does
not replace authoritative budget accounting. Dispatch, resource, result, cleanup,
aggregate and review presence are recorded claims. Even a stored `passed` result,
settled cleanup flags or a pair-review row does not prove authenticated receipts,
review approval, acceptance, execution authorization or source delivery. The UI
requires every authority flag to remain false and does not invent evidence links.

A selected registration with missing status is shown as unavailable, never as zero
usage. Malformed optional status, unknown enum values, mismatched objective/project
identities or excessive history fail snapshot validation. Legacy objectives that
omit prepared fields keep their original execution view.

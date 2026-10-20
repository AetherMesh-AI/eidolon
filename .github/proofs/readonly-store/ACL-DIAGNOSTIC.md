# Bounded Windows ACL mismatch observation

This is a separate diagnostic-only derivative of frozen native proof revision 3,
source manifest SHA-256
`cf7a018dc4ee3e756f1ca25cdbfa0f3f30fe87688d27eaba09b5d244b6b24c17`.
The exact base inventory is retained as `base-manifest.json`. The previous
revision, its evidence, and product code are not modified.

## Scope and decisive result

Only `native_permissions.py` changes among the base's 17 source files. The sole
behavioral addition is a guarded diagnostic string appended to the existing
full-descriptor-byte-equality AssertionError. The condition, capture calls,
restoration calls and order, permission restrictions, ownership checks, and
failure cleanup behavior remain unchanged. No additional native/security API,
filesystem capture, SACL access, account lookup, token privilege, permission
change, retry, or alternate restoration is introduced.

The observer receives only the two existing immutable captured byte strings.
Raw security descriptors, raw SIDs, SID strings, ACE payloads, exception details,
and account names are never included in its output. Component and whole-byte
equalities are computed directly from the captured bytes, not by hash comparison.
The additional information cannot turn a failed restoration into a pass.

`valid: true` means the observer completely parsed its supported bounded
structural format. It does not mean Windows validated the descriptor or that
effective access is equivalent. Every record says
`accessEquivalence: "not_assessed"`. The helper supports ordinary allow and deny
ACEs (types 0 and 1). Other potentially valid ACE layouts invalidate the
observation as unsupported, rather than receiving a guessed interpretation.

## Fixed marker and schema

An original mismatch error ends with a semicolon, a space, the exact marker
`ACL_DESCRIPTOR_COMPARISON_V1=`, and one compact JSON object. JSON is ASCII and
contains no embedded newlines. Its schema identifier is
`windows-acl-descriptor-comparison-v1`.

A normal comparison has these exact top-level keys:

- `schema`: identifier above
- `valid`: true only when both sides are complete supported observations
- `before`, `after`: side summaries below
- `equal`: equality object below when valid, otherwise null
- `accessEquivalence`: always `not_assessed`

A valid side summary has:

- `valid: true`, `length`, `sha256`
- `revision`, `sbz1`, numeric `control`, ordered `controlFlags`
- `offsets`: numeric `owner`, `group`, `sacl`, `dacl`
- `owner`, `group`: `present`, `length`, `sha256`; absent SID has false/0/null
- `dacl`: `present`, `null`, `revision`, `size`, `aceCount`, `bytesInUse`,
  `bytesFree`, `sha256`, and ordered `aces`
- Each ACE: `index`, `type`, `flags`, `size`, uint32 `mask`, `sha256`, and
  `sid` containing only `length` and `sha256`

An absent or NULL DACL has revision/hash null, all counts/sizes zero, and empty
ACEs. The separate `present` and `null` booleans distinguish those cases. An
empty present DACL has a real ACL header and zero ACEs.

The equality object has exactly these boolean keys:
`descriptorBytes`, `revision`, `sbz1`, `control`, `offsets`, `ownerBytes`,
`groupBytes`, `daclBytes`, `orderedAceBytes`. DACL equality includes presence,
so absent and NULL are not considered equal. ACE ordering is never normalized.

An invalid side summary has only `valid: false` and a fixed `reason` code. Its
opposite side can still carry a valid summary, but the comparison remains
invalid with `equal: null`; no partial comparison verdict is emitted.

An observer/import/formatter/serialization failure, or an output-size limit,
has only these top-level keys: `schema`, `valid: false`, `reason`, `equal: null`,
`accessEquivalence: "not_assessed"`. It has no before/after summaries. The fixed
reasons are `observer_failure` and `diagnostic_size_limit`, respectively. These
failures cannot replace the original AssertionError.

## Bounds and closed failure reasons

Limits are 131072 descriptor bytes, 64 ACEs per descriptor, and 32768 compact
JSON characters (excluding the fixed marker). Descriptor offsets are DWORD
aligned, exclude the header, and must fit the captured byte string. All SID,
ACL, and ACE spans are checked before reading their contents. Only exact
owner/group SID-range aliasing is supported; any partial overlap, or overlap
with a DACL, invalidates the observation. Trailing descriptor bytes and unused
ACL bytes remain part of the corresponding exact-byte digest/equality; they
are never discarded to make a result pass.

SACL presence or a nonzero SACL offset immediately produces
`unsupported_sacl_presence`; its bytes are not dereferenced, parsed, or hashed
by the observer.

The parser's fixed failure reasons are:

- `invalid_input_type`, `descriptor_size_limit`, `truncated_descriptor_header`
- `unsupported_descriptor_revision`, `not_self_relative`
- `unsupported_sacl_presence`, `absent_dacl_with_offset`
- `component_overlaps_header`, `unaligned_component_offset`, `truncated_component`
- `unsupported_sid_revision`, `sid_subauthority_limit`, `truncated_sid`
- `unsupported_acl_revision`, `nonzero_acl_reserved`, `invalid_acl_size`, `truncated_acl`
- `ace_count_limit`, `truncated_ace_header`, `invalid_ace_size`, `truncated_ace`
- `unsupported_ace_type`, `truncated_ace_sid`, `unsupported_ace_sid_revision`
- `ace_sid_subauthority_limit`, `invalid_ace_sid_size`, `overlapping_components`

Consumers must reject missing, duplicate, malformed, oversized, truncated, or
schema-inconsistent observations. A valid observation must never override the
native proof failure or imply effective-access equivalence. The existing proof
case inventory and pass gate are unchanged.

The hook enforces the closed schema before including formatted output. Reuse
`acl_descriptor_diagnostic.validate_comparison_message(message, require_mismatch=True)`
for artifact gating. Pass exactly the marker plus compact JSON, without pytest
prefixes or trailing log text. It returns the validated record; any exception
invalidates the observation. It rejects unknown or duplicate keys, arbitrary
strings, noncanonical JSON, non-finite values, out-of-range fields, and boolean
values substituted for integers (or vice versa). The only arbitrary-length
strings admitted are the fixed-length lowercase hex digests. This validator
does not check provenance, match error-path identities, or decide whether an
artifact contains all expected observations; the artifact gate must bind the
record to the original assertion and frozen run evidence independently.

## Local controls

Run `PYTHONDONTWRITEBYTECODE=1 python test_acl_descriptor_diagnostic.py --receipt evidence/controls.json`.
The standalone synthetic suite does not invoke native Windows calls or change
permissions. It covers layout-only versus control, owner, group, DACL, ordered
ACE, access-mask and SID changes; exact aliases versus partial overlaps;
NULL/absent/empty components; padding; all caps; malformed/truncated inputs;
unsupported SACL/ACE cases; raw-data exclusion; and observer failures retaining
the original assertion. A deliberately constant hash verifies that equalities
do not depend on digest equality. An AST comparison verifies that removing the
single additive hook restores the exact frozen base program structure.

This suite is not Windows execution evidence. Its class is constructed only by
the standalone runner and is not added to the hosted pytest case inventory.

"""Bounded, observational parsing of two already-captured Windows descriptors.

No native APIs, filesystem operations, account lookups, or raw descriptor/SID
exports. ``valid`` means complete bounded structural observation, not Windows
validation or effective-access equivalence. All equalities compare actual
component bytes; digests are only evidence labels. Unsupported inputs fail
closed rather than becoming partial or normalized comparisons.
"""

import hashlib
import json
import struct


MARKER = "ACL_DESCRIPTOR_COMPARISON_V1="
SCHEMA = "windows-acl-descriptor-comparison-v1"
MAX_DESCRIPTOR_BYTES = 131072
MAX_ACES = 64
MAX_JSON_CHARS = 32768
PARSE_REASONS = frozenset({
    "invalid_input_type", "descriptor_size_limit", "truncated_descriptor_header",
    "unsupported_descriptor_revision", "not_self_relative", "unsupported_sacl_presence",
    "absent_dacl_with_offset", "component_overlaps_header", "unaligned_component_offset",
    "truncated_component", "unsupported_sid_revision", "sid_subauthority_limit",
    "truncated_sid", "unsupported_acl_revision", "nonzero_acl_reserved", "invalid_acl_size",
    "truncated_acl", "ace_count_limit", "truncated_ace_header", "invalid_ace_size",
    "truncated_ace", "unsupported_ace_type", "truncated_ace_sid",
    "unsupported_ace_sid_revision", "ace_sid_subauthority_limit", "invalid_ace_sid_size",
    "overlapping_components",
})
CONTROL_FLAGS = (
    (0x0001, "owner_defaulted"), (0x0002, "group_defaulted"),
    (0x0004, "dacl_present"), (0x0008, "dacl_defaulted"),
    (0x0010, "sacl_present"), (0x0020, "sacl_defaulted"),
    (0x0040, "dacl_untrusted"), (0x0080, "server_security"),
    (0x0100, "dacl_auto_inherit_req"), (0x0200, "sacl_auto_inherit_req"),
    (0x0400, "dacl_auto_inherited"), (0x0800, "sacl_auto_inherited"),
    (0x1000, "dacl_protected"), (0x2000, "sacl_protected"),
    (0x4000, "rm_control_valid"), (0x8000, "self_relative"),
)


class _Invalid(ValueError):
    """Only fixed non-sensitive reason codes may be supplied internally."""


def _require(condition, reason):
    if not condition:
        raise _Invalid(reason)


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _parse(data):
    _require(type(data) is bytes, "invalid_input_type")
    _require(len(data) <= MAX_DESCRIPTOR_BYTES, "descriptor_size_limit")
    _require(len(data) >= 20, "truncated_descriptor_header")
    revision, sbz1, control, owner, group, sacl, dacl = struct.unpack_from("<BBHIIII", data)
    _require(revision == 1, "unsupported_descriptor_revision")
    _require(control & 0x8000, "not_self_relative")
    # SACL data is outside this observer's scope. Do not dereference or hash it.
    _require(not sacl and not control & 0x0010, "unsupported_sacl_presence")
    _require(bool(control & 0x0004) or not dacl, "absent_dacl_with_offset")
    spans = []

    def span_start(offset, minimum):
        _require(offset >= 20, "component_overlaps_header")
        _require(offset % 4 == 0, "unaligned_component_offset")
        _require(offset <= len(data) and minimum <= len(data) - offset,
                 "truncated_component")

    def sid(offset, name):
        if not offset:
            return {"present": False, "length": 0, "sha256": None}, None
        span_start(offset, 8)
        _require(data[offset] == 1, "unsupported_sid_revision")
        count = data[offset + 1]
        _require(count <= 15, "sid_subauthority_limit")
        length = 8 + 4 * count
        _require(length <= len(data) - offset, "truncated_sid")
        raw = data[offset:offset + length]
        spans.append((offset, offset + length, name))
        return {"present": True, "length": length, "sha256": _hash(raw)}, raw

    owner_summary, owner_raw = sid(owner, "owner")
    group_summary, group_raw = sid(group, "group")
    present = bool(control & 0x0004)
    dacl_summary = {"present": present, "null": present and not dacl,
                    "revision": None, "size": 0, "aceCount": 0,
                    "bytesInUse": 0, "bytesFree": 0, "sha256": None, "aces": []}
    dacl_raw = None
    ace_raws = []
    if dacl:
        span_start(dacl, 8)
        acl_revision, reserved1, size, count, reserved2 = struct.unpack_from("<BBHHH", data, dacl)
        _require(acl_revision in (2, 4), "unsupported_acl_revision")
        _require(reserved1 == 0 and reserved2 == 0, "nonzero_acl_reserved")
        _require(size >= 8 and size % 4 == 0, "invalid_acl_size")
        _require(size <= len(data) - dacl, "truncated_acl")
        _require(count <= MAX_ACES, "ace_count_limit")
        end = dacl + size
        cursor = dacl + 8
        for index in range(count):
            _require(end - cursor >= 4, "truncated_ace_header")
            ace_type, flags, ace_size = struct.unpack_from("<BBH", data, cursor)
            _require(ace_size >= 4 and ace_size % 4 == 0, "invalid_ace_size")
            _require(ace_size <= end - cursor, "truncated_ace")
            # Only ordinary allow/deny ACE payloads are understood. Other ACEs
            # may be valid Windows data, but make this observation incomplete.
            _require(ace_type in (0, 1), "unsupported_ace_type")
            _require(ace_size >= 16, "truncated_ace_sid")
            _require(data[cursor + 8] == 1, "unsupported_ace_sid_revision")
            sid_count = data[cursor + 9]
            _require(sid_count <= 15, "ace_sid_subauthority_limit")
            _require(ace_size == 16 + 4 * sid_count, "invalid_ace_sid_size")
            raw = data[cursor:cursor + ace_size]
            mask = struct.unpack_from("<I", data, cursor + 4)[0]
            sid_raw = data[cursor + 8:cursor + ace_size]
            ace_raws.append(raw)
            dacl_summary["aces"].append({"index": index, "type": ace_type,
                                         "flags": flags, "size": ace_size,
                                         "mask": mask, "sid": {"length": len(sid_raw),
                                                                "sha256": _hash(sid_raw)},
                                         "sha256": _hash(raw)})
            cursor += ace_size
        dacl_raw = data[dacl:end]
        spans.append((dacl, end, "dacl"))
        dacl_summary.update(revision=acl_revision, size=size, aceCount=count,
                            bytesInUse=cursor - dacl, bytesFree=end - cursor,
                            sha256=_hash(dacl_raw))
    for index, left in enumerate(spans):
        for right in spans[index + 1:]:
            if max(left[0], right[0]) < min(left[1], right[1]):
                # Owner and group may legally reference the identical SID.
                alias = (left[:2] == right[:2] and
                         {left[2], right[2]} == {"owner", "group"})
                _require(alias, "overlapping_components")
    summary = {
        "valid": True, "length": len(data), "sha256": _hash(data),
        "revision": revision, "sbz1": sbz1, "control": control,
        "controlFlags": [name for bit, name in CONTROL_FLAGS if control & bit],
        "offsets": {"owner": owner, "group": group, "sacl": sacl, "dacl": dacl},
        "owner": owner_summary, "group": group_summary, "dacl": dacl_summary,
    }
    # Private return value: never serialized. Preserve presence separately,
    # since absent and NULL DACLs both have no component bytes.
    raw_parts = (owner_raw, group_raw, (present, dacl_raw), tuple(ace_raws))
    return summary, raw_parts


def _observe(data):
    try:
        return _parse(data)
    except _Invalid as exc:
        reason = str(exc)
        _require(reason in PARSE_REASONS, "invalid_internal_reason")
        return {"valid": False, "reason": reason}, None


def compare_descriptors(before, after):
    """Return complete bounded comparison or explicitly invalid observation."""
    left, left_raw = _observe(before)
    right, right_raw = _observe(after)
    valid = left["valid"] and right["valid"]
    result = {"schema": SCHEMA, "valid": valid, "before": left, "after": right,
              "equal": None, "accessEquivalence": "not_assessed"}
    if valid:
        result["equal"] = {
            "descriptorBytes": before == after,
            "revision": left["revision"] == right["revision"],
            "sbz1": left["sbz1"] == right["sbz1"],
            "control": left["control"] == right["control"],
            "offsets": left["offsets"] == right["offsets"],
            "ownerBytes": left_raw[0] == right_raw[0],
            "groupBytes": left_raw[1] == right_raw[1],
            "daclBytes": left_raw[2] == right_raw[2],
            "orderedAceBytes": left_raw[3] == right_raw[3],
        }
    return result


def comparison_message(before, after):
    """Serialize bounded evidence; caller preserves its original assertion."""
    result = compare_descriptors(before, after)
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded) > MAX_JSON_CHARS:
        encoded = ('{"accessEquivalence":"not_assessed","equal":null,'
                   '"reason":"diagnostic_size_limit",'
                   '"schema":"windows-acl-descriptor-comparison-v1","valid":false}')
    return MARKER + encoded


def validate_comparison_message(message, *, require_mismatch=False):
    """Closed outbound/artifact schema; reject rather than sanitize bad output.

    This reads only bounded diagnostic JSON, not security descriptors. Return
    the validated record for artifact consumers. Unknown keys, arbitrary
    strings, duplicate JSON keys and bool-as-int fields are forbidden.
    """
    def need(condition):
        if not condition:
            raise ValueError("Invalid diagnostic schema")

    def keys(value, expected):
        need(type(value) is dict and set(value) == set(expected.split()))

    def integer(value, low, high):
        need(type(value) is int and low <= value <= high)

    def boolean(value):
        need(type(value) is bool)

    def digest(value):
        need(type(value) is str and len(value) == 64 and
             all(char in "0123456789abcdef" for char in value))

    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result)
            result[key] = value
        return result

    def forbidden_constant(unused):
        raise ValueError("Invalid diagnostic schema")

    def sid_summary(value, *, presence):
        keys(value, "present length sha256" if presence else "length sha256")
        if presence:
            boolean(value["present"])
        if not presence or value["present"]:
            integer(value["length"], 8, 68)
            need(value["length"] % 4 == 0)
            digest(value["sha256"])
        else:
            integer(value["length"], 0, 0)
            need(value["sha256"] is None)

    def side(value):
        need(type(value) is dict and type(value.get("valid")) is bool)
        if not value["valid"]:
            keys(value, "valid reason")
            need(type(value["reason"]) is str and value["reason"] in PARSE_REASONS)
            return
        keys(value, "valid length sha256 revision sbz1 control controlFlags offsets owner group dacl")
        integer(value["length"], 20, MAX_DESCRIPTOR_BYTES)
        digest(value["sha256"])
        integer(value["revision"], 1, 1)
        integer(value["sbz1"], 0, 255)
        integer(value["control"], 0, 65535)
        need(bool(value["control"] & 0x8000) and not value["control"] & 0x0010)
        need(type(value["controlFlags"]) is list and value["controlFlags"] ==
             [name for bit, name in CONTROL_FLAGS if value["control"] & bit])
        keys(value["offsets"], "owner group sacl dacl")
        for offset in value["offsets"].values():
            integer(offset, 0, value["length"])
            need(offset == 0 or offset >= 20 and offset % 4 == 0)
        need(value["offsets"]["sacl"] == 0)
        spans = []
        for name in ("owner", "group"):
            item = value[name]
            sid_summary(item, presence=True)
            offset = value["offsets"][name]
            need(bool(offset) == item["present"])
            need(offset + item["length"] <= value["length"])
            if offset:
                spans.append((offset, offset + item["length"], name))
        item = value["dacl"]
        keys(item, "present null revision size aceCount bytesInUse bytesFree sha256 aces")
        boolean(item["present"])
        boolean(item["null"])
        need(item["present"] == bool(value["control"] & 4))
        offset = value["offsets"]["dacl"]
        need(item["null"] == (item["present"] and not offset))
        need(not offset or item["present"])
        need(type(item["aces"]) is list)
        if not offset:
            need(item["revision"] is None and item["sha256"] is None and not item["aces"])
            for name in ("size", "aceCount", "bytesInUse", "bytesFree"):
                integer(item[name], 0, 0)
        else:
            integer(item["revision"], 2, 4)
            need(item["revision"] in (2, 4))
            integer(item["size"], 8, 65535)
            need(item["size"] % 4 == 0 and offset + item["size"] <= value["length"])
            integer(item["aceCount"], 0, MAX_ACES)
            need(len(item["aces"]) == item["aceCount"])
            integer(item["bytesInUse"], 8, item["size"])
            integer(item["bytesFree"], 0, item["size"])
            need(item["bytesInUse"] + item["bytesFree"] == item["size"])
            digest(item["sha256"])
            for index, entry in enumerate(item["aces"]):
                keys(entry, "index type flags size mask sid sha256")
                integer(entry["index"], index, index)
                integer(entry["type"], 0, 1)
                integer(entry["flags"], 0, 255)
                integer(entry["size"], 16, 76)
                integer(entry["mask"], 0, 0xffffffff)
                sid_summary(entry["sid"], presence=False)
                need(entry["size"] == 8 + entry["sid"]["length"])
                digest(entry["sha256"])
            need(item["bytesInUse"] == 8 + sum(entry["size"] for entry in item["aces"]))
            spans.append((offset, offset + item["size"], "dacl"))
        for index, left in enumerate(spans):
            for right in spans[index + 1:]:
                if max(left[0], right[0]) < min(left[1], right[1]):
                    need(left[:2] == right[:2] and {left[2], right[2]} == {"owner", "group"}
                         and value["owner"] == value["group"])

    need(type(message) is str and message.startswith(MARKER))
    encoded = message[len(MARKER):]
    need(0 < len(encoded) <= MAX_JSON_CHARS)
    result = json.loads(encoded, object_pairs_hook=pairs, parse_constant=forbidden_constant)
    need(type(result) is dict and result.get("schema") == SCHEMA and
         result.get("accessEquivalence") == "not_assessed")
    boolean(result.get("valid"))
    if "reason" in result:
        keys(result, "schema valid reason equal accessEquivalence")
        need(result["valid"] is False and result["equal"] is None and
             type(result["reason"]) is str and
             result["reason"] in ("observer_failure", "diagnostic_size_limit"))
    else:
        keys(result, "schema valid before after equal accessEquivalence")
        side(result["before"])
        side(result["after"])
        need(result["valid"] == (result["before"]["valid"] and result["after"]["valid"]))
        if not result["valid"]:
            need(result["equal"] is None)
        else:
            eq = result["equal"]
            keys(eq, "descriptorBytes revision sbz1 control offsets ownerBytes groupBytes daclBytes orderedAceBytes")
            for value in eq.values():
                boolean(value)
            left, right = result["before"], result["after"]
            for name in ("revision", "sbz1", "control", "offsets"):
                need(eq[name] == (left[name] == right[name]))
            for name, flag in (("owner", "ownerBytes"), ("group", "groupBytes"), ("dacl", "daclBytes")):
                need(not eq[flag] or left[name] == right[name])
            need(not eq["orderedAceBytes"] or left["dacl"]["aces"] == right["dacl"]["aces"])
            need(not eq["descriptorBytes"] or left == right and all(eq.values()))
            need(not require_mismatch or eq["descriptorBytes"] is False)
    # Reject hidden whitespace, escapes or trailing text: the emitted form is
    # exactly one canonical ASCII JSON record with the closed fields above.
    need(encoded == json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return result

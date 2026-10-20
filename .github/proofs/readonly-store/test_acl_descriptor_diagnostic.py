"""Standalone synthetic descriptor controls, never a Windows execution proof.

Run with: python test_acl_descriptor_diagnostic.py --receipt evidence/controls.json
No native methods or permission changes are invoked. The test class is built
only inside build_suite(), outside the hosted pytest case inventory.
"""

import argparse
import ast
import copy
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import unittest
from unittest import mock

import acl_descriptor_diagnostic as diagnostic
import native_permissions


BASE_AST_SHA256 = "f8c93127a8e4f252bb96849b39bdc33f1ca8d2beaa10cd437b1c1aed2b75760e"


def sid(number=1):
    return struct.pack("<BB6sI", 1, 1, b"\0\0\0\0\0\5", number)


def ace(number=1, *, kind=0, flags=0, mask=1):
    payload = struct.pack("<I", mask) + sid(number)
    return struct.pack("<BBH", kind, flags, 4 + len(payload)) + payload


def acl(aces=(), *, tail=b"", revision=2):
    body = b"".join(aces) + tail
    return struct.pack("<BBHHH", revision, 0, 8 + len(body), len(aces), 0) + body


def descriptor(*, owner=None, group=None, dacl=None, control=0x8004,
               order=("owner", "group", "dacl"), alias=False, tail=b""):
    components = {"owner": sid(100) if owner is None else owner,
                  "group": sid(200) if group is None else group,
                  "dacl": acl((ace(), ace(2, kind=1))) if dacl is None else dacl}
    offsets = {"owner": 0, "group": 0, "dacl": 0}
    body = b""
    for name in order:
        if alias and name == "group":
            offsets[name] = offsets["owner"]
        elif components[name]:
            offsets[name] = 20 + len(body)
            body += components[name]
    return struct.pack("<BBHIIII", 1, 0, control, offsets["owner"],
                       offsets["group"], 0, offsets["dacl"]) + body + tail


def change(data, offset, form, value):
    result = bytearray(data)
    struct.pack_into(form, result, offset, value)
    return bytes(result)


def observe(before, after):
    return diagnostic.compare_descriptors(before, after)


def message_record(message):
    return json.loads(message.split(diagnostic.MARKER, 1)[1])


def build_suite():
    class DescriptorControls(unittest.TestCase):
        def invalid(self, data, reason):
            result = observe(data, descriptor())
            self.assertFalse(result["valid"])
            self.assertIsNone(result["equal"])
            self.assertEqual(result["before"], {"valid": False, "reason": reason})
            self.assertEqual(result["accessEquivalence"], "not_assessed")

        def hook_failure(self, before, after):
            backend = object.__new__(native_permissions._WindowsAcl)
            with mock.patch.object(backend, "capture", return_value={"descriptor": after}) as capture:
                with self.assertRaises(AssertionError) as caught:
                    backend.check_restored(Path("owned-fixture"), {"descriptor": before})
            capture.assert_called_once_with(Path("owned-fixture"))
            message = str(caught.exception)
            self.assertTrue(message.startswith("OWNER/GROUP/DACL descriptor not restored byte-for-byte:"))
            self.assertEqual(message.count(diagnostic.MARKER), 1)
            self.assertIn("before=" + hashlib.sha256(before).hexdigest(), message)
            self.assertIn("after=" + hashlib.sha256(after).hexdigest(), message)
            self.assertNotIn("NONPUBLIC_RAW_SENTINEL", message)
            return message_record(message)

        def rejected_record(self, record):
            encoded = diagnostic.MARKER + json.dumps(record, sort_keys=True, separators=(",", ":"))
            with self.assertRaises(ValueError):
                diagnostic.validate_comparison_message(encoded, require_mismatch=True)
            with mock.patch.object(diagnostic, "comparison_message", return_value=encoded):
                result = self.hook_failure(descriptor(), descriptor(owner=sid(99)))
            self.assertEqual(result["reason"], "observer_failure")

        def test_identical_descriptor(self):
            data = descriptor()
            result = observe(data, data)
            self.assertTrue(result["valid"])
            self.assertTrue(all(result["equal"].values()))
            self.assertEqual(result["accessEquivalence"], "not_assessed")

        def test_layout_only_keeps_component_equalities(self):
            before = descriptor()
            after = descriptor(order=("dacl", "group", "owner"))
            result = self.hook_failure(before, after)
            self.assertTrue(result["valid"])
            for key in ("ownerBytes", "groupBytes", "daclBytes", "orderedAceBytes", "control"):
                self.assertTrue(result["equal"][key])
            self.assertFalse(result["equal"]["descriptorBytes"])
            self.assertFalse(result["equal"]["offsets"])

        def test_control_and_sbz1_changes_are_reported(self):
            before = descriptor()
            for offset, form, value, key in ((2, "<H", 0x9004, "control"),
                                             (2, "<H", 0x8404, "control"),
                                             (1, "B", 1, "sbz1")):
                with self.subTest(key=key, value=value):
                    result = self.hook_failure(before, change(before, offset, form, value))
                    self.assertTrue(result["valid"])
                    self.assertFalse(result["equal"][key])
                    self.assertTrue(result["equal"]["daclBytes"])

        def test_owner_and_group_changes_remain_distinct(self):
            before = descriptor()
            for name, after in (("ownerBytes", descriptor(owner=sid(101))),
                                ("groupBytes", descriptor(group=sid(201)))):
                with self.subTest(name=name):
                    result = observe(before, after)
                    self.assertTrue(result["valid"])
                    self.assertFalse(result["equal"][name])
                    self.assertTrue(result["equal"]["daclBytes"])

        def test_ordered_aces_are_not_sorted_or_merged(self):
            before = descriptor(dacl=acl((ace(), ace(2, kind=1))))
            after = descriptor(dacl=acl((ace(2, kind=1), ace())))
            result = observe(before, after)
            self.assertFalse(result["equal"]["orderedAceBytes"])
            self.assertFalse(result["equal"]["daclBytes"])
            self.assertEqual([item["type"] for item in result["before"]["dacl"]["aces"]], [0, 1])

        def test_ace_type_flags_mask_and_sid_changes(self):
            before = descriptor(dacl=acl((ace(),)))
            for value in (ace(kind=1), ace(flags=0x10), ace(mask=2), ace(number=2)):
                with self.subTest(value_hash=hashlib.sha256(value).hexdigest()):
                    result = observe(before, descriptor(dacl=acl((value,))))
                    self.assertTrue(result["valid"])
                    self.assertFalse(result["equal"]["orderedAceBytes"])
            mask_result = observe(before, descriptor(dacl=acl((ace(mask=2),))))
            self.assertEqual(mask_result["before"]["dacl"]["aces"][0]["mask"], 1)
            self.assertEqual(mask_result["after"]["dacl"]["aces"][0]["mask"], 2)
            sid_result = observe(before, descriptor(dacl=acl((ace(number=2),))))
            left_sid = sid_result["before"]["dacl"]["aces"][0]["sid"]
            right_sid = sid_result["after"]["dacl"]["aces"][0]["sid"]
            self.assertEqual(left_sid["length"], 12)
            self.assertNotEqual(left_sid["sha256"], right_sid["sha256"])

        def test_empty_null_and_absent_dacls_are_distinct(self):
            values = [descriptor(dacl=acl()), descriptor(dacl=b""),
                      descriptor(dacl=b"", control=0x8000)]
            for index, left in enumerate(values):
                for right in values[index + 1:]:
                    result = observe(left, right)
                    self.assertTrue(result["valid"])
                    self.assertFalse(result["equal"]["daclBytes"])
            self.assertEqual(observe(values[1], values[2])["before"]["dacl"]["null"], True)
            self.assertEqual(observe(values[1], values[2])["after"]["dacl"]["present"], False)

        def test_missing_owner_group_and_exact_alias(self):
            absent = descriptor(owner=b"", group=b"")
            self.assertTrue(observe(absent, absent)["valid"])
            alias = descriptor(alias=True)
            duplicate = descriptor(group=sid(100))
            result = observe(alias, duplicate)
            self.assertTrue(result["valid"])
            self.assertTrue(result["equal"]["ownerBytes"])
            self.assertTrue(result["equal"]["groupBytes"])
            self.assertFalse(result["equal"]["offsets"])

        def test_descriptor_and_acl_trailing_padding_is_retained(self):
            before = descriptor()
            after = descriptor(tail=b"\0" * 4)
            result = observe(before, after)
            self.assertTrue(result["valid"])
            self.assertFalse(result["equal"]["descriptorBytes"])
            self.assertTrue(result["equal"]["daclBytes"])
            left = descriptor(dacl=acl((ace(),), tail=b"\0" * 4))
            right = descriptor(dacl=acl((ace(),), tail=b"\1" * 4))
            result = observe(left, right)
            self.assertEqual(result["before"]["dacl"]["bytesFree"], 4)
            self.assertFalse(result["equal"]["daclBytes"])
            self.assertTrue(result["equal"]["orderedAceBytes"])

        def test_equal_hashes_do_not_create_equal_components(self):
            with mock.patch.object(diagnostic, "_hash", return_value="0" * 64):
                result = observe(descriptor(), descriptor(owner=sid(300)))
            self.assertFalse(result["equal"]["ownerBytes"])
            self.assertFalse(result["equal"]["descriptorBytes"])

        def test_descriptor_input_type_and_size_caps(self):
            self.invalid(bytearray(descriptor()), "invalid_input_type")
            self.invalid(None, "invalid_input_type")
            at_limit = descriptor() + b"\0" * (diagnostic.MAX_DESCRIPTOR_BYTES - len(descriptor()))
            self.assertTrue(observe(at_limit, at_limit)["valid"])
            self.invalid(at_limit + b"\0", "descriptor_size_limit")

        def test_every_truncated_prefix_is_invalid(self):
            data = descriptor()
            for length in range(len(data)):
                with self.subTest(length=length):
                    result = observe(data[:length], data)
                    self.assertFalse(result["valid"])
                    self.assertIsNone(result["equal"])

        def test_header_revision_and_self_relative_requirements(self):
            self.invalid(change(descriptor(), 0, "B", 2), "unsupported_descriptor_revision")
            self.invalid(change(descriptor(), 2, "<H", 4), "not_self_relative")

        def test_component_offset_bounds(self):
            for offset, reason in ((4, "component_overlaps_header"),
                                   (21, "unaligned_component_offset"),
                                   (0xfffffffc, "truncated_component")):
                with self.subTest(offset=offset):
                    self.invalid(change(descriptor(), 4, "<I", offset), reason)

        def test_sacl_presence_and_offset_refused(self):
            for data in (change(descriptor(), 12, "<I", 0xfffffffc),
                         change(descriptor(), 2, "<H", 0x8014)):
                with mock.patch.object(diagnostic, "_hash", side_effect=AssertionError("must not hash")):
                    summary, raw = diagnostic._observe(data)
                self.assertEqual(summary, {"valid": False, "reason": "unsupported_sacl_presence"})
                self.assertIsNone(raw)

        def test_absent_dacl_with_offset_is_invalid(self):
            self.invalid(change(descriptor(), 2, "<H", 0x8000), "absent_dacl_with_offset")

        def test_sid_header_and_count_bounds(self):
            self.invalid(change(descriptor(), 20, "B", 2), "unsupported_sid_revision")
            self.invalid(change(descriptor(), 21, "B", 16), "sid_subauthority_limit")
            data = descriptor(owner=b"", group=b"", dacl=b"", control=0x8000)
            data = change(data, 4, "<I", 20) + sid()
            self.invalid(change(data, 21, "B", 2), "truncated_sid")

        def test_partial_owner_group_overlap_is_invalid(self):
            outer = bytearray(struct.pack("<BB6sIII", 1, 3, b"\0" * 6, 0, 0, 0))
            outer[4:16] = sid()
            data = struct.pack("<BBHIIII", 1, 0, 0x8000, 20, 24, 0, 0) + bytes(outer)
            self.invalid(data, "overlapping_components")

        def test_sid_dacl_overlap_is_invalid(self):
            body = struct.pack("<BB6s", 1, 2, b"\0" * 6) + acl()
            data = struct.pack("<BBHIIII", 1, 0, 0x8004, 20, 0, 0, 28) + body
            self.invalid(data, "overlapping_components")

        def test_acl_revision_reserved_and_size_bounds(self):
            data = descriptor()
            offset = struct.unpack_from("<I", data, 16)[0]
            for delta, form, value, reason in (
                (0, "B", 3, "unsupported_acl_revision"),
                (1, "B", 1, "nonzero_acl_reserved"),
                (6, "<H", 1, "nonzero_acl_reserved"),
                (2, "<H", 7, "invalid_acl_size"),
                (2, "<H", 10, "invalid_acl_size"),
                (2, "<H", 65532, "truncated_acl"),
            ):
                with self.subTest(reason=reason, value=value):
                    self.invalid(change(data, offset + delta, form, value), reason)

        def test_ace_count_bound(self):
            at_limit = descriptor(dacl=acl((ace(),) * diagnostic.MAX_ACES))
            self.assertTrue(observe(at_limit, at_limit)["valid"])
            over = descriptor(dacl=acl((ace(),) * (diagnostic.MAX_ACES + 1)))
            self.invalid(over, "ace_count_limit")
            record = message_record(diagnostic.comparison_message(at_limit, at_limit))
            self.assertTrue(record["valid"])

        def test_ace_bounds_and_truncated_headers(self):
            data = descriptor(dacl=acl((ace(),)))
            offset = struct.unpack_from("<I", data, 16)[0]
            for size, reason in ((0, "invalid_ace_size"), (6, "invalid_ace_size"),
                                 (65532, "truncated_ace")):
                with self.subTest(size=size):
                    self.invalid(change(data, offset + 10, "<H", size), reason)
            self.invalid(change(data, offset + 4, "<H", 2), "truncated_ace_header")
            empty = descriptor(dacl=acl())
            self.invalid(change(empty, struct.unpack_from("<I", empty, 16)[0] + 4,
                                "<H", 1), "truncated_ace_header")

        def test_unsupported_ace_payload_not_exported(self):
            payload = b"NONPUBLIC_RAW_SENTINEL_123456789"  # 32 bytes, DWORD aligned.
            self.assertEqual(len(payload) % 4, 0)
            opaque = struct.pack("<BBH", 0xff, 0, 4 + len(payload)) + payload
            data = descriptor(dacl=acl((opaque,)))
            output = diagnostic.comparison_message(data, data)
            self.assertNotIn(payload.decode(), output)
            self.assertNotIn(data.hex(), output)
            self.assertNotIn(sid(100).hex(), output)
            result = message_record(output)
            self.assertFalse(result["valid"])
            self.assertEqual(result["before"]["reason"], "unsupported_ace_type")
            self.assertEqual(result["accessEquivalence"], "not_assessed")

        def test_allow_deny_ace_sid_bounds(self):
            data = descriptor(dacl=acl((ace(),)))
            start = struct.unpack_from("<I", data, 16)[0] + 8
            for delta, form, value, reason in (
                (8, "B", 2, "unsupported_ace_sid_revision"),
                (9, "B", 16, "ace_sid_subauthority_limit"),
                (9, "B", 2, "invalid_ace_sid_size"),
                (2, "<H", 4, "truncated_ace_sid"),
            ):
                with self.subTest(reason=reason):
                    self.invalid(change(data, start + delta, form, value), reason)

        def test_output_cap_is_explicit_invalid_not_truncated_json(self):
            with mock.patch.object(diagnostic, "MAX_JSON_CHARS", 20):
                result = message_record(diagnostic.comparison_message(descriptor(), descriptor()))
            self.assertFalse(result["valid"])
            self.assertEqual(result["reason"], "diagnostic_size_limit")
            self.assertIsNone(result["equal"])

        def test_malformed_input_still_preserves_original_assertion(self):
            result = self.hook_failure(descriptor(), b"bad")
            self.assertFalse(result["valid"])
            self.assertEqual(result["after"]["reason"], "truncated_descriptor_header")

        def test_formatter_failure_preserves_assertion_and_hides_exception(self):
            for error in (RuntimeError("PRIVATE_ERROR"), KeyboardInterrupt("PRIVATE_ERROR")):
                with self.subTest(kind=type(error).__name__):
                    with mock.patch.object(diagnostic, "comparison_message", side_effect=error):
                        result = self.hook_failure(descriptor(), descriptor(owner=sid(99)))
                    self.assertEqual(result["reason"], "observer_failure")
                    self.assertNotIn("PRIVATE_ERROR", json.dumps(result))

        def test_serialization_failure_preserves_assertion(self):
            with mock.patch.object(diagnostic.json, "dumps", side_effect=ValueError("PRIVATE_ERROR")):
                result = self.hook_failure(descriptor(), descriptor(owner=sid(99)))
            self.assertFalse(result["valid"])
            self.assertEqual(result["reason"], "observer_failure")

        def test_import_failure_preserves_assertion(self):
            import builtins
            original = builtins.__import__
            def failing_import(name, *args, **kwargs):
                if name == "acl_descriptor_diagnostic":
                    raise ImportError("PRIVATE_ERROR")
                return original(name, *args, **kwargs)
            with mock.patch.object(builtins, "__import__", side_effect=failing_import):
                result = self.hook_failure(descriptor(), descriptor(owner=sid(99)))
            self.assertEqual(result["reason"], "observer_failure")

        def test_bad_formatter_output_preserves_assertion(self):
            for value in (None, b"bad", "x" * 32801, "", "invalid",
                          diagnostic.MARKER + '{"truncated":'):
                with self.subTest(kind=type(value).__name__):
                    with mock.patch.object(diagnostic, "comparison_message", return_value=value):
                        result = self.hook_failure(descriptor(), descriptor(owner=sid(99)))
                    self.assertEqual(result["reason"], "observer_failure")

        def test_closed_schema_valid_and_invalid_observations(self):
            for before, after in ((descriptor(), descriptor(owner=sid(99))),
                                  (descriptor(), b"bad"),
                                  (b"bad", b"also bad"),
                                  (descriptor(), descriptor(dacl=b"")),
                                  (descriptor(alias=True), descriptor(group=sid(100))),
                                  (descriptor(owner=b"", group=b""), descriptor()),
                                  (descriptor(dacl=b"", control=0x8000), descriptor())):
                with self.subTest(before_hash=hashlib.sha256(before).hexdigest()):
                    message = diagnostic.comparison_message(before, after)
                    result = diagnostic.validate_comparison_message(message, require_mismatch=True)
                    self.assertEqual(result, message_record(message))

        def test_unknown_keys_and_arbitrary_strings_are_not_exported(self):
            base = observe(descriptor(), descriptor(owner=sid(99)))
            for path, key, value in (
                ((), "raw", "NONPUBLIC_RAW_SENTINEL"),
                (("before",), "rawSid", "NONPUBLIC_RAW_SENTINEL"),
                (("before", "owner"), "sidString", "NONPUBLIC_RAW_SENTINEL"),
                (("before", "dacl", "aces", 0), "raw", "NONPUBLIC_RAW_SENTINEL"),
                (("before",), "sha256", "NONPUBLIC_RAW_SENTINEL"),
                (("before",), "controlFlags", ["NONPUBLIC_RAW_SENTINEL"]),
                ((), "accessEquivalence", "equivalent"),
                ((), "schema", "NONPUBLIC_RAW_SENTINEL"),
            ):
                with self.subTest(path=path, key=key):
                    bad = copy.deepcopy(base)
                    target = bad
                    for item in path:
                        target = target[item]
                    target[key] = value
                    self.rejected_record(bad)
            self.rejected_record({"schema": diagnostic.SCHEMA, "valid": False,
                                  "equal": None, "reason": "NONPUBLIC_RAW_SENTINEL",
                                  "accessEquivalence": "not_assessed"})

        def test_boolean_values_cannot_replace_integers(self):
            base = observe(descriptor(), descriptor(owner=sid(99)))
            paths = (("before", "length"), ("before", "revision"),
                     ("before", "sbz1"), ("before", "control"),
                     ("before", "offsets", "sacl"),
                     ("before", "owner", "length"),
                     ("before", "dacl", "aceCount"),
                     ("before", "dacl", "bytesFree"),
                     ("before", "dacl", "aces", 0, "index"),
                     ("before", "dacl", "aces", 0, "type"),
                     ("before", "dacl", "aces", 0, "mask"),
                     ("before", "dacl", "aces", 0, "sid", "length"))
            for path in paths:
                with self.subTest(path=path):
                    bad = copy.deepcopy(base)
                    target = bad
                    for item in path[:-1]:
                        target = target[item]
                    target[path[-1]] = False if target[path[-1]] == 0 else True
                    self.rejected_record(bad)

        def test_integers_cannot_replace_boolean_values(self):
            base = observe(descriptor(), descriptor(owner=sid(99)))
            for path in (("valid",), ("before", "valid"),
                         ("before", "owner", "present"),
                         ("before", "dacl", "present"),
                         ("before", "dacl", "null"),
                         ("equal", "control")):
                with self.subTest(path=path):
                    bad = copy.deepcopy(base)
                    target = bad
                    for item in path[:-1]:
                        target = target[item]
                    target[path[-1]] = int(target[path[-1]])
                    self.rejected_record(bad)

        def test_duplicate_keys_and_noncanonical_json_rejected(self):
            good = diagnostic.comparison_message(descriptor(), descriptor(owner=sid(99)))
            encoded = good[len(diagnostic.MARKER):]
            bad_values = (
                diagnostic.MARKER + '{"schema":"' + diagnostic.SCHEMA + '",' + encoded[1:],
                good.replace('"length":12', '"length":12,"length":12', 1),
                diagnostic.MARKER + " " + encoded,
                good + " ", good + "NONPUBLIC_RAW_SENTINEL",
                good.replace('"sbz1":0', '"sbz1":NaN', 1),
                good.replace('"sbz1":0', '"sbz1":0.0', 1),
                good[:-1],
            )
            for bad in bad_values:
                with self.subTest(digest=hashlib.sha256(bad.encode()).hexdigest()):
                    with self.assertRaises(ValueError):
                        diagnostic.validate_comparison_message(bad, require_mismatch=True)
                    with mock.patch.object(diagnostic, "comparison_message", return_value=bad):
                        result = self.hook_failure(descriptor(), descriptor(owner=sid(99)))
                    self.assertEqual(result["reason"], "observer_failure")

        def test_inconsistent_schema_and_out_of_range_fields_rejected(self):
            base = observe(descriptor(), descriptor(owner=sid(99)))
            mutations = (
                (("before", "controlFlags"), []),
                (("before", "offsets", "owner"), 4),
                (("before", "dacl", "bytesFree"), 1),
                (("before", "dacl", "aces", 0, "mask"), 0x100000000),
                (("before", "dacl", "aces", 0, "flags"), 256),
                (("before", "dacl", "aces", 0, "type"), 2),
                (("before", "dacl", "aces", 0, "size"), 24),
                (("equal", "control"), False),
                (("equal", "ownerBytes"), True),
                (("valid",), False),
                (("equal", "descriptorBytes"), True),
            )
            for path, value in mutations:
                with self.subTest(path=path):
                    bad = copy.deepcopy(base)
                    target = bad
                    for item in path[:-1]:
                        target = target[item]
                    target[path[-1]] = value
                    self.rejected_record(bad)

        def test_validator_failure_preserves_assertion(self):
            with mock.patch.object(diagnostic, "validate_comparison_message", side_effect=RuntimeError("PRIVATE_ERROR")):
                result = self.hook_failure(descriptor(), descriptor(owner=sid(99)))
            self.assertEqual(result["reason"], "observer_failure")

        def test_successful_exact_restore_never_calls_observer(self):
            data = descriptor()
            backend = object.__new__(native_permissions._WindowsAcl)
            with mock.patch.object(backend, "capture", return_value={"descriptor": data}) as capture:
                with mock.patch.object(diagnostic, "comparison_message", side_effect=AssertionError("not called")):
                    result = backend.check_restored(Path("owned-fixture"), {"descriptor": data})
            self.assertTrue(result["exact"])
            capture.assert_called_once_with(Path("owned-fixture"))

        def test_original_ast_is_unchanged_except_additive_hook(self):
            path = Path(native_permissions.__file__)
            tree = ast.parse(path.read_bytes())
            helpers = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                       and node.name == "_acl_mismatch_diagnostic"]
            self.assertEqual(len(helpers), 1)
            tree.body.remove(helpers[0])
            cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "_WindowsAcl")
            check = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "check_restored")
            assertion = next(node for node in check.body if isinstance(node, ast.Assert))
            self.assertIsInstance(assertion.msg, ast.BinOp)
            self.assertIsInstance(assertion.msg.op, ast.Add)
            self.assertEqual(assertion.msg.right.func.id, "_acl_mismatch_diagnostic")
            assertion.msg = assertion.msg.left
            # Avoid ast.dump formatting changes across Python versions. Only
            # exclude the empty type_params grammar field introduced in 3.12.
            def normalize(value):
                if isinstance(value, ast.AST):
                    if "type_params" in value._fields:
                        self.assertEqual(value.type_params, [])
                    return [type(value).__name__, [[name, normalize(child)]
                            for name, child in ast.iter_fields(value) if name != "type_params"]]
                if isinstance(value, list):
                    return [normalize(item) for item in value]
                return [type(value).__name__, repr(value)]
            encoded = json.dumps(normalize(tree), separators=(",", ":"), ensure_ascii=True).encode()
            actual = hashlib.sha256(encoded).hexdigest()
            self.assertEqual(actual, BASE_AST_SHA256)

        def test_parser_imports_only_pure_standard_library(self):
            tree = ast.parse(Path(diagnostic.__file__).read_bytes())
            imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                       for alias in node.names]
            self.assertEqual(imports, ["hashlib", "json", "struct"])
            self.assertFalse(any(isinstance(node, ast.ImportFrom) for node in ast.walk(tree)))

    return unittest.defaultTestLoader.loadTestsFromTestCase(DescriptorControls)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(build_suite())
    text = output.getvalue()
    print(text, end="")
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps({
            "kind": "synthetic_descriptor_controls_not_windows_execution",
            "python": sys.version,
            "tests": result.testsRun, "failures": len(result.failures),
            "errors": len(result.errors), "skipped": len(result.skipped),
            "success": result.wasSuccessful(), "output": text,
        }, indent=2) + "\n")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())

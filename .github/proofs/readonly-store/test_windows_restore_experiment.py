"""Portable restore-only controls; no Windows API, permission or native proof.

Run python test_windows_restore_experiment.py --receipt evidence/controls.json.
ctypes only allocates local buffers. Every DLL/function is a Python fake. The
inherited descriptor suite runs unchanged except its obsolete whole-module AST
pin: a stronger exact parent-relative AST control replaces that single case.
No unittest class exists at module scope, keeping hosted collection unchanged.
"""
import argparse
import ast
from contextlib import ExitStack
import ctypes
from ctypes import wintypes
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import weakref

import native_permissions as native
import test_acl_descriptor_diagnostic as inherited

ROOT = Path(__file__).resolve().parent
BASE_SHA = 'b1c8c63622989ebcd8739e48f1d2f10c5a843c425ad4f2795484b47c8b9212eb'
BASE_MANIFEST_SHA = 'e7752bc60c5ef78877021767b16977f870f7a2471227a4edb4b08abdc5e4c743'


class FakeFunction:
    def __init__(self, name, callback=None):
        self.name, self.callback, self.calls = name, callback, []

    def __call__(self, *args):
        self.calls.append(args)
        if self.callback is None:
            raise AssertionError('Unexpected simulated API call: ' + self.name)
        return self.callback(*args)


class FakeDll:
    def __init__(self):
        self.functions = {}

    def __getattr__(self, name):
        return self.functions.setdefault(name, FakeFunction(name))


def adapter(callback):
    """Run the exact constructor against fake libraries, preserving bindings."""
    advapi, kernel = FakeDll(), FakeDll()
    advapi.SetFileSecurityW.callback = callback
    def get_dacl(buffer, present, dacl, defaulted):
        header = ctypes.string_at(buffer, 20)
        control = int.from_bytes(header[2:4], 'little')
        offset = int.from_bytes(header[16:20], 'little')
        present._obj.value = bool(control & 4)
        defaulted._obj.value = bool(control & 8)
        dacl._obj.value = ctypes.addressof(buffer) + offset if offset else None
        return 1
    advapi.GetSecurityDescriptorDacl.callback = get_dacl
    loads = []

    def dll(name, **kwargs):
        loads.append((name, kwargs))
        assert kwargs == {'use_last_error': True}
        return {'advapi32': advapi, 'kernel32': kernel}[name]

    with mock.patch.object(ctypes, 'WinDLL', dll, create=True), mock.patch.object(
            native._WindowsAcl, '_token_sids', return_value=(b'user', b'owner')):
        backend = native._WindowsAcl()
    return backend, advapi, kernel, loads


class FixtureReplay:
    """Real disposable path identities, fake captures/restrictions/Win32 calls."""
    def __init__(self, root, count=2):
        self.root = root
        self.files = tuple(root / ('file%d.db' % i) for i in range(count))
        for path in self.files:
            path.write_bytes(b'fixture-data')
        self.events = []
        self.fail_restore = {}
        self.fail_check = {}
        self.fail_restrict = {}
        self.fail_capture = {}
        self.checking = False
        self.descriptor = inherited.descriptor()
        self.backend, self.advapi, self.kernel, self.loads = adapter(self.set_file)
        self.backend.capture = self.capture
        self.backend.restrict = self.restrict
        self.owner = b'user'
        self.fixture = native.OwnedPermissionDirectory(root, native._identity(root))

    @property
    def paths(self):
        return (self.root, *self.files)

    def capture(self, path):
        phase = 'check' if self.checking else 'capture'
        self.events.append((phase, path))
        failure = self.fail_check.get(path) if self.checking else self.fail_capture.get(path)
        if failure:
            raise failure
        return {'descriptor': self.descriptor, 'control': 0x8004, 'owner': self.owner}

    def restrict(self, path, saved, *, directory):
        self.events.append(('restrict', path))
        if path in self.fail_restrict:
            raise self.fail_restrict[path]

    def set_file(self, path, information, buffer):
        path = Path(path)
        self.events.append(('restore', path))
        assert information == 4
        assert ctypes.string_at(buffer, len(self.descriptor)) == self.descriptor
        self.checking = True
        if path in self.fail_restore:
            raise self.fail_restore[path]
        return 1

    def patches(self):
        stack = ExitStack()
        stack.enter_context(mock.patch.object(native, 'os', SimpleNamespace(name='nt')))
        stack.enter_context(mock.patch.object(native, '_WindowsAcl', return_value=self.backend))
        stack.enter_context(mock.patch.object(self.fixture, '_prove_readonly', return_value=None))
        return stack

    def run(self, *, files=None, body=None):
        with self.patches():
            with self.fixture.restrict_readonly(self.files if files is None else files):
                if body:
                    body()


def module_delta_check(case):
    before_path = ROOT / 'fixtures/native_permissions.diagnostic-base.py'
    case.assertEqual(hashlib.sha256(before_path.read_bytes()).hexdigest(), BASE_SHA)
    before = ast.parse(before_path.read_bytes())
    after = ast.parse((ROOT / 'native_permissions.py').read_bytes())
    cls = next(n for n in after.body if isinstance(n, ast.ClassDef) and n.name == '_WindowsAcl')
    original_cls = next(n for n in before.body if isinstance(n, ast.ClassDef) and n.name == '_WindowsAcl')
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
    sig = next(n.value for n in init.body if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == 'signatures' for t in n.targets))
    positions = [i for i, key in enumerate(sig.keys) if isinstance(key, ast.Constant)
                 and key.value == 'SetFileSecurityW']
    case.assertEqual(len(positions), 1)
    index = positions[0]
    case.assertEqual(ast.dump(sig.values[index]), ast.dump(ast.parse(
        '(boolean, [wintypes.LPCWSTR, dword, ptr])', mode='eval').body))
    del sig.keys[index], sig.values[index]
    restore = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'restore')
    expected = ast.parse('''def restore(self, path, saved):
    descriptor = self.ctypes.create_string_buffer(saved["descriptor"])
    self._dacl(descriptor)
    self._ok(self.advapi.SetFileSecurityW(str(path), self.DACL, descriptor))
''').body[0]
    case.assertEqual(ast.dump(restore), ast.dump(expected))
    cls.body[cls.body.index(restore)] = next(n for n in original_cls.body
        if isinstance(n, ast.FunctionDef) and n.name == 'restore')
    case.assertEqual(ast.dump(after), ast.dump(before))


def build_suite():
    class RestoreControls(unittest.TestCase):
        def setUp(self):
            self.temp = tempfile.TemporaryDirectory(prefix='acl-restore-portable-')
            self.addCleanup(self.temp.cleanup)
            self.root = Path(self.temp.name)

        def test_exact_binding_and_no_constructor_native_calls(self):
            backend, advapi, kernel, loads = adapter(lambda *args: 1)
            self.assertIs(advapi.SetFileSecurityW.restype, wintypes.BOOL)
            self.assertEqual(advapi.SetFileSecurityW.argtypes,
                             [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p])
            self.assertEqual(loads, [('advapi32', {'use_last_error': True}),
                                    ('kernel32', {'use_last_error': True})])
            self.assertTrue(all(not f.calls for dll in (advapi, kernel)
                                for f in dll.functions.values()))
            self.assertIs(backend.ctypes, ctypes)

        def check_buffer(self, raw, *, saved_control=0):
            observed = []
            path = self.root / 'wide-\u96ea.db'
            saved = {'descriptor': raw, 'control': saved_control, 'owner': b'user'}
            before = saved.copy()

            def callback(actual_path, information, buffer):
                self.assertEqual(actual_path, str(path))
                self.assertEqual(type(information), int)
                self.assertEqual(information, 4)
                self.assertIsInstance(buffer, ctypes.Array)
                self.assertEqual(ctypes.sizeof(buffer), len(raw) + 1)
                self.assertEqual(ctypes.string_at(buffer, len(raw)), raw)
                self.assertEqual(buffer[len(raw)], b'\0')
                observed.append((weakref.ref(buffer), ctypes.addressof(buffer)))
                import gc
                gc.collect()
                self.assertIs(observed[-1][0](), buffer)
                return 7  # BOOL accepts any nonzero; DWORD status would fail.

            backend, advapi, kernel, unused = adapter(callback)
            backend.restore(path, saved)
            self.assertEqual(saved, before)
            self.assertIs(saved['descriptor'], raw)
            self.assertEqual(len(observed), 1)
            self.assertEqual(len(advapi.SetFileSecurityW.calls), 1)
            self.assertEqual(len(advapi.GetSecurityDescriptorDacl.calls), 1)
            self.assertIs(advapi.GetSecurityDescriptorDacl.calls[0][0],
                          advapi.SetFileSecurityW.calls[0][2])
            self.assertTrue(all(not f.calls for name, f in advapi.functions.items()
                                if name not in {'SetFileSecurityW', 'GetSecurityDescriptorDacl'}))
            self.assertTrue(all(not f.calls for f in kernel.functions.values()))
            advapi.SetFileSecurityW.calls.clear()  # The fakes must not retain args.
            advapi.GetSecurityDescriptorDacl.calls.clear()
            import gc
            gc.collect()
            self.assertIsNone(observed[0][0]())

        def test_all_protected_auto_inherited_control_combinations(self):
            for control in (0x8004, 0x8404, 0x9004, 0x9404):
                with self.subTest(control=control):
                    self.check_buffer(inherited.descriptor(control=control), saved_control=control)

        def test_saved_header_not_rebuilt_from_separate_control_field(self):
            self.check_buffer(inherited.descriptor(control=0x9404), saved_control=0)

        def test_ordered_explicit_and_inherited_aces_preserved(self):
            entries = (inherited.ace(5, kind=1, flags=0, mask=0x10116),
                       inherited.ace(7, flags=0x13, mask=0x120089),
                       inherited.ace(9, kind=1, flags=0x10, mask=0x10000))
            for order in (entries, entries[::-1]):
                self.check_buffer(inherited.descriptor(dacl=inherited.acl(order)))

        def test_offsets_alias_and_padding_passed_unchanged(self):
            self.check_buffer(inherited.descriptor(order=('dacl', 'group', 'owner'), tail=b'\0'*8))
            self.check_buffer(inherited.descriptor(alias=True))

        def test_false_return_uses_last_error_once_without_fallback(self):
            backend, advapi, kernel, unused = adapter(lambda *args: 0)
            error = OSError('simulated Windows failure')
            with mock.patch.object(ctypes, 'get_last_error', return_value=5, create=True) as last:
                with mock.patch.object(ctypes, 'WinError', return_value=error, create=True) as win:
                    with self.assertRaises(OSError) as caught:
                        backend.restore(self.root, {'descriptor': inherited.descriptor()})
            self.assertIs(caught.exception, error)
            last.assert_called_once_with(); win.assert_called_once_with(5)
            self.assertEqual(len(advapi.SetFileSecurityW.calls), 1)
            self.assertTrue(all(not f.calls for n, f in advapi.functions.items() if n not in {'SetFileSecurityW', 'GetSecurityDescriptorDacl'}))

        def test_native_wrapper_exception_preserved_without_retry(self):
            error = RuntimeError('simulated uncertain call failure')
            def callback(*args):
                raise error
            backend, advapi, kernel, unused = adapter(callback)
            with self.assertRaises(RuntimeError) as caught:
                backend.restore(self.root, {'descriptor': inherited.descriptor()})
            self.assertIs(caught.exception, error)
            self.assertEqual(len(advapi.SetFileSecurityW.calls), 1)

        def test_buffer_allocation_failure_makes_no_api_call(self):
            backend, advapi, kernel, unused = adapter(lambda *args: 1)
            with mock.patch.object(ctypes, 'create_string_buffer', side_effect=MemoryError):
                with self.assertRaises(MemoryError):
                    backend.restore(self.root, {'descriptor': inherited.descriptor()})
            self.assertFalse(advapi.SetFileSecurityW.calls)

        def test_absent_dacl_refused_before_write(self):
            backend, advapi, kernel, unused = adapter(lambda *args: 1)
            raw = inherited.descriptor(control=0x8000, dacl=b'')
            with self.assertRaisesRegex(AssertionError, 'real, present fixture DACL'):
                backend.restore(self.root, {'descriptor': raw})
            self.assertEqual(len(advapi.GetSecurityDescriptorDacl.calls), 1)
            self.assertFalse(advapi.SetFileSecurityW.calls)

        def test_null_dacl_refused_before_write(self):
            backend, advapi, kernel, unused = adapter(lambda *args: 1)
            raw = inherited.descriptor(control=0x8004, dacl=b'')
            with self.assertRaisesRegex(AssertionError, 'real, present fixture DACL'):
                backend.restore(self.root, {'descriptor': raw})
            self.assertEqual(len(advapi.GetSecurityDescriptorDacl.calls), 1)
            self.assertFalse(advapi.SetFileSecurityW.calls)

        def test_failed_dacl_query_uses_last_error_and_never_writes(self):
            backend, advapi, kernel, unused = adapter(lambda *args: 1)
            advapi.GetSecurityDescriptorDacl.callback = lambda *args: 0
            error = OSError('simulated DACL query failure')
            with mock.patch.object(ctypes, 'get_last_error', return_value=1338, create=True) as last:
                with mock.patch.object(ctypes, 'WinError', return_value=error, create=True) as win:
                    with self.assertRaises(OSError) as caught:
                        backend.restore(self.root, {'descriptor': inherited.descriptor()})
            self.assertIs(caught.exception, error)
            last.assert_called_once_with(); win.assert_called_once_with(1338)
            self.assertEqual(len(advapi.GetSecurityDescriptorDacl.calls), 1)
            self.assertFalse(advapi.SetFileSecurityW.calls)

        def test_dacl_query_exception_never_writes(self):
            backend, advapi, kernel, unused = adapter(lambda *args: 1)
            error = RuntimeError('simulated DACL wrapper failure')
            def fail(*args):
                raise error
            advapi.GetSecurityDescriptorDacl.callback = fail
            with self.assertRaises(RuntimeError) as caught:
                backend.restore(self.root, {'descriptor': inherited.descriptor()})
            self.assertIs(caught.exception, error)
            self.assertEqual(len(advapi.GetSecurityDescriptorDacl.calls), 1)
            self.assertFalse(advapi.SetFileSecurityW.calls)

        def test_exact_parent_ast_except_two_authorized_changes(self):
            module_delta_check(self)

        def test_frozen_source_inventory_unchanged_except_native_permissions(self):
            manifest = ROOT / 'diagnostic-base-manifest.json'
            self.assertEqual(hashlib.sha256(manifest.read_bytes()).hexdigest(), BASE_MANIFEST_SHA)
            rows = json.loads(manifest.read_text())['files']
            for row in rows:
                if row['path'] != 'native_permissions.py':
                    self.assertEqual(hashlib.sha256((ROOT / row['path']).read_bytes()).hexdigest(), row['sha256'])

        def test_success_restore_order_and_full_check_all_paths(self):
            replay = FixtureReplay(self.root)
            replay.run()
            expected = [('capture', p) for p in replay.paths]
            expected += [('restrict', p) for p in (*replay.files, replay.root)]
            expected += [('restore', p) for p in replay.paths]
            expected += [('check', p) for p in replay.paths]
            self.assertEqual(replay.events, expected)
            self.assertTrue(replay.fixture.restored)
            self.assertEqual(len(replay.fixture.restoration_evidence), 3)

        def test_all_target_restoration_and_errors_in_original_order(self):
            replay = FixtureReplay(self.root)
            errors = [RuntimeError('first restore'), ValueError('last restore'), AssertionError('middle readback')]
            replay.fail_restore = {replay.root: errors[0], replay.files[1]: errors[1]}
            replay.fail_check = {replay.files[0]: errors[2]}
            with self.assertRaises(BaseExceptionGroup) as caught:
                replay.run()
            self.assertEqual(list(caught.exception.exceptions), errors)
            self.assertEqual([p for event, p in replay.events if event == 'restore'], list(replay.paths))
            self.assertEqual([p for event, p in replay.events if event == 'check'], list(replay.paths))
            self.assertFalse(replay.fixture.restored)

        def test_partial_restriction_failure_still_restores_every_target(self):
            replay = FixtureReplay(self.root)
            error = RuntimeError('uncertain restriction failure')
            replay.fail_restrict[replay.files[0]] = error
            with self.assertRaises(RuntimeError) as caught:
                replay.run()
            self.assertIs(caught.exception, error)
            self.assertEqual([p for e, p in replay.events if e == 'restore'], list(replay.paths))
            self.assertEqual([p for e, p in replay.events if e == 'check'], list(replay.paths))
            self.assertTrue(replay.fixture.restored)

        def test_body_error_retained_after_successful_all_path_restore(self):
            replay = FixtureReplay(self.root)
            error = KeyboardInterrupt('simulated body interruption')
            def body():
                raise error
            with self.assertRaises(KeyboardInterrupt) as caught:
                replay.run(body=body)
            self.assertIs(caught.exception, error)
            self.assertTrue(replay.fixture.restored)
            self.assertEqual([p for e, p in replay.events if e == 'restore'], list(replay.paths))

        def test_restore_error_group_keeps_body_exception_context(self):
            replay = FixtureReplay(self.root)
            body_error = RuntimeError('body')
            restore_error = OSError('restore')
            replay.fail_restore[replay.root] = restore_error
            def body():
                raise body_error
            with self.assertRaises(BaseExceptionGroup) as caught:
                replay.run(body=body)
            self.assertEqual(list(caught.exception.exceptions), [restore_error])
            self.assertIs(caught.exception.__context__, body_error)

        def test_owner_refusal_before_any_mutation(self):
            replay = FixtureReplay(self.root)
            replay.owner = b'other-owner'
            with self.assertRaisesRegex(AssertionError, 'owner differs'):
                replay.run()
            self.assertEqual(replay.events, [('capture', p) for p in replay.paths])
            self.assertFalse(replay.advapi.SetFileSecurityW.calls)

        def test_capture_failure_before_any_mutation(self):
            replay = FixtureReplay(self.root)
            replay.fail_capture[replay.files[0]] = RuntimeError('capture')
            with self.assertRaisesRegex(RuntimeError, 'capture'):
                replay.run()
            self.assertFalse(any(e in {'restrict', 'restore'} for e, p in replay.events))

        def test_root_identity_refusal_before_any_mutation(self):
            replay = FixtureReplay(self.root)
            replay.fixture.identity = (-1, -1)
            with self.assertRaisesRegex(AssertionError, 'directory was replaced'):
                replay.run()
            self.assertEqual(replay.events, [])

        def test_duplicate_files_refused_before_capture(self):
            replay = FixtureReplay(self.root)
            with self.assertRaises(AssertionError):
                replay.run(files=(replay.files[0], replay.files[0]))
            self.assertEqual(replay.events, [])

        def test_unexpected_entry_refused_before_capture(self):
            replay = FixtureReplay(self.root)
            (self.root / 'extra').write_bytes(b'extra')
            with self.assertRaisesRegex(AssertionError, 'Unexpected fixture entries'):
                replay.run()
            self.assertEqual(replay.events, [])

        def test_outside_path_refused_before_capture(self):
            replay = FixtureReplay(self.root)
            outside = self.root.parent / 'not-owned.db'
            with mock.patch.object(Path, 'iterdir', return_value=iter((outside,))):
                with self.assertRaisesRegex(AssertionError, 'outside the owned fixture'):
                    replay.run(files=(outside,))
            self.assertEqual(replay.events, [])

        def test_links_and_reparse_points_refused(self):
            for mode, attributes in ((0o120777, 0), (0o100600, 0x400)):
                with self.subTest(mode=mode, attributes=attributes):
                    fake = SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
                    with mock.patch.object(Path, 'lstat', return_value=fake):
                        with self.assertRaisesRegex(AssertionError, 'links/reparse'):
                            native._identity(self.root)

        def test_multiple_link_file_refused_before_capture(self):
            replay = FixtureReplay(self.root)
            original = Path.lstat
            def lstat(path):
                actual = original(path)
                if path == replay.files[0]:
                    return SimpleNamespace(st_mode=actual.st_mode, st_dev=actual.st_dev,
                        st_ino=actual.st_ino, st_nlink=2)
                return actual
            with mock.patch.object(Path, 'lstat', lstat):
                with self.assertRaises(AssertionError):
                    replay.run()
            self.assertEqual(replay.events, [])

        def test_identity_change_skips_only_refused_target_and_keeps_all_checks(self):
            replay = FixtureReplay(self.root)
            original = native._identity
            changed = False
            def identity(path):
                if changed and path == replay.root:
                    return (-1, -1)
                return original(path)
            def body():
                nonlocal changed
                changed = True
            with mock.patch.object(native, '_identity', identity):
                with self.assertRaises(BaseExceptionGroup) as caught:
                    replay.run(body=body)
            self.assertEqual(len(caught.exception.exceptions), 2)
            self.assertTrue(all(isinstance(e, AssertionError) for e in caught.exception.exceptions))
            self.assertEqual([p for e, p in replay.events if e == 'restore'], list(replay.files))
            self.assertEqual([p for e, p in replay.events if e == 'check'], list(replay.files))
            self.assertFalse(replay.fixture.restored)

        def test_exact_readback_mismatch_remains_decisive(self):
            raw = inherited.descriptor(control=0x8004)
            changed = inherited.descriptor(control=0x8404)
            backend, advapi, kernel, unused = adapter(lambda *args: 1)
            backend.capture = lambda path: {'descriptor': changed}
            backend.restore(self.root, {'descriptor': raw})
            with self.assertRaisesRegex(AssertionError, 'not restored byte-for-byte') as caught:
                backend.check_restored(self.root, {'descriptor': raw})
            payload = str(caught.exception).split('; ACL_DESCRIPTOR_COMPARISON_V1=', 1)[1]
            record = inherited.diagnostic.validate_comparison_message(
                'ACL_DESCRIPTOR_COMPARISON_V1=' + payload)
            self.assertFalse(record['equal']['descriptorBytes'])
            self.assertFalse(record['equal']['control'])
            self.assertEqual(record['accessEquivalence'], 'not_assessed')

        def test_failed_fixture_not_deleted_or_replaced(self):
            with mock.patch.object(native.shutil, 'rmtree') as remove:
                with native.owned_permission_directory(self.root) as fixture:
                    fixture.restored = False
                remove.assert_not_called()
                self.assertTrue(fixture.root.is_dir())

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RestoreControls)
    replaced = []
    for test in inherited.build_suite():
        if test._testMethodName == 'test_original_ast_is_unchanged_except_additive_hook':
            replaced.append(test._testMethodName)
        else:
            suite.addTest(test)
    assert replaced == ['test_original_ast_is_unchanged_except_additive_hook']
    return suite


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    forbidden_native_loads = []
    def guard(event, arguments):
        if event == 'ctypes.dlopen':
            forbidden_native_loads.append(event)
            raise RuntimeError('Native library loads forbidden during portable controls')
    sys.addaudithook(guard)
    output = io.StringIO()
    suite = build_suite()
    expected = suite.countTestCases()
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    rendered = output.getvalue()
    print(rendered, end='')
    success = result.wasSuccessful() and result.testsRun == expected and not result.skipped and not forbidden_native_loads
    receipt = {'kind': 'portable_restore_experiment_controls_not_windows_execution',
        'python': sys.version, 'hostPlatform': sys.platform, 'success': success,
        'tests': result.testsRun, 'expectedTests': expected, 'failures': len(result.failures),
        'errors': len(result.errors), 'skipped': len(result.skipped),
        'windowsApiCalls': 0, 'permissionChanges': 0,
        'blockedNativeLoadEvents': forbidden_native_loads,
        'sourceSha256': hashlib.sha256((ROOT / 'native_permissions.py').read_bytes()).hexdigest(),
        'controlSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'inheritedControls': 39,
        'replacedInheritedControl': 'test_original_ast_is_unchanged_except_additive_hook',
        'replacement': 'test_exact_parent_ast_except_two_authorized_changes',
        'limitations': ['All Windows functions are Python fakes; Windows ABI and filesystem semantics are not exercised.',
            'Native protected/unprotected and AUTO_INHERITED round trips remain unproven.',
            'ctypes buffer allocation/lifetime is real and local; native pointer consumption is simulated.',
            'No owner/group/SACL writes, privilege changes, fallback, retry or baseline normalization.'],
        'output': rendered}
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())

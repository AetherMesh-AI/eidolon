#!/usr/bin/env python3
"""Standalone deterministic constructor/adapter replay, NOT a Windows OS test.

Run: python test_windows_binding.py --receipt evidence/windows-binding.json

The pinned official ctypes files are parsed, never imported. Only their exact
CDLL.__init__ AST nodes run, with controlled Windows globals, a fake nt module,
a non-native function-pointer base, and a forbidden _dlopen. The adapter's exact
_windows_library and require AST nodes run with fake ctypes/kernel32 objects.
Neither ctypes nor sqlite3/_sqlite3 is imported. No SQLite library is loaded.
The test classes are constructed only by build_suite(), keeping this standalone
control out of pytest collection and the hosted required-cases/run.py inventory.
"""

import argparse
import ast
import builtins
import copy
import hashlib
import io
import json
import ntpath
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parent
ADAPTER_PATH = ROOT / 'native_sqlite.py'
FIXTURES = (
    {
        'version': '3.11.9',
        'file': 'fixtures/cpython-v3.11.9-ctypes-init.py',
        'url': 'https://raw.githubusercontent.com/python/cpython/v3.11.9/Lib/ctypes/__init__.py',
        'bytes': 17844,
        'sha256': '608671c6b49e5e027e3348fcf492718c33f69f919d99f4ff170522c40bd4c550',
    },
    {
        'version': '3.12.10',
        'file': 'fixtures/cpython-v3.12.10-ctypes-init.py',
        'url': 'https://raw.githubusercontent.com/python/cpython/v3.12.10/Lib/ctypes/__init__.py',
        'bytes': 18210,
        'sha256': 'e0db1242e78267cba0124ee9fe5963b72d1c8a1832bb51a88abcf012a9cbce76',
    },
)
OBSERVED_FILENAME = r'C:\Fixture Runtime\DLLs\sqlite3.dll'
BORROWED_HANDLE = 0x202000
OTHER_HANDLE = 0x101000
SECOND_SQLITE_HANDLE = 0x303000


class ForbiddenLoad(RuntimeError):
    pass


def file_identity(path):
    data = path.read_bytes()
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def select_one(nodes, kind, name):
    found = [node for node in nodes if isinstance(node, kind) and node.name == name]
    if len(found) != 1:
        raise AssertionError('Expected one AST node: ' + name)
    return found[0]


def exact_constructor(fixture):
    path = ROOT / fixture['file']
    actual = file_identity(path)
    if actual != {key: fixture[key] for key in ('bytes', 'sha256')}:
        raise AssertionError('Pinned official fixture bytes differ: ' + str(path))
    tree = ast.parse(path.read_bytes(), filename=str(path))
    cls = select_one(tree.body, ast.ClassDef, 'CDLL')
    return copy.deepcopy(select_one(cls.body, ast.FunctionDef, '__init__'))


def adapter_nodes():
    tree = ast.parse(ADAPTER_PATH.read_bytes(), filename=str(ADAPTER_PATH))
    selected = [copy.deepcopy(select_one(tree.body, ast.FunctionDef, name))
                for name in ('require', '_windows_library')]
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'REQUIRED_SYMBOLS'
                           for target in node.targets)]
    if len(assignments) != 1:
        raise AssertionError('Expected one literal REQUIRED_SYMBOLS assignment')
    symbols = ast.literal_eval(assignments[0].value)
    if not isinstance(symbols, tuple) or len(symbols) != 7:
        raise AssertionError('Review required: adapter symbol inventory changed')
    return selected, symbols


class ConstructorReplay:
    """Runs the official constructor only; all potential native work is fake."""

    def __init__(self, fixture):
        self.loader_calls = []
        self.fullpath_calls = []
        self.import_calls = []

        def forbidden_dlopen(*args):
            self.loader_calls.append(args)
            raise ForbiddenLoad('_dlopen forbidden in constructor replay')

        def fullpath(name):
            self.fullpath_calls.append(name)
            if name != OBSERVED_FILENAME or not ntpath.isabs(name):
                raise AssertionError('Expected the exact absolute observed filename')
            return name

        fake_nt = SimpleNamespace(
            _LOAD_LIBRARY_SEARCH_DEFAULT_DIRS=0x1000,
            _LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR=0x100,
            _getfullpathname=fullpath,
        )

        def controlled_import(name, globals=None, locals=None, fromlist=(), level=0):
            self.import_calls.append(name)
            if name != 'nt' or level != 0:
                raise AssertionError('Unexpected constructor import: ' + name)
            return fake_nt

        namespace = {
            '__name__': 'official_ctypes_constructor_replay',
            '__builtins__': dict(vars(builtins), __import__=controlled_import),
            '_os': SimpleNamespace(name='nt', fspath=os.fspath),
            '_sys': SimpleNamespace(platform='win32'),
            'DEFAULT_MODE': 0,
            '_FUNCFLAG_USE_ERRNO': 8,
            '_FUNCFLAG_USE_LASTERROR': 16,
            '_CFuncPtr': object,
            '_dlopen': forbidden_dlopen,
        }
        node = exact_constructor(fixture)
        self.constructor_lines = [node.lineno, node.end_lineno]
        module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
        exec(compile(module, str(ROOT / fixture['file']), 'exec'), namespace)
        self.CDLL = type('ReplayedCDLL', (), {
            '__init__': namespace['__init__'],
            '_func_flags_': 1,
            '_func_restype_': object,
        })


class FakeScalar:
    def __init__(self, value=None):
        self.value = value


class FakeArray:
    def __init__(self, item_type, count):
        self.item_type = item_type
        self.values = [None] * count

    def __getitem__(self, index):
        return self.values[index]

    def __setitem__(self, index, value):
        self.values[index] = value


class FakeType:
    def __init__(self, name, size, default=None):
        self.name, self.size, self.default = name, size, default

    def __call__(self, *args):
        return FakeScalar(args[0] if args else self.default)

    def __mul__(self, count):
        return lambda: FakeArray(self, count)


class FakeBuffer:
    def __init__(self, count):
        self.capacity = count
        self.value = ''

    def __len__(self):
        return self.capacity


class FakeFunction:
    """Allows the real adapter to assign argtypes/restype without native APIs."""

    def __init__(self, callback):
        self.callback = callback
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.callback(*args)


class FakeKernel:
    def __init__(self, symbols, missing=None, wrong_owner=None, ambiguous=False):
        self.symbols = symbols
        self.modules = [OTHER_HANDLE, BORROWED_HANDLE, SECOND_SQLITE_HANDLE]
        self.exports = {}
        self.owners = {}
        for index, symbol in enumerate(symbols):
            address = 0x700000 + index * 0x100
            self.exports[BORROWED_HANDLE, symbol.encode('ascii')] = (
                0 if symbol == missing else address)
            self.owners[address] = OTHER_HANDLE if symbol == wrong_owner else BORROWED_HANDLE
            if ambiguous:
                second_address = 0x800000 + index * 0x100
                self.exports[SECOND_SQLITE_HANDLE, symbol.encode('ascii')] = second_address
                self.owners[second_address] = SECOND_SQLITE_HANDLE
        self.GetCurrentProcess = FakeFunction(lambda: -1)
        self.K32EnumProcessModules = FakeFunction(self.enumerate_modules)
        self.GetProcAddress = FakeFunction(lambda handle, name: self.exports.get((handle, name), 0))
        self.GetModuleHandleExW = FakeFunction(self.symbol_owner)
        self.GetModuleFileNameW = FakeFunction(self.image_filename)

    def enumerate_modules(self, process, modules, size, needed):
        if process != -1 or size != 4096 * 8:
            raise AssertionError('Unexpected module enumeration arguments')
        for index, handle in enumerate(self.modules):
            modules[index] = handle
        needed.value = len(self.modules) * 8
        return 1

    def symbol_owner(self, flags, address, owner):
        if flags != 0x4 | 0x2:
            raise AssertionError('Borrowed ownership query changed reference-count flags')
        owner.value = self.owners[address]
        return 1

    def image_filename(self, handle, buffer, count):
        if handle != BORROWED_HANDLE or count != 32768:
            raise AssertionError('Expected exact selected image filename query')
        buffer.value = OBSERVED_FILENAME
        return len(OBSERVED_FILENAME)


class AdapterReplay:
    def __init__(self, fixture, original_none=False, **kernel_options):
        self.constructor = ConstructorReplay(fixture)
        nodes, self.symbols = adapter_nodes()
        self.kernel = FakeKernel(self.symbols, **kernel_options)
        self.cdll_calls, self.windll_calls = [], []
        pointer = FakeType('void*', 8)

        def sizeof(value):
            if isinstance(value, FakeArray):
                return len(value.values) * value.item_type.size
            if isinstance(value, FakeType):
                return value.size
            raise AssertionError('Unexpected sizeof operand')

        def cdll(name, *args, **kwargs):
            self.cdll_calls.append((name, args, kwargs))
            return self.constructor.CDLL(name, *args, **kwargs)

        def windll(name, *args, **kwargs):
            self.windll_calls.append((name, args, kwargs))
            if (name, args, kwargs) != ('kernel32', (), {'use_last_error': True}):
                raise AssertionError('Unexpected system DLL request')
            return self.kernel

        fake_ctypes = SimpleNamespace(
            WinDLL=windll, CDLL=cdll, POINTER=lambda value: ('pointer', value),
            c_uint32=FakeType('uint32', 4, 0), c_char_p=FakeType('char*', 8),
            c_wchar_p=FakeType('wchar*', 8), sizeof=sizeof, byref=lambda value: value,
            create_unicode_buffer=FakeBuffer,
        )
        cdll_nodes = [node for node in ast.walk(nodes[1]) if isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Attribute) and node.func.attr == 'CDLL'
                      and isinstance(node.func.value, ast.Name) and node.func.value.id == 'C']
        if len(cdll_nodes) != 1:
            raise AssertionError('Expected exactly one adapter CDLL call')
        call = cdll_nodes[0]
        if ast.dump(call.args[0]) != ast.dump(ast.parse('name.value', mode='eval').body):
            raise AssertionError('Adapter is not using the reviewed name.value correction')
        if original_none:
            # Only the first CDLL argument is mutated; exact adapter otherwise.
            call.args[0] = ast.copy_location(ast.Constant(None), call.args[0])
        namespace = {
            '__name__': 'actual_windows_adapter_replay',
            'C': fake_ctypes, 'P': pointer, 'I': FakeType('int', 4, 0),
            'REQUIRED_SYMBOLS': self.symbols,
        }
        module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
        exec(compile(module, str(ADAPTER_PATH), 'exec'), namespace)
        self.run = namespace['_windows_library']


class _BindingChecks:
    """Mixin; dynamically instantiated TestCases are not discovered by pytest."""

    def setUp(self):
        self.observations = []

    def record(self, **values):
        self.observations.append(values)

    def assert_no_load(self, replay):
        self.assertEqual(replay.loader_calls, [])

    def test_original_none_constructor_is_red(self):
        replay = ConstructorReplay(self.fixture)
        with self.assertRaisesRegex(TypeError, 'NoneType') as caught:
            replay.CDLL(None, handle=BORROWED_HANDLE)
        self.assert_no_load(replay)
        self.assertEqual(replay.fullpath_calls, [])
        self.record(expectedException=type(caught.exception).__name__,
                    message=str(caught.exception), dlopenCalls=0)

    def test_observed_filename_constructor_is_green(self):
        replay = ConstructorReplay(self.fixture)
        library = replay.CDLL(OBSERVED_FILENAME, handle=BORROWED_HANDLE)
        self.assertIs(library._handle, BORROWED_HANDLE)
        self.assertEqual(library._name, OBSERVED_FILENAME)
        self.assertEqual(replay.fullpath_calls, [OBSERVED_FILENAME])
        self.assertEqual(replay.import_calls, ['nt'])
        self.assert_no_load(replay)
        self.record(observedFilename=library._name, borrowedHandle=hex(library._handle),
                    sameHandleObject=True, dlopenCalls=0)

    def test_no_handle_constructor_hits_forbidden_loader_control(self):
        replay = ConstructorReplay(self.fixture)
        with self.assertRaisesRegex(ForbiddenLoad, '_dlopen forbidden'):
            replay.CDLL(OBSERVED_FILENAME)
        self.assertEqual(replay.loader_calls, [(OBSERVED_FILENAME, 0x1000 | 0x100)])
        self.record(expectedException='ForbiddenLoad', interceptedLoadAttempts=1,
                    actualNativeLoads=0)

    def test_actual_adapter_uses_observed_filename_and_borrowed_handle(self):
        replay = AdapterReplay(self.fixture)
        library, path = replay.run()
        self.assertEqual(path, OBSERVED_FILENAME)
        self.assertEqual(library._name, path)
        self.assertIs(library._handle, BORROWED_HANDLE)
        self.assertIs(library._diagnostic_kernel, replay.kernel)
        self.assertEqual(replay.cdll_calls, [(OBSERVED_FILENAME, (), {'handle': BORROWED_HANDLE})])
        self.assertEqual(len(replay.kernel.GetProcAddress.calls), 3 * len(replay.symbols))
        self.assertEqual(len(replay.kernel.GetModuleHandleExW.calls), len(replay.symbols))
        self.assertEqual(len(replay.kernel.GetModuleFileNameW.calls), 1)
        self.assert_no_load(replay.constructor)
        self.record(observedFilename=path, borrowedHandle=hex(library._handle),
                    sameHandleObject=True, ownerChecks=len(replay.symbols),
                    unchangedRefcountFlags=6, dlopenCalls=0)

    def test_original_none_adapter_mutation_is_red(self):
        replay = AdapterReplay(self.fixture, original_none=True)
        with self.assertRaisesRegex(TypeError, 'NoneType') as caught:
            replay.run()
        self.assertEqual(replay.cdll_calls, [(None, (), {'handle': BORROWED_HANDLE})])
        self.assertEqual(len(replay.kernel.GetModuleFileNameW.calls), 1)
        self.assert_no_load(replay.constructor)
        self.record(expectedException=type(caught.exception).__name__,
                    mutation='CDLL first argument name.value -> None only', dlopenCalls=0)

    def test_adapter_missing_symbol_fails_closed(self):
        for symbol in adapter_nodes()[1]:
            replay = AdapterReplay(self.fixture, missing=symbol)
            with self.assertRaisesRegex(RuntimeError, 'exactly one already-loaded'):
                replay.run()
            self.assertEqual(replay.cdll_calls, [])
            self.assertEqual(replay.kernel.GetModuleFileNameW.calls, [])
            self.assertEqual(replay.kernel.GetModuleHandleExW.calls, [])
            self.assert_no_load(replay.constructor)
            self.record(missingSymbol=symbol, expectedException='RuntimeError',
                        cdllCalls=0, dlopenCalls=0)

    def test_adapter_wrong_symbol_owner_fails_closed(self):
        for symbol in adapter_nodes()[1]:
            replay = AdapterReplay(self.fixture, wrong_owner=symbol)
            with self.assertRaisesRegex(RuntimeError, 'differing Windows images'):
                replay.run()
            self.assertEqual(replay.cdll_calls, [])
            self.assertEqual(replay.kernel.GetModuleFileNameW.calls, [])
            self.assertEqual(len(replay.kernel.GetModuleHandleExW.calls), len(replay.symbols))
            self.assert_no_load(replay.constructor)
            self.record(wrongOwnerSymbol=symbol, expectedException='RuntimeError',
                        cdllCalls=0, dlopenCalls=0)

    def test_adapter_ambiguous_images_fail_closed(self):
        replay = AdapterReplay(self.fixture, ambiguous=True)
        with self.assertRaisesRegex(RuntimeError, 'exactly one already-loaded'):
            replay.run()
        self.assertEqual(replay.cdll_calls, [])
        self.assertEqual(replay.kernel.GetModuleFileNameW.calls, [])
        self.assertEqual(replay.kernel.GetModuleHandleExW.calls, [])
        self.assert_no_load(replay.constructor)
        self.record(candidateImages=2, expectedException='RuntimeError', cdllCalls=0, dlopenCalls=0)


class ReceiptResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def capture(self, test, status, error=None):
        record = {'id': test.id(), 'fixtureVersion': test.fixture['version'],
                  'status': status, 'observations': getattr(test, 'observations', [])}
        if error is not None:
            record['error'] = self._exc_info_to_string(error, test)
        self.records.append(record)

    def addSuccess(self, test):
        super().addSuccess(test)
        self.capture(test, 'passed')

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.capture(test, 'failed', error)

    def addError(self, test, error):
        super().addError(test, error)
        self.capture(test, 'error', error)


def build_suite():
    suite = unittest.TestSuite()
    for fixture in FIXTURES:
        cls = type('WindowsBinding_' + fixture['version'].replace('.', '_'),
                   (_BindingChecks, unittest.TestCase), {'fixture': fixture})
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    return suite


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', type=Path, help='Write a standalone JSON control receipt')
    args = parser.parse_args(argv)
    if any(name in sys.modules for name in ('sqlite3', '_sqlite3', 'ctypes')):
        raise RuntimeError('Standalone replay must start without ctypes or SQLite imported')
    blocked_audit_events = []

    def guard(event, arguments):
        if event == 'ctypes.dlopen' or (event == 'import' and arguments
                                      and arguments[0] in ('ctypes', 'sqlite3', '_sqlite3')):
            blocked_audit_events.append(event)
            raise RuntimeError('Native import/load forbidden in standalone replay')

    sys.addaudithook(guard)
    stream = io.StringIO()
    suite = build_suite()
    expected_cases = suite.countTestCases()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=ReceiptResult).run(suite)
    output = stream.getvalue()
    print(output, end='')
    imported_forbidden = [name for name in ('ctypes', 'sqlite3', '_sqlite3') if name in sys.modules]
    success = (result.wasSuccessful() and result.testsRun == expected_cases == 16
               and len(result.records) == 16 and not imported_forbidden and not blocked_audit_events)
    receipt = {
        'schema': 'windows-constructor-replay-v1',
        'scope': 'Deterministic Python-level simulation; NOT native Windows OS evidence',
        'success': success,
        'hostPlatform': sys.platform,
        'hostPython': sys.version,
        'simulatedPlatform': 'win32',
        'simulatedOsName': 'nt',
        'fixtures': [dict(fixture, actual=file_identity(ROOT / fixture['file']),
                          constructorLines=ConstructorReplay(fixture).constructor_lines)
                     for fixture in FIXTURES],
        'adapter': dict(file='native_sqlite.py', **file_identity(ADAPTER_PATH)),
        'controlScript': dict(file='test_windows_binding.py', **file_identity(Path(__file__))),
        'testsRun': result.testsRun,
        'expectedTests': expected_cases,
        'failures': len(result.failures),
        'errors': len(result.errors),
        'forbiddenImportedModules': imported_forbidden,
        'blockedNativeAuditEvents': blocked_audit_events,
        'actualNativeLoads': 0,
        'testResults': result.records,
        'unittestOutput': output,
        'limitations': [
            'Fake ctypes types and kernel32 APIs do not exercise Windows ABI, OS module enumeration, or DLL ownership.',
            'Official constructors are AST-extracted, not full ctypes module imports or native interpreter executions.',
            'No SQLite runtime, database, VFS, hosting matrix, product source, or hosted required-case inventory is exercised.',
        ],
    }
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        print('Standalone receipt: ' + str(args.receipt))
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())

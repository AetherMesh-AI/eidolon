"""Diagnostic-only binding to SQLite already loaded by this Python process.

Never search for, download, or load an alternative SQLite implementation.
The registered-VFS witness proves that these exported APIs serve Python sqlite3.
"""
from contextlib import closing
import ctypes as C
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import platform
import sqlite3
import _sqlite3
import sys
import tempfile
import uuid


P, I = C.c_void_p, C.c_int


class VFS(C.Structure):
    _fields_ = [('iVersion', I), ('szOsFile', I), ('mxPathname', I),
        ('pNext', P), ('zName', C.c_char_p), ('pAppData', P)] + [(n, P) for n in (
        'xOpen xDelete xAccess xFullPathname xDlOpen xDlError xDlSym xDlClose '
        'xRandomness xSleep xCurrentTime xGetLastError xCurrentTimeInt64 '
        'xSetSystemCall xGetSystemCall xNextSystemCall').split()]


class Methods(C.Structure):
    _fields_ = [('iVersion', I)] + [(n, P) for n in (
        'xClose xRead xWrite xTruncate xSync xFileSize xLock xUnlock '
        'xCheckReservedLock xFileControl xSectorSize xDeviceCharacteristics '
        'xShmMap xShmLock xShmBarrier xShmUnmap xFetch xUnfetch').split()]


OPEN = C.CFUNCTYPE(I, P, P, P, I, C.POINTER(I))
LOCK = C.CFUNCTYPE(I, P, I)
CLOSE = C.CFUNCTYPE(I, P)
REQUIRED_SYMBOLS = (
    'sqlite3_libversion', 'sqlite3_sourceid', 'sqlite3_compileoption_get',
    'sqlite3_threadsafe', 'sqlite3_vfs_find', 'sqlite3_vfs_register',
    'sqlite3_vfs_unregister',
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def image_bytes(path):
    path = Path(path).resolve(strict=True)
    require(path.is_file(), 'Loaded SQLite image is not a readable regular file')
    before = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    after = path.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
            'Loaded image file changed while recording identity')
    return {'path': str(path), 'sha256': digest, 'bytes': after.st_size}


def checked_vfs(pointer):
    require(bool(pointer), 'Missing native VFS')
    require(C.cast(pointer, C.POINTER(I))[0] == 3,
            'Unsupported VFS version; refused before full table copy')
    result = VFS.from_buffer_copy(pointer.contents)
    require(bool(result.xOpen) and bool(result.zName), 'Missing native VFS fields')
    require(result.szOsFile >= C.sizeof(P) and 0 < result.mxPathname <= 1048576,
            'Invalid native VFS dimensions')
    return result


def checked_methods(address):
    require(bool(address), 'Missing native I/O method table')
    require(C.cast(address, C.POINTER(I))[0] == 3,
            'Unsupported native method version; refused before full table copy')
    result = Methods.from_buffer_copy(Methods.from_address(address))
    require(bool(result.xLock) and bool(result.xClose), 'Missing native I/O methods')
    return result


def _posix_library():
    # RTLD_NOLOAD prevents this adapter from introducing any other SQLite image.
    require(hasattr(os, 'RTLD_NOLOAD'), 'Runtime has no safe no-load binding')
    extension_path = getattr(_sqlite3, '__file__', None)
    extension = (C.CDLL(extension_path, mode=os.RTLD_NOLOAD | os.RTLD_NOW)
                 if extension_path else C.CDLL(None))
    for name in REQUIRED_SYMBOLS:
        require(hasattr(extension, name), 'Loaded extension does not expose ' + name)
    class DlInfo(C.Structure):
        _fields_ = [('filename', C.c_char_p), ('base', P),
                    ('symbol', C.c_char_p), ('symbol_address', P)]
    dladdr = C.CDLL(None).dladdr
    dladdr.argtypes, dladdr.restype = [P, C.POINTER(DlInfo)], I
    images = []
    for name in REQUIRED_SYMBOLS:
        info = DlInfo()
        require(dladdr(C.cast(getattr(extension, name), P), C.byref(info)) != 0 and
                bool(info.filename), 'Cannot identify loaded symbol image: ' + name)
        images.append(str(Path(os.fsdecode(info.filename)).resolve(strict=True)))
    require(len(set(images)) == 1, 'SQLite APIs resolve to different loaded images')
    return extension, images[0]


def _windows_library():
    # kernel32 is an OS component already mapped into every native Python process.
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.argtypes, kernel.GetCurrentProcess.restype = [], P
    kernel.K32EnumProcessModules.argtypes = [P, C.POINTER(P), C.c_uint32,
                                            C.POINTER(C.c_uint32)]
    kernel.K32EnumProcessModules.restype = I
    kernel.GetProcAddress.argtypes, kernel.GetProcAddress.restype = [P, C.c_char_p], P
    kernel.GetModuleFileNameW.argtypes = [P, C.c_wchar_p, C.c_uint32]
    kernel.GetModuleFileNameW.restype = C.c_uint32
    kernel.GetModuleHandleExW.argtypes = [C.c_uint32, P, C.POINTER(P)]
    kernel.GetModuleHandleExW.restype = I
    process = kernel.GetCurrentProcess()
    modules, needed = (P * 4096)(), C.c_uint32()
    require(kernel.K32EnumProcessModules(process, modules, C.sizeof(modules),
                                        C.byref(needed)) != 0,
            'Cannot enumerate already-loaded Windows modules')
    require(needed.value <= C.sizeof(modules) and needed.value % C.sizeof(P) == 0,
            'Loaded-module enumeration exceeded diagnostic bound')
    candidates = []
    for handle in modules[:needed.value // C.sizeof(P)]:
        symbols = [kernel.GetProcAddress(handle, name.encode('ascii'))
                   for name in REQUIRED_SYMBOLS]
        if all(symbols):
            candidates.append((handle, symbols))
    require(len(candidates) == 1, 'Expected exactly one already-loaded SQLite API image')
    handle, symbols = candidates[0]
    owners = []
    for address in symbols:
        owner = P()
        # FROM_ADDRESS | UNCHANGED_REFCOUNT; never free this borrowed module.
        require(kernel.GetModuleHandleExW(0x4 | 0x2, address, C.byref(owner)) != 0,
                'Cannot identify Windows SQLite symbol owner')
        owners.append(owner.value)
    require(set(owners) == {handle}, 'SQLite exports belong to differing Windows images')
    name = C.create_unicode_buffer(32768)
    size = kernel.GetModuleFileNameW(handle, name, len(name))
    require(0 < size < len(name), 'Cannot read exact loaded SQLite image path')
    # A supplied handle does not call LoadLibrary or search for a replacement.
    library = C.CDLL(None, handle=handle)
    library._diagnostic_kernel = kernel
    return library, name.value


class NativeSQLite:
    def __init__(self):
        require(C.sizeof(P) == 8, 'Only reviewed 64-bit hosted architectures are supported')
        require(sys.platform in {'linux', 'darwin', 'win32'}, 'Unsupported proof platform')
        with closing(sqlite3.connect(':memory:')) as conn:
            py_version, py_source = conn.execute(
                'SELECT sqlite_version(), sqlite_source_id()').fetchone()
            py_options = sorted(row[0] for row in conn.execute('PRAGMA compile_options'))
        self.lib, image = _windows_library() if sys.platform == 'win32' else _posix_library()
        for name in ('sqlite3_libversion', 'sqlite3_sourceid'):
            function = getattr(self.lib, name)
            function.argtypes, function.restype = [], C.c_char_p
        self.lib.sqlite3_compileoption_get.argtypes = [I]
        self.lib.sqlite3_compileoption_get.restype = C.c_char_p
        self.lib.sqlite3_threadsafe.argtypes, self.lib.sqlite3_threadsafe.restype = [], I
        self.lib.sqlite3_vfs_find.argtypes = [C.c_char_p]
        self.lib.sqlite3_vfs_find.restype = C.POINTER(VFS)
        self.lib.sqlite3_vfs_register.argtypes, self.lib.sqlite3_vfs_register.restype = [C.POINTER(VFS), I], I
        self.lib.sqlite3_vfs_unregister.argtypes, self.lib.sqlite3_vfs_unregister.restype = [C.POINTER(VFS)], I
        native_version = self.lib.sqlite3_libversion().decode('ascii')
        native_source = self.lib.sqlite3_sourceid().decode('ascii')
        options = []
        for index in range(4096):
            value = self.lib.sqlite3_compileoption_get(index)
            if value is None:
                break
            options.append(value.decode('ascii'))
        else:
            raise RuntimeError('Compile-option enumeration exceeded bound')
        require(native_version == py_version == sqlite3.sqlite_version and
                native_source == py_source and sorted(options) == py_options,
                'Native API and Python sqlite3 runtime identities differ')
        require(self.lib.sqlite3_threadsafe() != 0 and sqlite3.threadsafety > 0,
                'Threaded diagnostic requires a threadsafe SQLite build')
        self.original = self.lib.sqlite3_vfs_find(None)
        native = checked_vfs(self.original)
        expected = 'win32' if sys.platform == 'win32' else 'unix'
        require(native.zName.decode('ascii') == expected,
                'Unexpected default VFS; do not substitute a different VFS')
        self.receipt = {
            'python': sys.version, 'executable': sys.executable,
            'baseExecutable': getattr(sys, '_base_executable', None),
            'platform': sys.platform, 'os': platform.platform(),
            'architecture': platform.machine(), 'pointerBytes': C.sizeof(P),
            'sqlite': native_version, 'sourceId': native_source,
            'compileOptions': py_options, 'sqliteThreadsafe': self.lib.sqlite3_threadsafe(),
            'pythonThreadsafety': sqlite3.threadsafety,
            'extension': (image_bytes(_sqlite3.__file__) if getattr(_sqlite3, '__file__', None)
                          else {'origin': 'built-in', 'executable': image_bytes(sys.executable)}),
            'library': image_bytes(image), 'vfs': expected, 'vfsVersion': native.iVersion,
            'runner': {key: os.environ.get(key) for key in
                       ('ImageOS', 'ImageVersion', 'RUNNER_OS', 'RUNNER_ARCH')},
        }
        self.receipt['sameInstanceWitness'] = self._witness()

    def _witness(self):
        original_address = C.cast(self.original, P).value
        proxy = checked_vfs(self.original)
        name = ('eidolon-instance-' + uuid.uuid4().hex).encode('ascii')
        proxy.zName, proxy.pNext = name, None
        delegate = OPEN(proxy.xOpen)
        events, errors = [], []
        @OPEN
        def opened(vfs, filename, file, flags, outflags):
            result = delegate(self.original, filename, file, flags, outflags)
            try:
                if flags & 0x100:
                    events.append({'result': result, 'flags': flags,
                                   'returnedFlags': outflags[0] if outflags else None})
            except BaseException:
                errors.append('witness_observation_failed')
            return result
        proxy.xOpen = C.cast(opened, P).value
        require(self.lib.sqlite3_vfs_register(C.byref(proxy), 0) == 0,
                'Cannot register same-instance witness')
        try:
            with tempfile.TemporaryDirectory(prefix='eidolon-vfs-witness-') as directory:
                path = Path(directory) / 'witness.db'
                with closing(sqlite3.connect(path.as_uri() + '?mode=rwc&vfs=' + name.decode(),
                                             uri=True, isolation_level=None)) as conn:
                    conn.execute('CREATE TABLE witness(value INTEGER)')
                    conn.execute('INSERT INTO witness VALUES(7)')
                    require(conn.execute('SELECT value FROM witness').fetchone() == (7,),
                            'Python VFS witness failed')
        finally:
            rc = self.lib.sqlite3_vfs_unregister(C.byref(proxy))
            require(rc == 0, 'Cannot remove same-instance witness')
            require(C.cast(self.lib.sqlite3_vfs_find(None), P).value == original_address,
                    'Same-instance witness changed the default VFS')
        require(not errors and len(events) == 1 and events[0]['result'] == 0,
                'Registered VFS did not observe exactly one Python main-file open')
        return {'pythonOpenObserved': True, 'defaultUnchanged': True,
                'mainOpens': events, 'restored': True}


@lru_cache(maxsize=1)
def runtime():
    return NativeSQLite()


def candidate_identity():
    root = Path(os.environ['EIDOLON_PROOF_SOURCE']).resolve(strict=True)
    from eidolon_cli import organization_store
    actual = Path(organization_store.__file__).resolve(strict=True)
    require(actual == root / 'eidolon_cli' / 'organization_store.py',
            'OrganizationStore imported outside explicit candidate source')
    receipt = image_bytes(actual)
    expected = os.environ.get('EIDOLON_PROOF_STORE_SHA256')
    require(bool(expected) and receipt['sha256'] == expected,
            'Candidate OrganizationStore bytes differ from runner receipt')
    return receipt

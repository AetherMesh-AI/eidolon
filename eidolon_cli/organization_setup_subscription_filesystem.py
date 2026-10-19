"""Read-only, descriptor-pinned Darwin filesystem protection for setup grants.

Only ancestor directories may renew a complete ACL observation once after
sibling-entry churn. Targets and ordinary files retain strict metadata checks.
"""
from contextlib import contextmanager
import ctypes
import errno
import os
from pathlib import Path
import platform
import stat


class _DarwinTimespec(ctypes.Structure):
    _fields_ = [('tv_sec', ctypes.c_int64), ('tv_nsec', ctypes.c_int64)]


class _DarwinStat64(ctypes.Structure):
    # Apple xnu bsd/sys/stat.h __DARWIN_STRUCT_STAT64, 64-bit macOS only.
    _fields_ = [
        ('st_dev', ctypes.c_int32), ('st_mode', ctypes.c_uint16),
        ('st_nlink', ctypes.c_uint16), ('st_ino', ctypes.c_uint64),
        ('st_uid', ctypes.c_uint32), ('st_gid', ctypes.c_uint32),
        ('st_rdev', ctypes.c_int32), ('st_atimespec', _DarwinTimespec),
        ('st_mtimespec', _DarwinTimespec), ('st_ctimespec', _DarwinTimespec),
        ('st_birthtimespec', _DarwinTimespec), ('st_size', ctypes.c_int64),
        ('st_blocks', ctypes.c_int64), ('st_blksize', ctypes.c_int32),
        ('st_flags', ctypes.c_uint32), ('st_gen', ctypes.c_uint32),
        ('st_lspare', ctypes.c_int32), ('st_qspare', ctypes.c_int64 * 2),
    ]

    @property
    def st_ctime_ns(self):
        return self.st_ctimespec.tv_sec * 1_000_000_000 + self.st_ctimespec.tv_nsec


def _file_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_ctime_ns)


@contextmanager
def _checked_acl_descriptor(path):
    from eidolon_cli.organization_setup_subscription_store import StoreError

    before = Path(path).lstat()
    if not (stat.S_ISREG(before.st_mode) or stat.S_ISDIR(before.st_mode)):
        raise StoreError('unsafe_filesystem')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if _file_identity(os.fstat(fd)) != _file_identity(before):
            raise StoreError('unsafe_filesystem')
        yield fd, before
        if (_file_identity(os.fstat(fd)) != _file_identity(before)
                or _file_identity(Path(path).lstat()) != _file_identity(before)):
            raise StoreError('unsafe_filesystem')
    finally:
        os.close(fd)


def _directory_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)


def _check_ancestor_acl(path, api, fstatx):
    from eidolon_cli.organization_setup_subscription_store import StoreError

    before = Path(path).lstat()
    if not stat.S_ISDIR(before.st_mode):
        raise StoreError('unsafe_filesystem')
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        opened = os.fstat(fd)
        if _directory_identity(opened) != _directory_identity(before):
            raise StoreError('unsafe_filesystem')
        renewed_at_open = _file_identity(opened) != _file_identity(before)
        baseline = opened if renewed_at_open else before
        if renewed_at_open and _file_identity(Path(path).lstat()) != _file_identity(baseline):
            raise StoreError('unsafe_filesystem')
        native, snapshot = _read_darwin_acl(
            api, fstatx, fd, baseline, allow_directory_churn=not renewed_at_open)
        final_fd = os.fstat(fd)
        final_path = Path(path).lstat()
        if (_directory_identity(final_fd) != _directory_identity(before)
                or _directory_identity(final_path) != _directory_identity(before)):
            raise StoreError('unsafe_filesystem')
        if _file_identity(final_path) != _file_identity(final_fd):
            # A first metadata-only drift can also occur between the final FD
            # and path reads. Any earlier drift has already spent the renewal.
            if (renewed_at_open or _file_identity(native) != _file_identity(before)
                    or _file_identity(final_fd) != _file_identity(before)):
                raise StoreError('unsafe_filesystem')
            final_fd = final_path
            if _file_identity(os.fstat(fd)) != _file_identity(final_fd):
                raise StoreError('unsafe_filesystem')
        if renewed_at_open:
            if _file_identity(final_fd) != _file_identity(baseline):
                raise StoreError('unsafe_filesystem')
            return
        if (_file_identity(native) != _file_identity(before)
                and _file_identity(final_fd) != _file_identity(native)):
            raise StoreError('unsafe_filesystem')
        if _file_identity(final_fd) == _file_identity(before):
            return
        # nlink/ctime alone cannot establish safe sibling churn: they also
        # change when ACLs change. Re-read the entire ACL on the pinned inode,
        # requiring both identical ACLs and a stable second metadata interval.
        _, renewed_snapshot = _read_darwin_acl(api, fstatx, fd, final_fd)
        if (_file_identity(os.fstat(fd)) != _file_identity(final_fd)
                or _file_identity(Path(path).lstat()) != _file_identity(final_fd)
                or renewed_snapshot != snapshot):
            raise StoreError('unsafe_filesystem')
    finally:
        os.close(fd)


def _acl_snapshot(api, acl, entries):
    from eidolon_cli.organization_setup_subscription_store import StoreError

    # Apple Libc posix1e/acl_translate.c: acl_copy_ext zeroes all bytes before
    # encoding ordered UUIDs, ACE flags/rights and ACL flags in big endian.
    # xnu bsd/sys/kauth.h: KAUTH_FILESEC_SIZE(n) == 44 + 24*n (n <= 128).
    size = api.acl_size(acl)
    if size != 44 + 24 * entries:
        raise StoreError('filesystem_protection_unavailable')
    buffer = ctypes.create_string_buffer(size)
    if api.acl_copy_ext(buffer, acl, size) != size:
        raise StoreError('filesystem_protection_unavailable')
    snapshot = buffer.raw
    if (snapshot[:4] != b'\x01\x2c\xc1\x6d' or snapshot[4:36] != bytes(32)
            or int.from_bytes(snapshot[36:40], 'big') != entries):
        raise StoreError('filesystem_protection_unavailable')
    if any(int.from_bytes(snapshot[offset:offset + 4], 'big') & 0xf != 2
           for offset in range(60, size, 24)):
        raise StoreError('filesystem_protection_unavailable')
    return snapshot


def _read_darwin_acl(api, fstatx, fd, before, *, allow_directory_churn=False):
    from eidolon_cli.organization_setup_subscription_store import StoreError

    fsec = api.filesec_init()
    if not fsec:
        raise StoreError('filesystem_protection_unavailable')
    acl = ctypes.c_void_p()
    try:
        native_stat = _DarwinStat64()
        if fstatx(fd, ctypes.byref(native_stat), fsec) != 0:
            raise StoreError('filesystem_protection_unavailable')
        if _file_identity(native_stat) != _file_identity(before):
            if (not allow_directory_churn or not stat.S_ISDIR(before.st_mode)
                    or _directory_identity(native_stat) != _directory_identity(before)):
                raise StoreError('unsafe_filesystem')
        present = ctypes.c_int(-1)
        if api.filesec_query_property(fsec, 5, ctypes.byref(present)) != 0 or present.value < 0:
            raise StoreError('filesystem_protection_unavailable')
        if present.value == 0:  # Absence differs from a present, empty ACL.
            return native_stat, None
        if api.filesec_get_property(fsec, 5, ctypes.byref(acl)) != 0 or not acl.value or acl.value == 1:
            raise StoreError('filesystem_protection_unavailable')
        if api.acl_valid(acl) != 0:
            raise StoreError('filesystem_protection_unavailable')
        entry, index = ctypes.c_void_p(), 0  # ACL_FIRST_ENTRY
        for entries in range(129):  # Darwin ACL_MAX_ENTRIES is 128.
            ctypes.set_errno(0)
            result = api.acl_get_entry(acl, index, ctypes.byref(entry))
            if result == -1 and ctypes.get_errno() == errno.EINVAL:
                return native_stat, _acl_snapshot(api, acl, entries)
            if result != 0:
                raise StoreError('filesystem_protection_unavailable')
            tag = ctypes.c_int()
            if api.acl_get_tag_type(entry, ctypes.byref(tag)) != 0:
                raise StoreError('filesystem_protection_unavailable')
            if tag.value != 2:  # Only ACL_EXTENDED_DENY is harmless.
                raise StoreError('unsafe_filesystem')
            index = -1  # ACL_NEXT_ENTRY
        raise StoreError('filesystem_protection_unavailable')
    finally:
        if acl.value and acl.value != 1:
            api.acl_free(acl)
        api.filesec_free(fsec)


def check_darwin_acl(path, *, ancestor=False):
    """Read-only, descriptor-bound ACL inspection; never accept arbitrary NULL.

    Apple Libc sys/statx_np.c populates FILESEC_ACL only when an ACL exists;
    gen/filesec.c exposes this through filesec_query_property. Absence is valid
    ONLY after successful fstatx and explicit successful property-presence query.
    xnu bsd/sys/stat.h and cdefs.h define the stat64 ABI and symbol suffixes.
    """
    from eidolon_cli.organization_setup_subscription_store import StoreError

    architecture = platform.machine().lower()
    if (architecture not in {'arm64', 'x86_64'} or ctypes.sizeof(ctypes.c_void_p) != 8
            or ctypes.sizeof(_DarwinStat64) != 144 or ctypes.alignment(_DarwinStat64) != 8):
        raise StoreError('filesystem_protection_unavailable')
    api = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    symbol = 'fstatx_np$INODE64' if architecture == 'x86_64' else 'fstatx_np'
    try:
        fstatx = getattr(api, symbol)  # Never fall back to an incompatible ABI.
        fstatx.argtypes = [ctypes.c_int, ctypes.POINTER(_DarwinStat64), ctypes.c_void_p]
        fstatx.restype = ctypes.c_int
        api.filesec_init.argtypes = []
        api.filesec_init.restype = ctypes.c_void_p
        api.filesec_free.argtypes = [ctypes.c_void_p]
        api.filesec_free.restype = None
        api.filesec_query_property.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        api.filesec_query_property.restype = ctypes.c_int
        api.filesec_get_property.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
        api.filesec_get_property.restype = ctypes.c_int
        api.acl_valid.argtypes = [ctypes.c_void_p]
        api.acl_valid.restype = ctypes.c_int
        api.acl_get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
        api.acl_get_entry.restype = ctypes.c_int
        api.acl_get_tag_type.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        api.acl_get_tag_type.restype = ctypes.c_int
        api.acl_size.argtypes = [ctypes.c_void_p]
        api.acl_size.restype = ctypes.c_ssize_t
        api.acl_copy_ext.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t]
        api.acl_copy_ext.restype = ctypes.c_ssize_t
        api.acl_free.argtypes = [ctypes.c_void_p]
        api.acl_free.restype = ctypes.c_int
    except AttributeError:
        raise StoreError('filesystem_protection_unavailable') from None
    if ancestor:
        _check_ancestor_acl(path, api, fstatx)
    else:
        with _checked_acl_descriptor(path) as (fd, before):
            _read_darwin_acl(api, fstatx, fd, before)

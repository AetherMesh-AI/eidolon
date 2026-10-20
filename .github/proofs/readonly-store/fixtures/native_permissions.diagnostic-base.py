"""Test-only, fail-closed permission controls for a freshly owned directory.

No production security/admission helper is replaced.  POSIX uses real process
permissions; Windows uses native DACLs, not chmod's read-only attribute.  Only a
directory created here and its direct, single-link regular files are changed.

Windows saves the exact OWNER/GROUP/DACL self-relative security descriptor and
requires byte-identical readback after restoration.  SACLs, audit policy, owner,
group, token privileges, and ancestor permissions are never changed.  Reading
SACLs would require additional privileges and is deliberately not attempted.

Native API contracts:
https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-getnamedsecurityinfow
https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setnamedsecurityinfow
https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-addaccessdeniedaceex
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import errno
import hashlib
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _acl_mismatch_diagnostic(before: bytes, after: bytes) -> str:
    # Only called while forming the existing byte-equality assertion failure.
    # An observer failure must never replace that failure or expose raw data.
    try:
        from acl_descriptor_diagnostic import comparison_message, validate_comparison_message
        message = comparison_message(before, after)
        if (type(message) is not str or len(message) > 32800 or
                not message.startswith('ACL_DESCRIPTOR_COMPARISON_V1={') or
                not message.endswith('}')):
            raise ValueError("Invalid diagnostic output")
        validate_comparison_message(message, require_mismatch=True)
        return "; " + message
    except BaseException:
        return ('; ACL_DESCRIPTOR_COMPARISON_V1={"accessEquivalence":"not_assessed",'
                '"equal":null,"reason":"observer_failure",'
                '"schema":"windows-acl-descriptor-comparison-v1","valid":false}')


def _identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise AssertionError(f"Permission fixture refuses links/reparse points: {path}")
    return info.st_dev, info.st_ino


@dataclass
class OwnedPermissionDirectory:
    root: Path
    identity: tuple[int, int]
    restored: bool = True
    denial_evidence: list[dict] = field(default_factory=list)
    restoration_evidence: list[dict] = field(default_factory=list)

    def _validate(self, files: tuple[Path, ...]) -> None:
        assert _identity(self.root) == self.identity, "Fixture directory was replaced"
        assert self.root.is_dir()
        assert files and len(set(files)) == len(files)
        assert set(self.root.iterdir()) == set(files), "Unexpected fixture entries"
        for path in files:
            assert path.parent == self.root, "Refusing a path outside the owned fixture"
            _identity(path)
            info = path.lstat()
            assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            if os.name == "posix":
                assert info.st_uid == os.geteuid(), "Fixture file has a different owner"
        if os.name == "posix":
            assert self.root.stat().st_uid == os.geteuid()
            assert os.geteuid() != 0, "Run POSIX permission proof as an unprivileged user"

    def _try_open(self, path: Path, flags: int, *, denied: bool) -> None:
        try:
            fd = os.open(path, flags, 0o600)
        except OSError as exc:
            if not denied or exc.errno not in {errno.EACCES, errno.EPERM}:
                raise
            self.denial_evidence.append({
                "path": path.name, "flags": flags, "errno": exc.errno,
                "winerror": getattr(exc, "winerror", None), "pid": os.getpid(),
            })
        else:
            os.close(fd)
            if denied:
                raise AssertionError(f"Native permission restriction was ineffective: {path}")

    def prove_writable(self, files: tuple[Path, ...]) -> None:
        """Positive control, using the same process and access flags as denial."""
        self._validate(files)
        probe = self.root / "permission-create-control.tmp"
        self._try_open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, denied=False)
        probe.unlink()
        for path in files:
            self._try_open(path, os.O_RDWR, denied=False)

    def _prove_readonly(self, files: tuple[Path, ...]) -> None:
        # Opening a genuinely new file is the sidecar-create negative control.
        probe = self.root / "permission-create-control.tmp"
        self._try_open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, denied=True)
        assert not probe.exists()
        for path in files:
            self._try_open(path, os.O_RDWR, denied=True)
            with path.open("rb") as stream:
                stream.read(1)  # A deny-all ACL must not satisfy this control.

    @contextmanager
    def restrict_readonly(self, files):
        files = tuple(Path(path) for path in files)
        self._validate(files)
        self.denial_evidence.clear()
        self.restoration_evidence.clear()
        if os.name == "nt":
            backend = _WindowsAcl()
        elif os.name == "posix" and sys.platform in {"linux", "darwin"}:
            backend = _PosixModes()
        else:
            raise AssertionError(f"Unsupported native permission platform: {sys.platform}")
        paths = (self.root, *files)
        identities = {path: _identity(path) for path in paths}
        # Save every path before the first mutation, including children whose
        # inherited ACLs could be affected by a directory DACL operation.
        saved = {path: backend.capture(path) for path in paths}
        backend.assert_owned(saved)
        self.restored = False
        changed = False
        try:
            # Children are protected first, keeping directory propagation local.
            for path in (*files, self.root):
                assert _identity(path) == identities[path]
                changed = True  # Even an uncertain failed native call is restored.
                backend.restrict(path, saved[path], directory=path == self.root)
            self._prove_readonly(files)
            yield self
        finally:
            errors = []
            # Restore parent first so child inheritance resolves from its exact
            # baseline. Attempt every restore even when an earlier one fails.
            if changed:
                for path in paths:
                    try:
                        assert _identity(path) == identities[path]
                        backend.restore(path, saved[path])
                    except BaseException as exc:
                        errors.append(exc)
            for path in paths:
                try:
                    assert _identity(path) == identities[path]
                    evidence = backend.check_restored(path, saved[path])
                    self.restoration_evidence.append(evidence)
                except BaseException as exc:
                    errors.append(exc)
            self.restored = not errors
            if errors:
                raise BaseExceptionGroup("Native permission restoration failed", errors)


@contextmanager
def owned_permission_directory(parent: Path):
    """Create the only directory this module is permitted to restrict."""
    root = Path(tempfile.mkdtemp(prefix="native-readonly-control-", dir=parent)).resolve()
    fixture = OwnedPermissionDirectory(root=root, identity=_identity(root))
    try:
        yield fixture
    finally:
        # Do not paper over failed ACL restoration by deleting/replacing files
        # or forcing alternate permissions. Leave a failed fixture for diagnosis.
        if fixture.restored:
            assert _identity(root) == fixture.identity
            shutil.rmtree(root)


class _PosixModes:
    def capture(self, path):
        return stat.S_IMODE(path.stat().st_mode)

    def assert_owned(self, saved):
        assert os.geteuid() != 0
        assert all(path.stat().st_uid == os.geteuid() for path in saved)

    def restrict(self, path, saved, *, directory):
        path.chmod(0o500 if directory else 0o400)

    def restore(self, path, saved):
        path.chmod(saved)

    def check_restored(self, path, saved):
        actual = self.capture(path)
        assert actual == saved, f"Mode restoration mismatch: {path}"
        return {"path": path.name, "kind": "posix-mode", "before": oct(saved),
                "after": oct(actual), "exact": True}


class _WindowsAcl:
    """Minimal ctypes adapter; only loaded on a real Windows process."""
    OWNER_GROUP_DACL = 0x00000007
    DACL = 0x00000004
    PROTECTED_DACL = 0x80000000
    UNPROTECTED_DACL = 0x20000000
    SE_DACL_PROTECTED = 0x1000

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.w = wintypes
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        ptr = ctypes.c_void_p
        pptr = ctypes.POINTER(ptr)
        dword = wintypes.DWORD
        pdword = ctypes.POINTER(dword)
        boolean = wintypes.BOOL
        pbool = ctypes.POINTER(boolean)

        signatures = {
            "GetNamedSecurityInfoW": (dword, [wintypes.LPCWSTR, dword, dword,
                                             pptr, pptr, pptr, pptr, pptr]),
            "SetNamedSecurityInfoW": (dword, [wintypes.LPWSTR, dword, dword,
                                             ptr, ptr, ptr, ptr]),
            "GetSecurityDescriptorLength": (dword, [ptr]),
            "GetSecurityDescriptorControl": (boolean, [ptr, ctypes.POINTER(wintypes.WORD), pdword]),
            "GetSecurityDescriptorDacl": (boolean, [ptr, pbool, pptr, pbool]),
            "GetSecurityDescriptorOwner": (boolean, [ptr, pptr, pbool]),
            "OpenProcessToken": (boolean, [wintypes.HANDLE, dword, ctypes.POINTER(wintypes.HANDLE)]),
            "GetTokenInformation": (boolean, [wintypes.HANDLE, dword, ptr, dword, pdword]),
            "GetLengthSid": (dword, [ptr]),
            "InitializeAcl": (boolean, [ptr, dword, dword]),
            "AddAccessDeniedAceEx": (boolean, [ptr, dword, dword, dword, ptr]),
            "GetAce": (boolean, [ptr, dword, pptr]),
            "AddAce": (boolean, [ptr, dword, dword, ptr, dword]),
        }
        for name, (restype, argtypes) in signatures.items():
            function = getattr(self.advapi, name)
            function.restype, function.argtypes = restype, argtypes
        for name, restype, argtypes in [
            ("GetCurrentProcess", wintypes.HANDLE, []),
            ("CloseHandle", boolean, [wintypes.HANDLE]),
            ("LocalFree", ptr, [ptr]),
        ]:
            function = getattr(self.kernel, name)
            function.restype, function.argtypes = restype, argtypes
        self.user_sid, self.default_owner_sid = self._token_sids()

    def _ok(self, value):
        if not value:
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        return value

    def _status(self, value):
        if value:
            raise self.ctypes.WinError(value)

    def _token_sids(self):
        c, w = self.ctypes, self.w
        token = w.HANDLE()
        self._ok(self.advapi.OpenProcessToken(self.kernel.GetCurrentProcess(), 0x0008, c.byref(token)))
        try:
            result = []
            for information_class in (1, 4):  # TokenUser, TokenOwner.
                length = w.DWORD()
                ok = self.advapi.GetTokenInformation(token, information_class, None, 0, c.byref(length))
                assert not ok and c.get_last_error() == 122 and length.value > 0
                buffer = c.create_string_buffer(length.value)
                self._ok(self.advapi.GetTokenInformation(
                    token, information_class, buffer, length.value, c.byref(length)))
                sid = c.c_void_p.from_buffer(buffer).value
                assert sid
                result.append(c.string_at(sid, self.advapi.GetLengthSid(sid)))
            return tuple(result)
        finally:
            self._ok(self.kernel.CloseHandle(token))

    def capture(self, path):
        c, w = self.ctypes, self.w
        descriptor = c.c_void_p()
        self._status(self.advapi.GetNamedSecurityInfoW(
            str(path), 1, self.OWNER_GROUP_DACL, None, None, None, None, c.byref(descriptor)))
        try:
            raw = c.string_at(descriptor, self.advapi.GetSecurityDescriptorLength(descriptor))
            control, revision = w.WORD(), w.DWORD()
            self._ok(self.advapi.GetSecurityDescriptorControl(descriptor, c.byref(control), c.byref(revision)))
            owner, defaulted = c.c_void_p(), w.BOOL()
            self._ok(self.advapi.GetSecurityDescriptorOwner(descriptor, c.byref(owner), c.byref(defaulted)))
            assert owner.value, "Fixture must have a real owner"
            owner_bytes = c.string_at(owner, self.advapi.GetLengthSid(owner))
            return {"descriptor": raw, "control": control.value, "owner": owner_bytes}
        finally:
            assert not self.kernel.LocalFree(descriptor), "LocalFree failed"

    def assert_owned(self, saved):
        assert all(snapshot["owner"] in {self.user_sid, self.default_owner_sid}
                   for snapshot in saved.values()), "Windows fixture owner differs from process token"

    def _dacl(self, descriptor_buffer):
        c, w = self.ctypes, self.w
        present, defaulted, dacl = w.BOOL(), w.BOOL(), c.c_void_p()
        self._ok(self.advapi.GetSecurityDescriptorDacl(
            descriptor_buffer, c.byref(present), c.byref(dacl), c.byref(defaulted)))
        assert present.value and dacl.value, "Expected a real, present fixture DACL"
        return dacl

    def _set(self, path, dacl, *, protected):
        information = self.DACL | (self.PROTECTED_DACL if protected else self.UNPROTECTED_DACL)
        # No OWNER/GROUP/SACL flags and no inheritable new ACEs are ever supplied.
        self._status(self.advapi.SetNamedSecurityInfoW(str(path), 1, information,
                                                     None, None, dacl, None))

    def restrict(self, path, saved, *, directory):
        c = self.ctypes
        descriptor = c.create_string_buffer(saved["descriptor"])
        original = self._dacl(descriptor)
        header = c.string_at(original, 8)  # ACL header: BYTE, BYTE, WORD, WORD, WORD.
        revision = header[0]
        assert revision in {2, 4}
        old_size = int.from_bytes(header[2:4], "little")
        ace_count = int.from_bytes(header[4:6], "little")
        size = old_size + 8 + len(self.user_sid)
        assert size <= 65535
        acl = c.create_string_buffer(size)
        self._ok(self.advapi.InitializeAcl(acl, size, revision))
        sid = c.create_string_buffer(self.user_sid)
        # Precise data/metadata/delete denial; READ_CONTROL and WRITE_DAC remain
        # available for exact restoration. Never deny GENERIC_WRITE wholesale:
        # its SYNCHRONIZE bit overlaps otherwise legitimate read-only opens.
        denied_rights = 0x2 | 0x4 | 0x10 | 0x100 | 0x10000
        if directory:
            denied_rights |= 0x40  # FILE_DELETE_CHILD, in addition to ADD_FILE.
        self._ok(self.advapi.AddAccessDeniedAceEx(acl, revision, 0, denied_rights, sid))
        for index in range(ace_count):
            ace = c.c_void_p()
            self._ok(self.advapi.GetAce(original, index, c.byref(ace)))
            ace_size = int.from_bytes(c.string_at(ace.value + 2, 2), "little")
            self._ok(self.advapi.AddAce(acl, revision, 0xFFFFFFFF, ace, ace_size))
        self._set(path, acl, protected=True)

    def restore(self, path, saved):
        descriptor = self.ctypes.create_string_buffer(saved["descriptor"])
        self._set(path, self._dacl(descriptor),
                  protected=bool(saved["control"] & self.SE_DACL_PROTECTED))

    def check_restored(self, path, saved):
        actual = self.capture(path)
        assert actual["descriptor"] == saved["descriptor"], (
            f"OWNER/GROUP/DACL descriptor not restored byte-for-byte: {path}; "
            f"before={_digest(saved['descriptor'])}, after={_digest(actual['descriptor'])}"
            + _acl_mismatch_diagnostic(saved["descriptor"], actual["descriptor"]))
        return {"path": path.name, "kind": "windows-owner-group-dacl-descriptor",
                "before": _digest(saved["descriptor"]),
                "after": _digest(actual["descriptor"]), "exact": True}

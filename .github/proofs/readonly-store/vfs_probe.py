"""Temporary same-process VFS scheduling evidence. Never a product module."""
import ctypes as C
from pathlib import Path
import threading
import uuid

from native_sqlite import P, OPEN, LOCK, CLOSE, VFS, checked_methods, checked_vfs
from native_sqlite import runtime, candidate_identity

KEEPALIVE = []
KEEPALIVE_LIMIT = 32


def file_identity(path):
    stat = Path(path).stat()
    if stat.st_ino == 0:
        raise RuntimeError('Filesystem did not provide usable native file identity')
    return stat.st_dev, stat.st_ino


class ClosePause:
    def __init__(self, *, pause_readonly_refusal=False):
        self.runtime = runtime()
        self.lib = self.runtime.lib
        self.original = self.lib.sqlite3_vfs_find(None)
        self.proxy = checked_vfs(self.original)
        self.name = ('eidolon-close-' + uuid.uuid4().hex).encode('ascii')
        self.proxy.zName, self.proxy.pNext = self.name, None
        self.open_original = OPEN(self.original.contents.xOpen)
        self.open_callback = OPEN(self._open)
        self.proxy.xOpen = C.cast(self.open_callback, P).value
        self.files, self.retained_bundles, self.open_flags = {}, [], {}
        self.max_bundles = 512
        self.target = self.target_file = self.lane = self.phase = None
        self.acquired, self.release, self.readonly_refused = (threading.Event() for _ in range(3))
        self.events, self.failures = [], []
        self.restored = self.observation_failed = False
        self.fault = lambda stage: None
        self.hits = self.refusal_hits = 0
        self.pause_readonly_refusal = pause_readonly_refusal
        self.target_open_flags = None
        self.target_identity = None
        self.delegations = {'open': 0, 'lock': 0, 'close': 0}

    def __enter__(self):
        assert len(KEEPALIVE) < KEEPALIVE_LIMIT, 'Bounded diagnostic lifetime exceeded'
        assert self.lib.sqlite3_vfs_register(C.byref(self.proxy), 1) == 0
        KEEPALIVE.append(self)
        return self

    def __exit__(self, *unused):
        self.release.set()
        try:
            unregistered = self.lib.sqlite3_vfs_unregister(C.byref(self.proxy))
        finally:
            restored = self.lib.sqlite3_vfs_register(self.original, 1)
        assert unregistered == restored == 0
        assert C.cast(self.lib.sqlite3_vfs_find(None), P).value == C.cast(self.original, P).value
        self.restored = True
        assert not self.files, 'Probe connections must close before VFS unregister'
        assert not self.failures, self.failures
        assert not self.observation_failed, 'Probe observation incomplete'

    def arm_close(self, path):
        self.target = file_identity(path)
        self.lane = threading.get_ident()
        matches = [ptr for ptr, record in self.files.items()
                   if record[-2:] == (self.target, self.lane)]
        assert len(matches) == 1, 'Closing file identity must be unique'
        self.target_file = matches[0]
        self.target_open_flags = dict(self.open_flags[self.target_file])
        self.target_identity = {'device': self.target[0], 'inode': self.target[1],
                                'nativeOpenPath': self.files[self.target_file][-3],
                                'armedPath': str(path), 'sameFile': True}
        self.phase = 'inside_connection_close'

    def disarm_close(self):
        self.phase = None

    def _observation_error(self):
        self.observation_failed = True

    def _open(self, vfs, name, file, flags, outflags):
        # Keep the original filename pointer, including SQLite URI suffix data.
        result = self.open_original(self.original, name, file, flags, outflags)
        native_address = None
        try:
            self.delegations['open'] += 1
            if result != 0 or not flags & 0x100:
                return result
            native_address = C.cast(file, C.POINTER(P))[0]
            self.fault('open_before_proxy')
            native = checked_methods(native_address)
            proxy = type(native).from_buffer_copy(native)
            lock_original, close_original = LOCK(native.xLock), CLOSE(native.xClose)
            path = C.cast(name, C.c_char_p).value.decode('utf-8') if name else None
            identity = file_identity(path)
            returned_flags = outflags[0] if outflags else None
            readonly = returned_flags is not None and bool(returned_flags & 1) and not bool(returned_flags & 2)
            @LOCK
            def lock(ptr, level):
                rc = lock_original(ptr, level)
                try:
                    self.delegations['lock'] += 1
                    if ptr == self.target_file and threading.get_ident() == self.lane and self.phase and level == 4:
                        self.fault('lock_observation')
                        self.events.append({'phase': self.phase, 'lock': 'exclusive', 'result': rc})
                        if self.pause_readonly_refusal and readonly and rc != 0 and self.refusal_hits == 0:
                            self.refusal_hits += 1
                            self.readonly_refused.set()
                            if not self.release.wait(5):
                                self.failures.append('readonly_refusal_pause_deadline')
                        if rc == 0 and self.hits == 0:
                            self.hits += 1
                            self.acquired.set()
                            if not self.release.wait(5):
                                self.failures.append('pause_deadline')
                except BaseException:
                    self._observation_error()
                return rc
            @CLOSE
            def close(ptr):
                rc = close_original(ptr)
                try:
                    self.delegations['close'] += 1
                    self.fault('close_tracking')
                except BaseException:
                    self._observation_error()
                finally:
                    try:
                        self.files.pop(file)
                    except BaseException:
                        self._observation_error()
                return rc
            proxy.xLock, proxy.xClose = C.cast(lock, P).value, C.cast(close, P).value
            bundle = (native, proxy, lock_original, close_original, lock, close, path, identity, threading.get_ident())
            if len(self.retained_bundles) >= self.max_bundles:
                raise RuntimeError('Bounded callback retention exhausted')
            self.retained_bundles.append(bundle)
            self.files[file] = bundle
            self.open_flags[file] = {'requestedReadonly': bool(flags & 1),
                'requestedReadwrite': bool(flags & 2), 'returnedReadonly': readonly,
                'returnedFlagsPresent': returned_flags is not None,
                'methodVersion': native.iVersion}
            C.cast(file, C.POINTER(P))[0] = C.addressof(proxy)
            self.fault('open_after_proxy')
        except BaseException:
            self._observation_error()
            if native_address:
                C.cast(file, C.POINTER(P))[0] = native_address
            try:
                self.files.pop(file, None)
            except BaseException:
                self._observation_error()
        return result

    def identity(self):
        return {**self.runtime.receipt, 'candidate': candidate_identity(),
                'mechanism': 'same_process_after_real_vfs_exclusive_lock_return',
                'pauseCapSeconds': 5}

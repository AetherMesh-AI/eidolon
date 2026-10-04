"""POSIX bootstrap binding only: no stop, closure or update authority.

Anonymous pipe ownership is synchronous and nonblocking: no abandoned workers.
The startup deadline is not a Handoff negotiation or predecessor closure clock.
"""
import json
import math
import os
from pathlib import Path
import secrets
import select
import sys
import time

from eidolon_cli.update_handoff_child import GateRefused, _frame

MAX_FRAME = 16384


class CapabilityUnavailable(GateRefused):
    """Authenticated bootstrap bound, but explicitly refused CLI capability."""


def require_supported():
    if sys.platform not in ('linux', 'darwin'):
        raise GateRefused('private driver transport unsupported on this platform')


def observe(pid):
    # A supplied PID/start pair is never accepted without an independent OS read.
    import psutil
    try:
        start = psutil.Process(pid).create_time()
    except Exception as exc:
        raise GateRefused('process incarnation observation unavailable') from exc
    if not isinstance(start, (int, float)) or not math.isfinite(start) or start <= 0:
        raise GateRefused('unknown process incarnation')
    return {'pid': pid, 'start': start}


def local_paths():
    from eidolon_constants import get_eidolon_home
    return {'home': str(get_eidolon_home().resolve()),
            'install': str(Path(__file__).resolve().parents[1])}


class Transport:
    """Sole owner of unbuffered binary streams; bounded I/O without threads."""
    def __init__(self, reader, writer, *, timeout=5.0, owns_streams=True, activate=True):
        self.reader, self.writer = reader, writer
        self.owns_streams = owns_streams
        self.closed = False
        self.deadline = time.monotonic() + timeout
        try:
            require_supported()
            if not math.isfinite(timeout) or timeout <= 0:
                raise GateRefused('invalid startup deadline')
            if activate:
                self.activate()
        except BaseException:
            self.close()
            raise

    def activate(self):
        self.check()
        os.set_blocking(self.reader.fileno(), False)
        os.set_blocking(self.writer.fileno(), False)

    def check(self):
        remaining = self.deadline - time.monotonic()
        if self.closed or remaining <= 0:
            raise GateRefused('driver startup deadline expired or transport closed')
        return remaining

    def _ready(self, fd, *, write=False):
        ready = select.select([] if write else [fd], [fd] if write else [], [], self.check())
        self.check()
        if not ready[1 if write else 0]:
            raise GateRefused('driver startup transport timed out')

    def send(self, raw):
        if len(raw) > MAX_FRAME:
            raise GateRefused('oversized driver frame')
        offset = 0
        while offset < len(raw):
            self._ready(self.writer.fileno(), write=True)
            try:
                count = os.write(self.writer.fileno(), raw[offset:])
            except (BlockingIOError, InterruptedError):
                continue
            if count <= 0:
                raise GateRefused('driver write made no progress')
            offset += count
        self.check()

    def line(self):
        raw = bytearray()
        while len(raw) < MAX_FRAME:
            self._ready(self.reader.fileno())
            try:
                part = os.read(self.reader.fileno(), 1)
            except (BlockingIOError, InterruptedError):
                continue
            if not part:
                raise GateRefused('driver transport EOF')
            raw.extend(part)
            if part == b'\n':
                return bytes(raw)
        raise GateRefused('oversized driver frame')

    def write(self, value):
        self.send(json.dumps(value, allow_nan=False, sort_keys=True).encode() + b'\n')

    def read(self):
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise GateRefused('duplicate driver field')
                result[key] = value
            return result
        try:
            value = json.loads(self.line(), object_pairs_hook=pairs,
                               parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        except (ValueError, UnicodeError) as exc:
            raise GateRefused('malformed driver frame') from exc
        if not isinstance(value, dict):
            raise GateRefused('driver frame is not an object')
        return value

    def close(self):
        if self.closed:
            return
        self.closed = True
        if not self.owns_streams:
            return
        try:
            if not getattr(self.reader, 'closed', False):
                self.reader.close()
        finally:
            if not getattr(self.writer, 'closed', False):
                self.writer.close()


class ParentDriver:
    def __init__(self, handle, instance_id):
        self.handle, self.instance_id = handle, instance_id
        self.bound = False
        self.binding = None
        # Own the streams before admission, but touch no descriptor until the
        # registry has checked its stop fence and authorized bootstrap.
        self.transport = Transport(handle.stdout, handle.stdin, activate=False)

    def connect(self, token):
        try:
            self.transport.activate()
            binding = dict(local_paths(), parent=observe(os.getpid()),
                           child=observe(self.handle.pid), instance=self.instance_id,
                           transaction=secrets.token_hex(32), parent_nonce=secrets.token_hex(32))
            self.transport.send(_frame(token))
            self.transport.write({'kind': 'BIND', 'binding': binding})
            reply = self.transport.read()
            nonce = reply.get('child_nonce')
            if (reply.get('kind') != 'BOUND_DISABLED' or reply.get('binding') != binding
                    or not isinstance(nonce, str) or len(nonce) != 64
                    or any(c not in '0123456789abcdef' for c in nonce)):
                raise GateRefused('child binding mismatch')
            if (observe(self.handle.pid) != binding['child']
                    or observe(os.getpid()) != binding['parent'] or self.handle.poll() is not None):
                raise GateRefused('incarnation changed during binding')
            self.transport.check()
            self.binding = dict(binding, child_nonce=nonce)
            self.bound = True
            raise CapabilityUnavailable(
                "full lifecycle capability unavailable; backend update refused before CLI entry")
        finally:
            # This milestone refuses CLI and has no long-lived transport worker.
            # Closing the channel is not evidence of process exit or recovery.
            self.transport.close()


class ChildContext:
    def __init__(self, transport, binding):
        self.transport, self.binding = transport, binding

    def require_full_lifecycle(self):
        raise GateRefused('full lifecycle capability unavailable; backend update refused before CLI entry')


def bind_child(transport):
    message = transport.read()
    binding = message.get('binding')
    if message.get('kind') != 'BIND' or not isinstance(binding, dict):
        raise GateRefused('no live parent driver binding')
    expected = dict(local_paths(), parent=observe(os.getppid()), child=observe(os.getpid()))
    if any(binding.get(key) != value for key, value in expected.items()):
        raise GateRefused('independent child identity/path mismatch')
    if type(binding.get('instance')) is not int or binding['instance'] <= 0:
        raise GateRefused('invalid registry instance')
    for key in ('transaction', 'parent_nonce'):
        value = binding.get(key)
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise GateRefused('invalid binding nonce')
    nonce = secrets.token_hex(32)
    context = ChildContext(transport, dict(binding, child_nonce=nonce))
    transport.write({'kind': 'BOUND_DISABLED', 'binding': binding, 'child_nonce': nonce})
    transport.check()
    return context

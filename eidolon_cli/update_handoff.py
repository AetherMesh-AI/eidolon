"""Private-channel updater ownership protocol. Stdlib only, no process discovery.

Callers supply *observed* incarnation verification and registry/lifecycle adapters.
Missing adapters refuse authority. Durable receipts are diagnostic obligations,
never a source of live permissions. A single channel owner serializes each peer.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import queue
import threading
import time

MAX_FRAME = 16 * 1024
TERMINAL = frozenset({"REJECTED", "TRANSFER_UNKNOWN", "HOLD"})


class HandoffError(RuntimeError):
    pass


class FrameChannel:
    """Bounded JSON-lines on anonymous binary streams, with one live driver.

    Timeout poisons the endpoint. A blocked OS operation may outlive its caller
    on a daemon thread; the endpoint cannot authorize another exchange afterward.
    """

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.failed = False
        self._operation = threading.Lock()

    def _bounded(self, operation, timeout):
        if not math.isfinite(timeout) or timeout <= 0 or self.failed:
            raise HandoffError("channel closed or deadline expired")
        if not self._operation.acquire(blocking=False):
            self.failed = True
            raise HandoffError("concurrent channel operation")
        result = queue.Queue(maxsize=1)
        def worker():
            try:
                result.put((True, operation()))
            except BaseException as exc:
                result.put((False, exc))
        try:
            threading.Thread(target=worker, daemon=True, name="update-handoff-io").start()
            ok, value = result.get(timeout=timeout)
            if not ok:
                raise HandoffError("channel IO failed") from value
            return value
        except BaseException as exc:
            self.failed = True
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise HandoffError("channel IO failed or timed out") from exc
        finally:
            self._operation.release()

    def read(self, *, timeout):
        def receive():
            raw = self.reader.readline(MAX_FRAME + 1)
            if not raw.endswith(b"\n") or len(raw) > MAX_FRAME:
                raise HandoffError("EOF, truncated or oversized frame")
            def pairs(items):
                obj = {}
                for key, value in items:
                    if key in obj:
                        raise HandoffError("duplicate JSON key")
                    obj[key] = value
                return obj
            def nonfinite(value):
                raise HandoffError("nonfinite JSON")
            value = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
            if not isinstance(value, dict):
                raise HandoffError("message is not an object")
            return value
        return self._bounded(receive, timeout)

    def write(self, message, *, timeout):
        raw = canonical(message) + b"\n"
        if len(raw) > MAX_FRAME:
            self.failed = True
            raise HandoffError("oversized frame")
        def send():
            offset = 0
            while offset < len(raw):
                count = self.writer.write(raw[offset:])
                if not isinstance(count, int) or count <= 0:
                    raise HandoffError("short channel write")
                offset += count
            self.writer.flush()
        self._bounded(send, timeout)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


@dataclass(frozen=True)
class Identity:
    pid: int
    start: str
    instance: str

    def __post_init__(self):
        if type(self.pid) is not int or self.pid <= 0 or not isinstance(self.start, str) or not self.start:
            raise ValueError("missing observed process incarnation")
        if not re.fullmatch(r"[0-9a-f]{32}", self.instance):
            raise ValueError("invalid process instance nonce")


@dataclass(frozen=True)
class Binding:
    transaction_id: str
    action_instance_id: int
    predecessor: Identity
    successor: Identity
    install: str
    home: str
    generation: int = 1

    def __post_init__(self):
        if not re.fullmatch(r"[0-9a-f]{32}", self.transaction_id):
            raise ValueError("invalid transaction")
        if type(self.action_instance_id) is not int or self.action_instance_id <= 0:
            raise ValueError("invalid action instance")
        if type(self.generation) is not int or self.generation != 1:
            raise ValueError("a fresh channel has exactly one generation")
        if self.predecessor == self.successor or not all(os.path.isabs(p) for p in (self.install, self.home)):
            raise ValueError("invalid launch identity binding")


class ReceiptStore:
    """Atomic, read-verified receipt sink in a pre-existing private directory.

    POSIX verifies owner/mode and pins the directory descriptor. Windows requires
    an injected ACL-aware sink; do not pretend POSIX chmod establishes an ACL.
    Separate peer files avoid two independent processes overwriting obligations.
    """
    def __init__(self, directory, role):
        if role not in ("predecessor", "successor"):
            raise ValueError("invalid peer role")
        self.directory, self.role = Path(directory), role

    def _open_directory(self):
        if os.name == "nt":
            raise HandoffError("private Windows receipt ACL adapter required")
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        s = os.fstat(fd)
        if s.st_uid != os.getuid() or s.st_mode & 0o077:
            os.close(fd)
            raise HandoffError("receipt directory must be private and owned")
        return fd

    def _name(self, transaction):
        if not re.fullmatch(r"[0-9a-f]{32}", transaction):
            raise ValueError("invalid receipt transaction")
        return f"{transaction}-{self.role}.json"

    def read(self, transaction):
        directory = self._open_directory()
        try:
            fd = os.open(self._name(transaction), os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            with os.fdopen(fd, "rb") as stream:
                s = os.fstat(stream.fileno())
                if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o077:
                    raise HandoffError("unsafe receipt file")
                raw = stream.read(MAX_FRAME + 1)
                if len(raw) > MAX_FRAME:
                    raise HandoffError("oversized receipt")
                return json.loads(raw)
        finally:
            os.close(directory)

    def write(self, transaction, receipt):
        data = canonical(receipt)
        if len(data) > MAX_FRAME:
            raise HandoffError("oversized receipt")
        directory = self._open_directory()
        temporary = "." + self._name(transaction) + "." + os.urandom(16).hex()
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._name(transaction), src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
            fd = os.open(self._name(transaction), os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            with os.fdopen(fd, "rb") as stream:
                if stream.read(MAX_FRAME + 1) != data:
                    raise HandoffError("receipt write verification failed")
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
            os.close(directory)


class Handoff:
    def __init__(self, binding, role, store, verify, *, clock=time.monotonic,
                 prepare=None, acknowledge=None, authorize=None):
        if role not in ("predecessor", "successor"):
            raise ValueError("invalid peer role")
        self.binding, self.role, self.store, self.verify = binding, role, store, verify
        self.clock, self.deadline = clock, clock() + 10.0
        self.prepare, self.acknowledge, self.authorize = prepare, acknowledge, authorize
        self.phase = "RUNNING"
        self.obligation = None
        self.failures = []
        self._sent = 0
        self._received = 0
        self._last = None
        self._response = None
        self._offer_digest = None
        self.stop_authorized = False
        self.replacement_allowed = False  # Only affirmative closure + exit may change this.
        self.acknowledged = False

    def _persist(self, phase):
        self.store.write(self.binding.transaction_id, {
            "version": 1, "binding": asdict(self.binding), "role": self.role,
            "phase": phase, "obligation": self.obligation,
            "failures": list(self.failures), "timestamp": time.time(),
            "evidence_scope": "registered-direct-actions",
        })
        self.phase = phase

    def abort(self, reason):
        if self.phase not in TERMINAL:
            self.phase = ("HOLD" if self.acknowledged or self.stop_authorized else
                          "TRANSFER_UNKNOWN" if self.phase in {"COMMITTED", "OWNED"} else "REJECTED")
            self.failures.append(str(reason))
            try:
                self._persist(self.phase)
            except Exception:
                self.failures.append("terminal receipt persistence failed")
        self.stop_authorized = False
        self.replacement_allowed = False
        raise HandoffError(f"{self.phase}: {reason}")

    def _check(self):
        if self.phase in TERMINAL:
            raise HandoffError(f"terminal handoff: {self.phase}")
        if self.clock() >= self.deadline:
            self.abort("negotiation deadline expired")
        if self.verify(self.binding) is not True:
            self.abort("incarnation not independently verified")
        # Independent verification itself may consume the remaining budget.
        if self.clock() >= self.deadline:
            self.abort("negotiation deadline expired")

    def _emit(self, kind, payload):
        self._sent += 1
        message = dict(asdict(self.binding), version=1, sequence=self._sent, type=kind, payload=payload)
        if len(canonical(message)) + 1 > MAX_FRAME:
            raise HandoffError("frame too large")
        return json.loads(canonical(message))

    def request(self, plan):
        try:
            self._check()
            if self.role != "successor" or self.phase != "RUNNING" or not isinstance(plan, dict) or not plan:
                raise HandoffError("unexpected recovery request")
            self.obligation = json.loads(canonical(plan))
            self._persist("REQUESTED")
            return self._emit("STOP_FOR_UPDATE", {"plan": self.obligation})
        except Exception as exc:
            self.abort(str(exc))

    def receive(self, message):
        try:
            self._check()
            if message is None:
                raise HandoffError("EOF is not permission")
            raw = canonical(message)
            if len(raw) + 1 > MAX_FRAME or not isinstance(message, dict):
                raise HandoffError("malformed/oversized frame")
            required = set(asdict(self.binding)) | {"version", "sequence", "type", "payload"}
            if set(message) != required or type(message["version"]) is not int or message["version"] != 1:
                raise HandoffError("unexpected frame fields/version")
            for key, value in asdict(self.binding).items():
                if canonical(message[key]) != canonical(value):
                    raise HandoffError(f"conflicting {key}")
            seq = message["sequence"]
            if type(seq) is not int or seq <= 0 or seq > 8:
                raise HandoffError("invalid finite sequence")
            if seq == self._received and raw == self._last:
                return json.loads(canonical(self._response))
            if seq != self._received + 1:
                raise HandoffError("stale, replayed or skipped sequence")
            kind, payload = message["type"], message["payload"]
            if not isinstance(payload, dict):
                raise HandoffError("invalid payload")
            response = self._transition(kind, payload)
            self._received, self._last, self._response = seq, raw, response
            return json.loads(canonical(response))
        except Exception as exc:
            self.abort(str(exc))

    def _transition(self, kind, payload):
        if self.role == "predecessor":
            if self.phase == "RUNNING" and kind == "STOP_FOR_UPDATE":
                if set(payload) != {"plan"} or not isinstance(payload["plan"], dict) or not payload["plan"]:
                    raise HandoffError("missing recovery obligation")
                if self.prepare is None:
                    raise HandoffError("no registry drain adapter")
                closure = self.prepare(payload["plan"])
                if closure != {"status": "complete", "evidence_scope": "registered-direct-actions", "unresolved": []}:
                    raise HandoffError("other direct actions are not closed")
                self.obligation = payload["plan"]
                offer = {"plan": self.obligation, "closure": closure}
                self._offer_digest = digest(dict(binding=asdict(self.binding), offer=offer))
                self._persist("OFFERED")
                return self._emit("OFFER", dict(offer, digest=self._offer_digest))
            if self.phase == "OFFERED" and kind == "ACCEPT":
                self._require_digest(payload)
                # Uncertain persistence of COMMIT is already uncertain ownership.
                self.phase = "COMMITTED"
                self._persist("COMMITTED")
                return self._emit("COMMIT", payload)
            if self.phase == "COMMITTED" and kind == "ACK":
                self._require_digest(payload)
                self._persist("ACKNOWLEDGED")
                self.acknowledged = True
                self._check()  # Durable I/O is not a liveness guarantee.
                if self.acknowledge is None or self.authorize is None:
                    raise HandoffError("no live registry authorization adapter")
                self.acknowledge(self)
                # This durable record is a proposal, not published live authority.
                # Ordinary stop may still win while persistence/verification runs.
                self._persist("STOP_AUTHORIZED")
                self._check()
                if self.authorize(self) is not True:
                    raise HandoffError("ordinary stop or unresolved drain won")
                self.stop_authorized = True
                return self._emit("STOP_AUTHORIZED", payload)
        else:
            if self.phase == "REQUESTED" and kind == "OFFER":
                if set(payload) != {"plan", "closure", "digest"} or payload["plan"] != self.obligation:
                    raise HandoffError("offer changed recovery obligation")
                closure = {"status": "complete", "evidence_scope": "registered-direct-actions", "unresolved": []}
                if payload["closure"] != closure:
                    raise HandoffError("incomplete direct-action offer")
                self._offer_digest = digest(dict(binding=asdict(self.binding), offer={"plan": self.obligation, "closure": closure}))
                self._require_digest({"digest": payload["digest"]})
                self._persist("ACCEPTED")  # Installs obligation before ACCEPT.
                return self._emit("ACCEPT", {"digest": self._offer_digest})
            if self.phase == "ACCEPTED" and kind == "COMMIT":
                self._require_digest(payload)
                self.phase = "OWNED"
                self._persist("OWNED")
                return self._emit("ACK", payload)
            if self.phase == "OWNED" and kind == "STOP_AUTHORIZED":
                self._require_digest(payload)
                # The recovery obligation is already owned. Even uncertain final
                # persistence must retain HOLD, never discard that obligation.
                self.acknowledged = True
                self._persist("STOP_AUTHORIZED")
                self._check()
                self.stop_authorized = True
                return None
        raise HandoffError(f"unexpected {kind} in {self.role}/{self.phase}")

    def _require_digest(self, payload):
        if payload != {"digest": self._offer_digest} or self._offer_digest is None:
            raise HandoffError("offer digest mismatch")

#!/usr/bin/env python3
"""Private, observation-only process identities for the native test fixture.

The caller owns Electron's ChildProcess and verifies it around ``root``. Only
``capture`` traverses children, directly from that verified root. ``wait`` never
discovers processes. Neither mode signals a process or reads its command line,
environment, files, or name. Identity-bearing replies must stay private.

POSIX real UIDs are required. There is deliberately no Windows fallback or
Windows runtime support claim. The caller also enforces hard 2s/6s timeouts on
this helper, including interpreter startup and stdin handling.
"""

from __future__ import annotations

import time

_START_MONOTONIC = time.monotonic()

import json
import math
import os
import queue
import sys
import threading

MAX_DESCENDANTS = 64
MAX_INPUT_BYTES = 16_384
POLL_SECONDS = 0.025
REASONS = frozenset({
    "invalid", "identity_changed", "inspection_denied", "inspection_failed",
    "limit", "timeout",
})


class Refusal(Exception):
    """A closed-schema failure; never include an underlying exception."""

    def __init__(self, reason):
        self.reason = reason


class Gone(Exception):
    pass


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def _birth(value):
    return (type(value) in (int, float) and 0 < value <= 2**53 - 1
            and math.isfinite(value))


def _identity(value):
    return (type(value) is dict and set(value) == {"pid", "birth", "uid"}
            and _integer(value["pid"], 1, 2**31 - 1)
            and _birth(value["birth"])
            and _integer(value["uid"], 0, 2**32 - 1))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refusal("invalid")
        result[key] = value
    return result


def read_request(stream):
    data = stream.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise Refusal("limit")
    try:
        return json.loads(data, object_pairs_hook=_unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(Refusal("invalid")))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise Refusal("invalid") from None


class Deadline:
    def __init__(self, seconds, deadline_epoch_ms=None, started=None):
        self.monotonic_end = (_START_MONOTONIC if started is None else started) + seconds
        self.epoch_end = None
        if deadline_epoch_ms is not None:
            self.epoch_end = deadline_epoch_ms / 1000
            self.monotonic_end = min(self.monotonic_end,
                                     time.monotonic() + self.epoch_end - time.time())

    def remaining(self):
        remaining = self.monotonic_end - time.monotonic()
        if self.epoch_end is not None:
            remaining = min(remaining, self.epoch_end - time.time())
        return max(0.0, remaining)

    def check(self):
        if self.remaining() <= 0:
            raise Refusal("timeout")


class Observer:
    def __init__(self, psutil, deadline):
        self.psutil = psutil
        self.deadline = deadline

    def inspect(self, operation):
        self.deadline.check()
        try:
            value = operation()
        except self.psutil.NoSuchProcess:
            raise Gone from None
        except self.psutil.AccessDenied:
            raise Refusal("inspection_denied") from None
        except Exception:
            raise Refusal("inspection_failed") from None
        self.deadline.check()
        return value

    def snapshot(self, pid, parent_pid=None):
        # New Process objects are essential: psutil caches create_time per object.
        process = self.inspect(lambda: self.psutil.Process(pid))
        birth = self.inspect(process.create_time)
        uid = self.inspect(lambda: process.uids().real)
        if not _birth(birth) or not _integer(uid, 0, 2**32 - 1):
            raise Refusal("inspection_failed")
        if parent_pid is not None:
            ppid = self.inspect(process.ppid)
            if not _integer(ppid, 1, 2**31 - 1) or ppid != parent_pid:
                raise Refusal("identity_changed")
        status = self.inspect(process.status)
        if not isinstance(status, str) or not status:
            raise Refusal("inspection_failed")
        if status in {self.psutil.STATUS_ZOMBIE, self.psutil.STATUS_DEAD}:
            raise Gone
        return {"pid": pid, "birth": birth, "uid": uid}

    def capture_identity(self, pid, parent_pid=None):
        identity = self.snapshot(pid, parent_pid)
        if self.snapshot(pid, parent_pid) != identity:
            raise Refusal("identity_changed")
        return identity

    def verify(self, expected):
        try:
            actual = self.capture_identity(expected["pid"])
        except Gone:
            raise Refusal("identity_changed") from None
        if actual != expected:
            raise Refusal("identity_changed")

    def live(self, expected):
        try:
            # Check birth before UID: a reused PID is the original's exit, even
            # if its replacement has a different owner or inaccessible metadata.
            for _ in range(2):
                process = self.inspect(lambda: self.psutil.Process(expected["pid"]))
                birth = self.inspect(process.create_time)
                if not _birth(birth):
                    raise Refusal("inspection_failed")
                if birth != expected["birth"]:
                    return False
                uid = self.inspect(lambda: process.uids().real)
                if not _integer(uid, 0, 2**32 - 1):
                    raise Refusal("inspection_failed")
                if uid != expected["uid"]:
                    raise Refusal("identity_changed")
                status = self.inspect(process.status)
                if not isinstance(status, str) or not status:
                    raise Refusal("inspection_failed")
                if status in {self.psutil.STATUS_ZOMBIE, self.psutil.STATUS_DEAD}:
                    return False
            return True
        except Gone:
            return False

    def capture(self, root):
        result = []
        seen = set()

        def traverse(parent):
            self.verify(parent)
            if parent["pid"] in seen:
                raise Refusal("identity_changed")
            if len(seen) >= MAX_DESCENDANTS + 1:
                raise Refusal("limit")
            seen.add(parent["pid"])
            result.append(parent)
            process = self.inspect(lambda: self.psutil.Process(parent["pid"]))
            children = self.inspect(lambda: process.children(recursive=False))
            self.verify(parent)
            if type(children) is not list:
                raise Refusal("inspection_failed")
            if len(children) > MAX_DESCENDANTS:
                raise Refusal("limit")
            direct = set()
            for child in children:
                self.verify(parent)
                child_pid = self.inspect(lambda: child.pid)
                if not _integer(child_pid, 1, 2**31 - 1):
                    raise Refusal("inspection_failed")
                if child_pid in direct or child_pid in seen:
                    raise Refusal("identity_changed")
                direct.add(child_pid)
                try:
                    listed_birth = self.inspect(child.create_time)
                    if not _birth(listed_birth):
                        raise Refusal("inspection_failed")
                    identity = self.capture_identity(child_pid, parent["pid"])
                except Gone:
                    # Only omit a vanished child if its exact parent survives.
                    self.verify(parent)
                    continue
                self.verify(parent)
                if identity["birth"] != listed_birth or identity["uid"] != parent["uid"]:
                    raise Refusal("identity_changed")
                traverse(identity)
                self.verify(parent)
                # Check the original direct linkage after the recursive walk.
                # If this child has ended, its captured identity remains safe to
                # wait on; a replacement/reparenting is never silently accepted.
                try:
                    after = self.capture_identity(child_pid, parent["pid"])
                except Gone:
                    after = identity
                self.verify(parent)
                if after != identity:
                    raise Refusal("identity_changed")
            self.verify(parent)

        traverse(root)
        return result

    def wait(self, identities, summary):
        active = identities[:]
        root_pid = identities[0]["pid"]
        while True:
            self.deadline.check()
            summary["remainingLive"] = None
            summary["rootExited"] = None
            remaining = []
            root_exited = all(identity["pid"] != root_pid for identity in active)
            summary["rootExited"] = root_exited if root_exited else None
            for identity in active:
                alive = self.live(identity)
                if identity["pid"] == root_pid:
                    summary["rootExited"] = not alive
                if alive:
                    remaining.append(identity)
            summary["remainingLive"] = len(remaining)
            if summary["initialLive"] is None:
                summary["initialLive"] = len(remaining)
            if not remaining:
                return {"ok": True, **summary, "reason": "none"}
            active = remaining
            self.deadline.check()
            time.sleep(min(POLL_SECONDS, self.deadline.remaining()))


def failure(reason, summary=None):
    return {"ok": False, "reason": reason, **(summary or {})}


def run(request, psutil=None, *, started=None):
    """Execute a validated request; the daemon bounds even stuck inspection."""
    summary = None
    try:
        if type(request) is not dict:
            raise Refusal("invalid")
        mode = request.get("mode")
        if mode == "root":
            if (set(request) != {"mode", "pid", "uid"}
                    or not _integer(request["pid"], 1, 2**31 - 1)
                    or not _integer(request["uid"], 0, 2**32 - 1)):
                raise Refusal("invalid")
        elif mode == "capture":
            if set(request) != {"mode", "root"} or not _identity(request["root"]):
                raise Refusal("invalid")
        elif mode == "wait":
            identities = request.get("identities")
            if type(identities) is not list or not 1 <= len(identities) <= MAX_DESCENDANTS + 1:
                raise Refusal("invalid")
            summary = {"capturedCount": len(identities), "initialLive": None,
                       "remainingLive": None, "rootExited": None}
            if (set(request) != {"mode", "identities", "deadlineEpochMs"}
                    or not all(_identity(identity) for identity in identities)
                    or len({identity["pid"] for identity in identities}) != len(identities)
                    or len({identity["uid"] for identity in identities}) != 1
                    or not _integer(request["deadlineEpochMs"], 1, 2**53 - 1)):
                raise Refusal("invalid")
        else:
            raise Refusal("invalid")
        deadline = Deadline(6 if mode == "wait" else 2,
                            request.get("deadlineEpochMs"), started)
        deadline.check()
        if os.name != "posix" or not hasattr(os, "getuid"):
            raise Refusal("inspection_failed")
        responses = queue.Queue(maxsize=1)

        def observe():
            try:
                module = psutil
                if module is None:
                    import psutil as module
                observer = Observer(module, deadline)
                if mode == "root":
                    try:
                        root = observer.capture_identity(request["pid"])
                    except Gone:
                        raise Refusal("identity_changed") from None
                    if root["uid"] != request["uid"]:
                        raise Refusal("identity_changed")
                    result = {"ok": True, "root": root}
                elif mode == "capture":
                    result = {"ok": True, "identities": observer.capture(request["root"])}
                else:
                    result = observer.wait(identities, summary)
                deadline.check()
            except Refusal as error:
                result = failure(error.reason, summary)
            except Gone:
                result = failure("identity_changed", summary)
            except Exception:
                result = failure("inspection_failed", summary)
            responses.put(result)

        threading.Thread(target=observe, daemon=True).start()
        try:
            return responses.get(timeout=deadline.remaining())
        except queue.Empty:
            return failure("timeout", dict(summary) if summary is not None else None)
    except Refusal as error:
        return failure(error.reason, summary)


def main():
    try:
        result = run(read_request(sys.stdin.buffer))
    except Refusal as error:
        result = failure(error.reason)
    except Exception:
        result = failure("inspection_failed")
    # Exactly one closed-schema JSON value. Never print inspection exceptions.
    sys.stdout.write(json.dumps(result, allow_nan=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()

"""Exercise the actual fixture-only lifecycle helper; never a production runtime.

The fake psutil implementation forbids scans and signals. Real subprocess proofs
are POSIX-only and close owned input pipes to let their descendants exit normally.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

HELPER = Path(__file__).resolve().parents[2] / "apps/desktop/e2e/subscription-native-lifecycle.py"
spec = importlib.util.spec_from_file_location("subscription_native_lifecycle", HELPER)
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)


@pytest.fixture(params=[
    pytest.param("linux", marks=pytest.mark.linux_only),
    pytest.param("darwin", marks=pytest.mark.macos_only),
])
def posix_host(request):
    """Exercise the real host; each collected case has exactly one OS marker."""
    assert sys.platform == request.param
    assert os.name == "posix" and hasattr(os, "getuid")


class FakePsutil:
    class NoSuchProcess(Exception):
        pass

    class AccessDenied(Exception):
        pass

    STATUS_ZOMBIE = "zombie"
    STATUS_DEAD = "dead"

    def __init__(self):
        self.records = {}
        self.calls = []
        self.forbidden_calls = []
        self.hook = lambda pid, method: None

    def add(self, pid, *, birth=None, uid=1000, ppid=0, children=(), status="running"):
        self.records[pid] = dict(birth=float(pid) if birth is None else birth,
                                 uid=uid, ppid=ppid, children=list(children), status=status)
        return self

    def lookup(self, pid, method):
        self.calls.append((pid, method))
        self.hook(pid, method)
        if pid not in self.records:
            raise self.NoSuchProcess()
        return self.records[pid]

    def Process(self, pid):
        owner = self
        # Like real psutil, retain a cached birth on each individual object.
        cached_birth = self.lookup(pid, "construct")["birth"]

        class Process:
            def __init__(self):
                self.pid = pid

            def create_time(self):
                owner.lookup(pid, "birth")
                return cached_birth

            def uids(self):
                return SimpleNamespace(real=owner.lookup(pid, "uid")["uid"])

            def ppid(self):
                return owner.lookup(pid, "ppid")["ppid"]

            def status(self):
                return owner.lookup(pid, "status")["status"]

            def children(self, recursive=False):
                assert recursive is False, "Only direct child traversal is allowed"
                children = []
                for child in owner.lookup(pid, "children")["children"]:
                    birth = owner.records.get(child, {}).get("birth")

                    def listed_birth(child_pid=child, cached=birth):
                        owner.lookup(child_pid, "listed_birth")
                        return cached

                    children.append(SimpleNamespace(pid=child, create_time=listed_birth))
                return children

            def kill(self):
                owner.forbidden("kill")

            def terminate(self):
                owner.forbidden("terminate")

            def send_signal(self, *_args):
                owner.forbidden("send_signal")

            def cmdline(self):
                owner.forbidden("cmdline")

            def environ(self):
                owner.forbidden("environ")

            def name(self):
                owner.forbidden("name")

            def open_files(self):
                owner.forbidden("open_files")

        return Process()

    def process_iter(self, *args, **kwargs):
        self.forbidden("process_iter")

    def pids(self):
        self.forbidden("pids")

    def forbidden(self, operation):
        self.forbidden_calls.append(operation)
        raise AssertionError("Lifecycle observation must not inspect unrelated state or signal processes")


def identity(pid=10, birth=None, uid=1000):
    return {"pid": pid, "birth": float(pid) if birth is None else birth, "uid": uid}


def run(request, fake, *, seconds_ago=0):
    return lifecycle.run(request, fake, started=time.monotonic() - seconds_ago)


def wait_request(identities=None, *, milliseconds=500):
    return {"mode": "wait", "identities": identities or [identity()],
            "deadlineEpochMs": int(time.time() * 1000) + milliseconds}


def assert_failure(result, reason, *, count=None):
    expected = {"ok", "reason"}
    if "diagnostic" in result:
        expected.add("diagnostic")
        detail = result["diagnostic"]
        assert reason == "identity_changed" and count is None
        assert set(detail) == {"check", "scope", "component"}
        assert detail["check"] in {"parent_link", "snapshot", "verify", "duplicate", "listed_child", "post_walk", "unhandled_disappearance"}
        assert detail["scope"] in {"root", "descendant", "unknown"}
        assert detail["component"] in {"birth", "uid", "pid", "ppid", "presence", "identity"}
    if count is not None:
        expected |= {"capturedCount", "initialLive", "remainingLive", "rootExited"}
        assert result["capturedCount"] == count
        for field in ("initialLive", "remainingLive"):
            assert result[field] is None or type(result[field]) is int and 0 <= result[field] <= count
        assert result["rootExited"] is None or type(result["rootExited"]) is bool
    assert set(result) == expected
    assert result["ok"] is False
    assert result["reason"] == reason


def test_root_requires_exact_live_real_uid(posix_host):
    fake = FakePsutil().add(10)
    assert run({"mode": "root", "pid": 10, "uid": 1000}, fake) == {
        "ok": True, "root": identity(),
    }
    assert_failure(run({"mode": "root", "pid": 10, "uid": 1001}, fake), "identity_changed")
    assert_failure(run({"mode": "root", "pid": 11, "uid": 1000}, fake), "identity_changed")


@pytest.mark.parametrize("payload", [
    None, [], {}, {"mode": "unknown"},
    {"mode": "root", "pid": True, "uid": 1000},
    {"mode": "root", "pid": 0, "uid": 1000},
    {"mode": "root", "pid": 10, "uid": -1},
    {"mode": "root", "pid": 10, "uid": 1000, "extra": "private"},
    {"mode": "capture", "root": identity(), "extra": True},
    {"mode": "capture", "root": {**identity(), "extra": True}},
    {"mode": "capture", "root": {**identity(), "birth": float("nan")}},
    {"mode": "capture", "root": {**identity(), "birth": 10**1000}},
    {"mode": "capture", "root": {"pid": 10, "uid": 1000}},
])
def test_invalid_inputs_do_not_inspect(payload):
    fake = FakePsutil()
    assert_failure(run(payload, fake), "invalid")
    assert not fake.calls


@pytest.mark.parametrize("raw", [
    b'{"mode":"root","mode":"capture","pid":10,"uid":1000}',
    b'{"mode":"capture","root":{"pid":10,"birth":1,"uid":1000,"uid":1000}}',
    b'{"mode":"root","pid":NaN,"uid":1000}', b'{} garbage', b'\xff', b'{',
])
def test_json_input_rejects_duplicates_nonfinite_or_partial(raw):
    with pytest.raises(lifecycle.Refusal) as error:
        lifecycle.read_request(io.BytesIO(raw))
    assert error.value.reason == "invalid"


def test_input_read_has_a_hard_size_bound():
    with pytest.raises(lifecycle.Refusal) as error:
        lifecycle.read_request(io.BytesIO(b" " * (lifecycle.MAX_INPUT_BYTES + 1)))
    assert error.value.reason == "limit"


def test_capture_follows_only_direct_same_uid_descendants(posix_host):
    fake = (FakePsutil().add(10, children=[20, 30]).add(20, ppid=10, children=[40])
            .add(30, ppid=10).add(40, ppid=20).add(99, uid=2000))
    result = run({"mode": "capture", "root": identity()}, fake)
    assert result == {"ok": True, "identities": [identity(10), identity(20), identity(40), identity(30)]}
    assert all(pid != 99 for pid, _ in fake.calls)
    assert [pid for pid, method in fake.calls if method == "children"] == [10, 20, 40, 30]


@pytest.mark.parametrize("field,value", [("birth", 11.0), ("uid", 1001)])
def test_capture_rejects_substituted_root(posix_host, field, value):
    fake = FakePsutil().add(10)
    fake.records[10][field] = value
    assert_failure(run({"mode": "capture", "root": identity()}, fake), "identity_changed")


@pytest.mark.parametrize("field,value", [("ppid", 9), ("uid", 1001)])
def test_capture_rejects_unlinked_or_cross_uid_child(posix_host, field, value):
    fake = FakePsutil().add(10, children=[20]).add(20, ppid=10)
    fake.records[20][field] = value
    assert_failure(run({"mode": "capture", "root": identity()}, fake), "identity_changed")


@pytest.mark.parametrize("field,value", [("birth", 999.0), ("uid", 1001)])
@pytest.mark.parametrize("at", ["children", "nested_children"])
def test_capture_revalidates_parent_around_each_traversal(posix_host, field, value, at):
    fake = FakePsutil().add(10, children=[20]).add(20, ppid=10)

    def substitute(pid, method):
        if method == "children" and pid == (10 if at == "children" else 20):
            fake.records[10][field] = value

    fake.hook = substitute
    assert_failure(run({"mode": "capture", "root": identity()}, fake), "identity_changed")


def test_capture_uses_fresh_objects_to_detect_cached_birth_pid_reuse(posix_host):
    fake = FakePsutil().add(10)

    def substitute(pid, method):
        if method == "uid":
            fake.records[pid]["birth"] = 99.0

    fake.hook = substitute
    assert_failure(run({"mode": "root", "pid": 10, "uid": 1000}, fake), "identity_changed")


def test_capture_rejects_child_reparented_during_recursive_walk(posix_host):
    fake = FakePsutil().add(10, children=[20]).add(20, ppid=10)

    def reparent(pid, method):
        if (pid, method) == (20, "children"):
            fake.records[20]["ppid"] = 99

    fake.hook = reparent
    assert_failure(run({"mode": "capture", "root": identity()}, fake), "identity_changed")


def test_capture_can_omit_disappeared_child_only_after_parent_revalidation(posix_host):
    fake = FakePsutil().add(10, children=[20])
    assert run({"mode": "capture", "root": identity()}, fake) == {
        "ok": True, "identities": [identity()],
    }

    def change_parent(pid, method):
        if (pid, method) == (20, "listed_birth"):
            fake.records[10]["uid"] = 2000

    fake.hook = change_parent
    assert_failure(run({"mode": "capture", "root": identity()}, fake), "identity_changed")


def test_capture_rejects_pid_reuse_after_child_enumeration(posix_host):
    fake = FakePsutil().add(10, children=[20]).add(20, ppid=10)

    def substitute(pid, method):
        if (pid, method) == (20, "listed_birth"):
            fake.records[20]["birth"] = 200.0

    fake.hook = substitute
    assert_failure(run({"mode": "capture", "root": identity()}, fake), "identity_changed")


@pytest.mark.parametrize("shape", ["wide", "deep"])
def test_capture_refuses_more_than_64_descendants(posix_host, shape):
    fake = FakePsutil()
    if shape == "wide":
        fake.add(10, children=range(20, 85))
    else:
        for pid in range(10, 76):
            fake.add(pid, ppid=pid - 1, children=[pid + 1] if pid < 75 else [])
    assert_failure(run({"mode": "capture", "root": identity()}, fake), "limit")


def test_capture_accepts_exactly_64_descendants(posix_host):
    fake = FakePsutil().add(10, children=range(20, 84))
    for pid in range(20, 84):
        fake.add(pid, ppid=10)
    result = run({"mode": "capture", "root": identity()}, fake)
    assert result["ok"] is True
    assert len(result["identities"]) == 65


@pytest.mark.parametrize("children", [[20, 20], [10]])
def test_capture_rejects_duplicate_or_cyclic_linkage(posix_host, children):
    fake = FakePsutil().add(10, children=children).add(20, ppid=10)
    assert_failure(run({"mode": "capture", "root": identity()}, fake), "identity_changed")


@pytest.mark.parametrize("error_type,reason", [
    (FakePsutil.AccessDenied, "inspection_denied"), (RuntimeError, "inspection_failed"),
])
def test_inspection_failure_is_closed_schema_and_never_leaks(posix_host, error_type, reason):
    fake = FakePsutil().add(10)

    def fail(pid, method):
        raise error_type("secret path /private/profile token=do-not-print")

    fake.hook = fail
    assert_failure(run({"mode": "capture", "root": identity()}, fake), reason)
    result = run(wait_request(), fake)
    assert_failure(result, reason, count=1)
    assert result["initialLive"] is result["remainingLive"] is result["rootExited"] is None
    assert "private" not in json.dumps(result)


def test_wait_reused_pid_is_original_exit_without_inspecting_replacement_uid(posix_host):
    fake = FakePsutil().add(10, birth=999.0, uid=2000)
    result = run(wait_request(), fake)
    assert result == {"ok": True, "capturedCount": 1, "initialLive": 0,
                      "remainingLive": 0, "rootExited": True, "reason": "none"}
    assert not any(method in {"uid", "status", "children"} for _, method in fake.calls)


def test_wait_same_birth_uid_change_refuses(posix_host):
    result = run(wait_request(), FakePsutil().add(10, uid=2000))
    assert_failure(result, "identity_changed", count=1)
    assert result["initialLive"] is result["remainingLive"] is result["rootExited"] is None


def test_wait_late_exit_tracks_only_captured_identities(posix_host):
    fake = FakePsutil().add(10, children=[20, 99]).add(20, ppid=10).add(99, ppid=10)
    scans = 0

    def exit_later(pid, method):
        nonlocal scans
        if (pid, method) == (20, "construct"):
            scans += 1
            if scans == 3:
                fake.records.pop(10, None)
                fake.records.pop(20, None)

    fake.hook = exit_later
    result = run(wait_request([identity(), identity(20)]), fake)
    assert result == {"ok": True, "capturedCount": 2, "initialLive": 2,
                      "remainingLive": 0, "rootExited": True, "reason": "none"}
    assert all(pid in {10, 20} and method != "children" for pid, method in fake.calls)
    assert 99 in fake.records


def test_wait_partial_scan_keeps_unknown_counts_null(posix_host):
    fake = FakePsutil().add(10).add(20)

    def fail_child(pid, method):
        if pid == 20:
            raise fake.AccessDenied()

    fake.hook = fail_child
    result = run(wait_request([identity(), identity(20)]), fake)
    assert_failure(result, "inspection_denied", count=2)
    assert result["initialLive"] is result["remainingLive"] is None
    assert result["rootExited"] is False


@pytest.mark.parametrize("kind", ["root", "capture", "wait"])
def test_hung_inspection_is_bounded_without_signals(posix_host, kind):
    fake = FakePsutil().add(10)
    release = threading.Event()
    entered = threading.Event()

    def block(pid, method):
        entered.set()
        release.wait(10)

    fake.hook = block
    request = ({"mode": "root", "pid": 10, "uid": 1000} if kind == "root" else
               {"mode": "capture", "root": identity()} if kind == "capture" else
               wait_request(milliseconds=100))
    start = time.monotonic()
    try:
        result = run(request, fake, seconds_ago=1.9 if kind != "wait" else 0)
    finally:
        release.set()
    assert entered.is_set()
    assert time.monotonic() - start < 3
    assert_failure(result, "timeout", count=1 if kind == "wait" else None)
    if kind == "wait":
        assert result["initialLive"] is result["remainingLive"] is result["rootExited"] is None


def test_wait_deadline_includes_startup_and_expired_request_never_inspects():
    fake = FakePsutil().add(10)
    assert_failure(run(wait_request(milliseconds=-1), fake), "timeout", count=1)
    assert_failure(run(wait_request(milliseconds=10_000), fake, seconds_ago=6.1), "timeout", count=1)
    assert not fake.calls


def test_deadline_uses_both_monotonic_and_epoch_clocks(monkeypatch):
    now = {"wall": 100.0, "mono": 200.0}
    monkeypatch.setattr(lifecycle.time, "time", lambda: now["wall"])
    monkeypatch.setattr(lifecycle.time, "monotonic", lambda: now["mono"])
    deadline = lifecycle.Deadline(6, 106_000, started=200)
    now.update(wall=90.0, mono=207.0)
    with pytest.raises(lifecycle.Refusal, match="timeout"):
        deadline.check()
    now.update(wall=100.0, mono=200.0)
    deadline = lifecycle.Deadline(6, 106_000, started=200)
    now.update(wall=107.0, mono=201.0)
    with pytest.raises(lifecycle.Refusal, match="timeout"):
        deadline.check()


def test_wait_timeout_reports_only_last_complete_observation(posix_host):
    result = run(wait_request(milliseconds=60), FakePsutil().add(10))
    assert_failure(result, "timeout", count=1)
    assert result["initialLive"] == 1
    assert result["remainingLive"] in (1, None)
    assert result["rootExited"] in (False, None)


@pytest.mark.parametrize("change", [
    lambda request: request.update(extra=True),
    lambda request: request.update(deadlineEpochMs=True),
    lambda request: request.update(identities=[identity(), identity()]),
    lambda request: request.update(identities=[identity(), identity(20, uid=2000)]),
    lambda request: request.update(identities=[{**identity(), "extra": True}]),
])
def test_wait_input_schema_rejects_unknown_or_duplicate_identities(change):
    request = wait_request()
    change(request)
    fake = FakePsutil()
    assert_failure(run(request, fake), "invalid", count=len(request["identities"]))
    assert not fake.calls


def test_observation_never_signals_or_inspects_unrelated_state(posix_host, monkeypatch):
    fake = FakePsutil().add(10, children=[20]).add(20, ppid=10).add(99)
    monkeypatch.setattr(lifecycle.os, "kill", lambda *_: fake.forbidden("os.kill"))
    monkeypatch.setattr(lifecycle.os, "killpg", lambda *_: fake.forbidden("os.killpg"))
    root = run({"mode": "root", "pid": 10, "uid": 1000}, fake)
    assert root["ok"] is True
    captured = run({"mode": "capture", "root": root["root"]}, fake)
    assert captured["ok"] is True
    fake.records.pop(10)
    fake.records.pop(20)
    assert run(wait_request(captured["identities"]), fake)["ok"] is True
    assert fake.forbidden_calls == []
    assert all(pid != 99 for pid, _ in fake.calls)


@pytest.mark.windows_only
def test_windows_runtime_refuses_without_inspection():
    assert sys.platform == "win32"
    fake = FakePsutil()
    assert_failure(run({"mode": "root", "pid": 10, "uid": 1000}, fake), "inspection_failed")
    assert not fake.calls


def invoke(request, *, timeout=3, env=None):
    raw = request if isinstance(request, bytes) else json.dumps(request).encode()
    completed = subprocess.run([sys.executable, str(HELPER)], input=raw, capture_output=True,
                               timeout=timeout, check=True, env=env)
    assert completed.stderr == b""
    assert completed.stdout.count(b"\n") == 1
    return json.loads(completed.stdout)


def test_actual_helper_protocol_rejects_duplicate_keys():
    assert_failure(invoke(b'{"mode":"root","mode":"root","pid":1,"uid":1}'), "invalid")


def test_actual_helper_with_fake_psutil_hung_inspection_exits_itself(posix_host, tmp_path):
    # Shadow only this child interpreter's optional dependency, not the helper.
    (tmp_path / "psutil.py").write_text(
        "import time\n"
        "class NoSuchProcess(Exception): pass\n"
        "class AccessDenied(Exception): pass\n"
        "def Process(pid):\n"
        "    time.sleep(30)\n"
    )
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    started = time.monotonic()
    result = invoke(wait_request(milliseconds=150), timeout=4, env=env)
    assert_failure(result, "timeout", count=1)
    assert time.monotonic() - started < 3
    assert result["initialLive"] is result["remainingLive"] is result["rootExited"] is None


def test_real_subprocess_descendants_capture_and_wait(posix_host):
    pytest.importorskip("psutil")
    # Three exact owned processes. The root closes the child's pipe, which closes
    # the grandchild's pipe, and both parents reap their descendants normally.
    grandchild = "import sys; sys.stdin.buffer.read(1)"
    child = (
        "import subprocess,sys; "
        f"p=subprocess.Popen([sys.executable,'-c',{grandchild!r}],stdin=subprocess.PIPE); "
        "print(p.pid,flush=True); sys.stdin.buffer.read(1); p.stdin.close(); p.wait(timeout=4)"
    )
    root_code = (
        "import subprocess,sys; "
        f"p=subprocess.Popen([sys.executable,'-c',{child!r}],stdin=subprocess.PIPE,stdout=subprocess.PIPE); "
        "print(str(p.pid)+' '+p.stdout.readline().decode().strip(),flush=True); "
        "sys.stdin.buffer.read(1); p.stdin.close(); p.wait(timeout=5)"
    )
    root = subprocess.Popen([sys.executable, "-c", root_code], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        # Pipe readiness is itself bounded; no unbounded readline on a failed child.
        import select
        ready, _, _ = select.select([root.stdout], [], [], 3)
        assert ready, "Owned subprocess readiness timed out"
        child_pid, grandchild_pid = map(int, root.stdout.readline().split())
        root_result = invoke({"mode": "root", "pid": root.pid, "uid": os.getuid()})
        assert root_result["ok"] is True
        captured = invoke({"mode": "capture", "root": root_result["root"]})
        assert captured["ok"] is True
        assert {item["pid"] for item in captured["identities"]} == {root.pid, child_pid, grandchild_pid}
        # All three owned processes remain blocked on their input pipes. This
        # independent observation must refuse cleanup while any survivor lives.
        retained = invoke(wait_request(captured["identities"], milliseconds=2_000), timeout=4)
        assert_failure(retained, "timeout", count=3)
        assert retained["initialLive"] == 3
        assert retained["remainingLive"] in (3, None)
        assert retained["rootExited"] in (False, None)
        request = wait_request(captured["identities"], milliseconds=3_000)
        waiting = subprocess.Popen([sys.executable, str(HELPER)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            waiting.stdin.write(json.dumps(request).encode())
            waiting.stdin.close()
            waiting.stdin = None
            root.stdin.close()
            root.stdin = None
            stdout, stderr = waiting.communicate(timeout=4)
            assert waiting.returncode == 0 and stderr == b""
            result = json.loads(stdout)
            assert result == {"ok": True, "capturedCount": 3, "initialLive": result["initialLive"],
                              "remainingLive": 0, "rootExited": True, "reason": "none"}
            assert type(result["initialLive"]) is int and 0 <= result["initialLive"] <= 3
        finally:
            if waiting.stdin is not None:
                waiting.stdin.close()
                waiting.stdin = None
            waiting.wait(timeout=7)
    finally:
        if root.stdin is not None:
            root.stdin.close()
            root.stdin = None
        root.wait(timeout=7)
        stdout, stderr = root.communicate(timeout=1)
        assert root.returncode == 0 and stderr == b""


@pytest.mark.parametrize("scenario,expected", [
    ("root_birth", ("verify", "root", "birth")),
    ("root_uid", ("verify", "root", "uid")),
    ("root_gone", ("verify", "root", "presence")),
    ("child_link", ("parent_link", "descendant", "ppid")),
    ("child_uid", ("listed_child", "descendant", "uid")),
    ("snapshot_birth", ("snapshot", "descendant", "birth")),
    ("listed_birth", ("listed_child", "descendant", "birth")),
    ("duplicate", ("duplicate", "descendant", "pid")),
    ("post_walk", ("post_walk", "descendant", "birth")),
    ("during_enumeration", ("unhandled_disappearance", "unknown", "presence")),
])
def test_capture_refusal_reports_only_check_scope_and_component(posix_host, scenario, expected):
    fake = FakePsutil().add(10, children=[20]).add(20, ppid=10)
    if scenario == "root_birth":
        fake.records[10]["birth"] = 991.0
    elif scenario == "root_uid":
        fake.records[10]["uid"] = 1001
    elif scenario == "root_gone":
        del fake.records[10]
    elif scenario == "child_link":
        fake.records[20]["ppid"] = 99
    elif scenario == "child_uid":
        fake.records[20]["uid"] = 1001
    elif scenario == "duplicate":
        fake.records[10]["children"] = [20, 20]
    walked = False

    def mutate(pid, method):
        nonlocal walked
        if scenario == "snapshot_birth" and (pid, method) == (20, "uid"):
            fake.records[20]["birth"] = 991.0
        if scenario == "listed_birth" and (pid, method) == (20, "listed_birth"):
            fake.records[20]["birth"] = 991.0
        if scenario == "post_walk":
            if (pid, method) == (20, "children"):
                walked = True
            if walked and (pid, method) == (10, "birth"):
                fake.records[20]["birth"] = 991.0
        if scenario == "during_enumeration" and (pid, method) == (10, "children"):
            del fake.records[10]

    fake.hook = mutate
    result = run({"mode": "capture", "root": identity()}, fake)
    assert_failure(result, "identity_changed")
    assert result["diagnostic"] == dict(zip(("check", "scope", "component"), expected))
    assert set(result) == {"ok", "reason", "diagnostic"}
    assert not fake.forbidden_calls
    assert "991" not in json.dumps(result) and "1001" not in json.dumps(result)

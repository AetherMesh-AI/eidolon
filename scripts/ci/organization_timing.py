"""Per-phase evidence for the recurring Windows attention shard timeout."""
import json
import os
from pathlib import Path
import time
import sys
import threading
import re

import pytest


_output_directory = None
_diagnostic_restore = None
_trace_stop = None
_trace_thread = None
_trace_output = None
_STACK_INTERVAL_SECONDS = 30


def _write_python_stacks(output):
    """Observe Python frames only; never retain locals, source text or host paths."""
    frames = sys._current_frames()
    frame = None
    lines = ["Python frame snapshot (not a native stack dump)\n"]
    try:
        for thread_id, frame in frames.items():
            lines.append(f"Thread {thread_id}:\n")
            while frame is not None:
                code = frame.f_code
                basename = re.split(r'[/\\]', code.co_filename)[-1]
                lines.append(f'  File "{basename}", line {frame.f_lineno} in {code.co_name}\n')
                frame = frame.f_back
    finally:
        # Release frames before potentially slow or failing file I/O.
        frame = None
        frames.clear()
    output.write(''.join(lines))
    output.flush()


def _capture_stacks(stop, output):
    try:
        while not stop.wait(_STACK_INTERVAL_SECONDS):
            try:
                _write_python_stacks(output)
            except Exception:
                pass  # Optional evidence must not change the test outcome.
    finally:
        # The writer owns closure, including when shutdown's bounded join expires.
        try:
            output.close()
        except Exception:
            pass


def pytest_addoption(parser):
    parser.addoption("--organization-timing-dir")


def pytest_configure(config):
    global _output_directory, _diagnostic_restore, _trace_stop, _trace_thread, _trace_output
    value = config.getoption("--organization-timing-dir")
    if value:
        _output_directory = Path(value).resolve()
        _output_directory.mkdir(parents=True, exist_ok=True)
        from scripts.ci.organization_sqlite_diagnostics import install
        _diagnostic_restore = install(lambda value: _record(value, stdout=False))
        # The canonical runner discards stderr after killing a timed-out file.
        # A separate watchdog retains stacks without replacing pytest's timer.
        _trace_output = (_output_directory / f'organization-stacks-{os.getpid()}.txt').open('w')
        _trace_stop = threading.Event()
        _trace_thread = threading.Thread(
            target=_capture_stacks, args=(_trace_stop, _trace_output),
            name='organization-diagnostic-stacks', daemon=True)
        _trace_thread.start()


def pytest_unconfigure(config):
    global _diagnostic_restore, _trace_stop, _trace_thread, _trace_output, _output_directory
    if _diagnostic_restore is not None:
        _diagnostic_restore()
        _diagnostic_restore = None
    if _trace_stop is not None:
        _trace_stop.set()
        _trace_thread.join(timeout=1)
        _trace_stop = _trace_thread = _trace_output = None
    _output_directory = None


def _record(value, *, stdout=True):
    value["time"] = time.monotonic()
    if stdout:
        print("ATTENTION_TIMING " + json.dumps(value), flush=True)
    # Successful subprocess stdout is discarded by the canonical runner.
    # Preserve completed phases outside its disposable per-file temporary root.
    if _output_directory is not None:
        path = _output_directory / f"organization-timing-{os.getpid()}.jsonl"
        with path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(value) + "\n")


def pytest_runtest_logstart(nodeid, location):
    _record({"event": "start", "nodeid": nodeid})


def pytest_runtest_logreport(report):
    _record({"event": report.when, "duration": report.duration,
             "outcome": report.outcome, "nodeid": report.nodeid})


def record_policy_exception(exc, phase):
    """Retain frame locations before worker cleanup; never serialize error values."""
    if _output_directory is None:
        return
    chain, seen = [], set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        frames, tb = [], exc.__traceback__
        while tb is not None:
            code = tb.tb_frame.f_code
            frames.append({'file': re.split(r'[/\\]', code.co_filename)[-1],
                           'line': tb.tb_lineno, 'function': code.co_name})
            tb = tb.tb_next
        reason = getattr(exc, 'reason', None)
        chain.append({'type': type(exc).__name__, 'frames': frames,
                      'reason': reason if type(reason) is str and reason in {
                          'locked', 'deadline', 'cancelled', 'reentrant', 'queue_full'
                      } else None})
        # OwnerPolicyError suppresses display of the underlying gate error.
        exc = exc.__cause__ if exc.__cause__ is not None else exc.__context__
    try:
        _record({'event': 'concurrent-policy-exception', 'phase': phase,
                 'thread': threading.get_ident(), 'chain': chain})
    except OSError:
        pass  # A failed evidence write must not replace the original exception.


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    if (item.name == 'test_same_key_concurrent_apply_returns_one_receipt_and_one_generation'
            and call.excinfo is not None):
        record_policy_exception(call.excinfo.value, call.when)
    return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(fixturedef, request):
    started = time.monotonic()
    try:
        return (yield)
    finally:
        if fixturedef.argname in {"store", "tmp_path", "_hermetic_environment"}:
            _record({"event": "fixture", "name": fixturedef.argname,
                     "duration": time.monotonic() - started, "nodeid": request.node.nodeid})


def print_evidence(directory):
    directory = Path(directory)
    files = sorted(directory.glob('organization-timing-*.jsonl'))
    if not files:
        raise ValueError('Missing native timing evidence')
    sanitized = directory / 'sanitized'
    sanitized.mkdir(exist_ok=True)
    for path in [*files, *sorted(directory.glob('organization-stacks-*.txt'))]:
        content = path.read_text(encoding='utf-8')
        if path.suffix == '.txt':
            # Source basenames and line numbers suffice; omit machine paths.
            content = re.sub(r'File "([^"\n]+)"',
                             lambda match: 'File "' + re.split(r'[/\\]', match.group(1))[-1] + '"', content)
        (sanitized / path.name).write_text(content, encoding='utf-8')
        print(path.name + '\n' + content, flush=True)


if __name__ == '__main__':
    import sys
    print_evidence(sys.argv[1])

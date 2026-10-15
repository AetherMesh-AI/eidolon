"""Per-phase evidence for the recurring Windows attention shard timeout."""
import json
import os
from pathlib import Path
import time
import faulthandler
import threading
import re

import pytest


_output_directory = None
_diagnostic_restore = None
_trace_stop = None
_trace_thread = None
_trace_output = None


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
        stop, output = _trace_stop, _trace_output
        def capture():
            while not stop.wait(30):
                faulthandler.dump_traceback(file=output, all_threads=True)
                output.flush()
        _trace_thread = threading.Thread(target=capture, name='organization-diagnostic-stacks', daemon=True)
        _trace_thread.start()


def pytest_unconfigure(config):
    global _diagnostic_restore, _trace_stop, _trace_thread, _trace_output, _output_directory
    if _diagnostic_restore is not None:
        _diagnostic_restore()
        _diagnostic_restore = None
    if _trace_stop is not None:
        _trace_stop.set()
        _trace_thread.join(timeout=1)
        if not _trace_thread.is_alive():
            _trace_output.close()
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

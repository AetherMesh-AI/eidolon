"""Per-phase evidence for the recurring Windows attention shard timeout."""
import json
import os
from pathlib import Path
import time

import pytest


_output_directory = None
_MAX_FAILURE_BYTES = 16 * 1024


def pytest_addoption(parser):
    parser.addoption("--organization-timing-dir")


def pytest_configure(config):
    global _output_directory
    value = config.getoption("--organization-timing-dir")
    if value:
        _output_directory = Path(value).resolve()
        _output_directory.mkdir(parents=True, exist_ok=True)


def _record(value):
    value["time"] = time.monotonic()
    serialized = json.dumps(value)
    # Successful subprocess stdout is discarded by the canonical runner.
    # Preserve completed phases outside its disposable per-file temporary root.
    if _output_directory is not None:
        path = _output_directory / f"organization-timing-{os.getpid()}.jsonl"
        with path.open("a", encoding="utf-8") as output:
            output.write(serialized + "\n")
    print("ATTENTION_TIMING " + serialized, flush=True)


def _failure_details(report):
    encoded = report.longreprtext.encode("utf-8", errors="replace")
    truncated = len(encoded) > _MAX_FAILURE_BYTES
    if truncated:
        marker = b"\n... failure traceback truncated ...\n"
        head = (_MAX_FAILURE_BYTES - len(marker)) // 2
        tail = _MAX_FAILURE_BYTES - len(marker) - head
        # Keep both the traceback origin and the final exception/assertion.
        text = (encoded[:head].decode("utf-8", errors="ignore")
                + marker.decode("ascii")
                + encoded[-tail:].decode("utf-8", errors="ignore"))
    else:
        text = encoded.decode("utf-8")
    return {"failureText": text, "failureTruncated": truncated,
            "failureOriginalBytes": len(encoded)}


def pytest_runtest_logstart(nodeid, location):
    _record({"event": "start", "nodeid": nodeid})


def pytest_runtest_logreport(report):
    record = {"event": report.when, "duration": report.duration,
              "outcome": report.outcome, "nodeid": report.nodeid}
    if report.failed:
        # A later per-file timeout may prevent pytest's terminal summary.
        record.update(_failure_details(report))
    _record(record)


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(fixturedef, request):
    started = time.monotonic()
    try:
        return (yield)
    finally:
        if fixturedef.argname in {"store", "tmp_path", "_hermetic_environment"}:
            _record({"event": "fixture", "name": fixturedef.argname,
                     "duration": time.monotonic() - started, "nodeid": request.node.nodeid})

"""Per-phase evidence for the recurring Windows attention shard timeout."""
import json
import os
from pathlib import Path
import time

import pytest


def _record(value):
    value["time"] = time.monotonic()
    print("ATTENTION_TIMING " + json.dumps(value), flush=True)
    # Successful subprocess stdout is discarded by the canonical runner.
    # Preserve completed phases outside its disposable per-file temporary root.
    if os.environ.get("RUNNER_TEMP"):
        path = Path(os.environ["RUNNER_TEMP"]) / f"organization-timing-{os.getpid()}.jsonl"
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

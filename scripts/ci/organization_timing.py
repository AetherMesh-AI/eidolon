"""Per-phase evidence for the recurring Windows attention shard timeout."""
import time


def pytest_runtest_logstart(nodeid, location):
    print(f"ATTENTION_TIMING start={time.monotonic():.3f} {nodeid}", flush=True)


def pytest_runtest_logreport(report):
    print(
        f"ATTENTION_TIMING phase={report.when} duration={report.duration:.3f}s "
        f"outcome={report.outcome} {report.nodeid}",
        flush=True,
    )

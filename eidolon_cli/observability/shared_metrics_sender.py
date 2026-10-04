"""Local consent history and fail-closed compatibility for retired telemetry sending.

Eidolon never transmits shared metrics. Legacy callers retain a harmless result;
all HTTP, retry, claiming and package-upload code has been removed.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .shared_metrics import _isoformat, _utc_now

logger = logging.getLogger(__name__)
MAX_OBS_ADVANCE_SECONDS = 30 * 24 * 3600


def _parse_stamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def reconcile_send_consent(
    connection: sqlite3.Connection,
    send_enabled: bool,
    *,
    now: datetime | None = None,
) -> None:
    """Reconcile the consent-window table with the observed config state.

    THE ONLY writer of consent state; must run inside a write transaction. Idempotent in
    (config, now, store): call it from anywhere, any number of times, in any order.
    """
    stamp = _isoformat(now or _utc_now())
    raw_stamp = stamp  # pre-cap observation time, used to clamp closes
    previous_obs = connection.execute(
        "SELECT stamp FROM consent_marks WHERE name = 'obs'"
    ).fetchone()
    if previous_obs is not None:
        ceiling = _isoformat(
            _parse_stamp(str(previous_obs[0])) + timedelta(seconds=MAX_OBS_ADVANCE_SECONDS)
        )
        stamp = min(stamp, ceiling)
    connection.execute(
        """
        INSERT INTO consent_marks(name, stamp) VALUES ('obs', ?)
        ON CONFLICT(name) DO UPDATE SET stamp = MAX(stamp, excluded.stamp)
        """,
        (stamp,),
    )
    marks = dict(connection.execute("SELECT name, stamp FROM consent_marks").fetchall())
    obs = marks["obs"]  # >= stamp; immune to clock rollback
    data = marks.get("data")

    open_row = connection.execute(
        "SELECT rowid FROM send_consent_windows WHERE closed_at IS NULL"
    ).fetchone()

    if send_enabled:
        if open_row is None:
            opened = max(x for x in (obs, data) if x is not None)
            connection.execute(
                "INSERT INTO send_consent_windows(opened_at, last_confirmed_at)"
                " VALUES (?, ?)",
                (opened, opened),
            )
        else:
            connection.execute(
                "UPDATE send_consent_windows"
                " SET last_confirmed_at = MAX(last_confirmed_at, ?)"
                " WHERE rowid = ?",
                (obs, open_row[0]),
            )
    elif open_row is not None:
        # Close at the last CONFIRMED moment, never after the closing observation's RAW
        # stamp. Both clamps are load-bearing: last_confirmed_at means an unobserved gap
        # (machine off, hand-edited config) is never asserted as consented; the raw
        # (pre-cap) stamp pulls a glitched-forward last_confirmed_at back to the true
        # revoke moment. A rolled-back clock only closes EARLIER — fail-closed.
        connection.execute(
            "UPDATE send_consent_windows"
            " SET closed_at = MIN(last_confirmed_at, ?)"
            " WHERE rowid = ?",
            (raw_stamp, open_row[0]),
        )


@dataclass
class SendOutcome:
    """No packages can leave the machine through the retired sender."""

    sent: int = 0
    rejected: int = 0
    deferred: int = 0
    disabled: bool = True


class SharedMetricsSender:
    """Fail-closed adapter for old callers; never reads or modifies their outbox."""

    def __init__(self, store, endpoint: str, *, post=None, sleep=None, now=None,
                 max_attempts: int = 3, consent_check=None) -> None:
        logger.warning("Shared-metrics transmission is retired in Eidolon; metrics stay local.")

    def send_pending(self) -> SendOutcome:
        return SendOutcome()

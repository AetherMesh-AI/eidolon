"""Local-only shared metrics, including when an old config requests sending."""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)
# Kept for import compatibility; no telemetry destination exists in Eidolon.
DEFAULT_ENDPOINT = ""
_warned_legacy_send = False


@dataclass(frozen=True)
class SendConfig:
    enabled: bool
    send: bool = False
    endpoint: str = DEFAULT_ENDPOINT


def resolve_send_config(config: dict | None) -> SendConfig:
    """Honor local collection only; legacy send/endpoint settings cannot enable egress."""
    global _warned_legacy_send
    raw = config if isinstance(config, dict) else {}
    telemetry = raw.get("telemetry")
    telemetry = telemetry if isinstance(telemetry, dict) else {}
    shared = telemetry.get("shared_metrics")
    shared = shared if isinstance(shared, dict) else {}
    if shared.get("send") is True and not _warned_legacy_send:
        _warned_legacy_send = True
        logger.warning("Shared-metrics transmission is retired in Eidolon; metrics stay local.")
    return SendConfig(enabled=shared.get("enabled") is True)


def reset_warning_latch_for_tests() -> None:
    global _warned_legacy_send
    _warned_legacy_send = False

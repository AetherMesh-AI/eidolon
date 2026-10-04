"""Resolve HERMES_HOME for standalone skill scripts.

Skill scripts may run outside the Eidolon process (system Python, nix env,
CI) where ``eidolon_constants`` is not importable.  This module provides the
same ``get_eidolon_home()`` contract without requiring it on ``sys.path``.

When ``eidolon_constants`` IS available it is used directly so profile
resolution and any future enhancements are picked up automatically.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from eidolon_constants import get_eidolon_home as get_eidolon_home
except (ModuleNotFoundError, ImportError):

    def get_eidolon_home() -> Path:
        """Return the Eidolon home directory (default: ``~/.hermes``)."""
        val = os.environ.get("HERMES_HOME", "").strip()
        return Path(val) if val else Path.home() / ".eidolon"

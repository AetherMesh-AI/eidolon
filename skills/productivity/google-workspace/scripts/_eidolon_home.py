"""Resolve HERMES_HOME for standalone skill scripts.

Skill scripts may run outside the Eidolon process (e.g. system Python,
nix env, CI) where ``eidolon_constants`` is not importable.  This module
provides the same ``get_eidolon_home()`` and ``display_eidolon_home()``
contracts as ``eidolon_constants`` without requiring it on ``sys.path``.

When ``eidolon_constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``eidolon_constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``HERMES_HOME = Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from eidolon_constants import display_eidolon_home as display_eidolon_home
    from eidolon_constants import get_eidolon_home as get_eidolon_home
except (ModuleNotFoundError, ImportError):

    def get_eidolon_home() -> Path:
        """Return the Eidolon home directory (default: ~/.hermes).

        Mirrors ``eidolon_constants.get_eidolon_home()``."""
        val = os.environ.get("HERMES_HOME", "").strip()
        return Path(val) if val else Path.home() / ".eidolon"

    def display_eidolon_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``eidolon_constants.display_eidolon_home()``."""
        home = get_eidolon_home()
        try:
            return "~/" + home.relative_to(Path.home()).as_posix()
        except ValueError:
            return str(home)

"""Retired: Eidolon keeps shared metrics local and has no staging upload service."""

import sys


def main() -> int:
    print("Shared-metrics uploads are retired in Eidolon. No data was collected or sent.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

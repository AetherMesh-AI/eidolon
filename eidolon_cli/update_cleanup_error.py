"""Typed failure boundary for incomplete dashboard update cleanup."""


class DashboardCleanupIncomplete(RuntimeError):
    """Cleanup returned unresolved ownership or recovery obligations."""

    def __init__(self, result: dict):
        self.result = result
        super().__init__(f"Dashboard update cleanup incomplete: {result}")

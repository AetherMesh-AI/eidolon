"""Explicit context for bounded callers that prohibit model metadata discovery.

The caller owns exact input admission and token reservations. This object avoids
turning a declared window into a model-catalog probe or a local model preload.
Ordinary interactive agents retain their existing discovery and minimum window.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class BoundedContextWindow:
    tokens: int

    def __post_init__(self):
        if type(self.tokens) is not int or not 4096 <= self.tokens <= 2_000_000:
            raise ValueError('An explicit bounded context window must be between 4096 and 2000000 tokens')


def explicit_context_window(agent):
    value = getattr(agent, '_bounded_context_window', None)
    return value.tokens if isinstance(value, BoundedContextWindow) else None

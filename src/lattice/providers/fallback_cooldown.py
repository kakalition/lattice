"""Fallback model cooldown to avoid primary↔fallback oscillation."""

from __future__ import annotations

import time


class FallbackCooldown:
    def __init__(self, *, base_seconds: float = 30.0, max_seconds: float = 600.0) -> None:
        self.base_seconds = base_seconds
        self.max_seconds = max_seconds
        self._until = 0.0
        self._streak = 0

    def mark_fallback(self) -> None:
        self._streak += 1
        delay = min(self.max_seconds, self.base_seconds * (2 ** (self._streak - 1)))
        self._until = time.monotonic() + delay

    def clear(self) -> None:
        self._streak = 0
        self._until = 0.0

    def primary_allowed(self) -> bool:
        return time.monotonic() >= self._until

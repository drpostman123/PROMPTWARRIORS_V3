"""Per-host async token bucket. Free API tiers are real constraints at a
60s scan cadence — every HTTP client shares one of these per host so a
burst of candidates can't 429-starve the scanner."""

from __future__ import annotations

import asyncio
import time


class TokenBucket:
    def __init__(self, rate_per_s: float, burst: float | None = None) -> None:
        self._rate = rate_per_s
        self._capacity = burst if burst is not None else max(1.0, rate_per_s)
        self._tokens = self._capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                await asyncio.sleep((1.0 - self._tokens) / self._rate)

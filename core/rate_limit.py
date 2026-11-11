"""Very small in-memory sliding-window rate limiter (per key, e.g. client IP).

Limitations (see REPORT.md): state lives in one process - it is reset on restart and is not shared
between several workers/instances. Behind a proxy the key must come from core.client_ip.client_ip
(BEHIND_PROXY: CF-Connecting-IP, then X-Forwarded-For / TRUSTED_PROXY_HOPS), otherwise every client shares
the proxy address."""
import time
from collections import deque
from typing import Deque, Dict, Optional


class SlidingWindowLimiter:
    def __init__(self, max_calls: int, period_seconds: float):
        self.max_calls = max_calls
        self.period = period_seconds
        self._hits: Dict[str, Deque[float]] = {}
        self._last_sweep = time.monotonic()

    def hit(self, key: str) -> Optional[int]:
        """Register a call. Returns None if allowed, otherwise the number of seconds to wait."""
        now = time.monotonic()
        self._sweep(now)
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self.period:
            hits.popleft()
        if len(hits) >= self.max_calls:
            return max(1, int(self.period - (now - hits[0])) + 1)
        hits.append(now)
        return None

    def reset(self) -> None:
        self._hits.clear()

    def _sweep(self, now: float) -> None:
        # Drop idle keys from time to time so memory does not grow without bound.
        if now - self._last_sweep < self.period:
            return
        self._last_sweep = now
        for key in [k for k, v in self._hits.items() if not v or now - v[-1] >= self.period]:
            del self._hits[key]

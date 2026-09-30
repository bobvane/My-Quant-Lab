"""In-process sliding-window rate limiter (docs/14 §4).

Deliberately tiny and dependency-free: the API runs as a single container, so a
per-process limiter is enough to blunt accidental or malicious bursts (e.g.
repeatedly poking ``POST /notifications/test``). It is *not* a distributed
limiter; behind multiple API replicas each process enforces its own budget, and
the reverse proxy remains the place for a shared limit.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

__all__ = ["SlidingWindowLimiter", "limiter"]


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, *, window_seconds: float = 60.0) -> bool:
        """Record one event for ``key`` and report whether it is within budget."""

        if limit <= 0:
            return True
        moment = time.monotonic()
        cutoff = moment - window_seconds
        with self._lock:
            queue = self._events[key]
            while queue and queue[0] < cutoff:
                queue.popleft()
            if len(queue) >= limit:
                return False
            queue.append(moment)
            return True

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


limiter = SlidingWindowLimiter()

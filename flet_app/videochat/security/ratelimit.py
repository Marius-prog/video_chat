"""In-memory sliding-window rate limiter.

State lives in this process only: with several worker processes each one keeps
its own counters (limits become per-process). Put a shared limiter (e.g. Redis)
behind the same interface if you scale out.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: float):
        super().__init__("rate limit exceeded")
        self.retry_after = max(1, int(retry_after + 0.999))


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    remaining: int
    retry_after: float = 0.0


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic, max_keys: int = 100_000):
        self._clock = clock
        self._max_keys = max_keys
        self._events: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window: float) -> RateDecision:
        """Record one attempt for ``key`` and say whether it is within ``limit`` per ``window`` seconds."""
        now = self._clock()
        with self._lock:
            events = self._events.get(key)
            if events is None:
                if len(self._events) >= self._max_keys:
                    self._sweep(now)
                events = self._events[key] = deque()
            self._expire(events, now, window)
            if len(events) >= limit:
                return RateDecision(False, 0, events[0] + window - now)
            events.append(now)
            return RateDecision(True, limit - len(events))

    def enforce(self, key: str, limit: int, window: float) -> None:
        decision = self.hit(key, limit, window)
        if not decision.allowed:
            raise RateLimitExceeded(decision.retry_after)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    @staticmethod
    def _expire(events: Deque[float], now: float, window: float) -> None:
        while events and events[0] <= now - window:
            events.popleft()

    def _sweep(self, now: float) -> None:
        # Called under the lock when the table is full: drop idle keys, and if
        # that is not enough, the oldest half (an attacker rotating keys must
        # not be able to grow memory without bound).
        longest = 24 * 3600
        for key in [k for k, v in self._events.items() if not v or v[-1] <= now - longest]:
            del self._events[key]
        if len(self._events) >= self._max_keys:
            oldest = sorted(self._events, key=lambda k: self._events[k][-1])
            for key in oldest[: len(oldest) // 2]:
                del self._events[key]

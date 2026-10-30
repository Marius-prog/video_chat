"""Rate limiting as a cross-cutting concern.

The limiter in ``security/ratelimit.py`` knows nothing about services or HTTP. This is
the one place its verdict becomes a :class:`RateLimited` service error, which the UI
shows as a message and the API answers with a 429 and ``Retry-After``. Services call
:func:`enforce` directly; the API wraps endpoints with a dependency built on it
(``web/api.py``), so the endpoints themselves never mention throttling.
"""
from __future__ import annotations

from ..security.ratelimit import RateLimiter, RateLimitExceeded
from .errors import RateLimited


def enforce(limiter: RateLimiter, key: str, limit: int, window: float) -> None:
    """Count one attempt against ``key``; raise :class:`RateLimited` once ``limit`` per ``window`` seconds is used up."""
    try:
        limiter.enforce(key, limit, window)
    except RateLimitExceeded as exc:
        raise RateLimited(exc.retry_after) from None

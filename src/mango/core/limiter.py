"""Async sliding-window rate limiter for outbound API calls.

Standard-library only (asyncio). Callers construct one `RateLimiter` per
API/provider and `await limiter.acquire()` immediately before each request.
`acquire()` returns immediately while under budget, and otherwise sleeps just
long enough to stay within `calls_per_minute` calls over any rolling 60
second window.

A sliding window (as opposed to a fixed bucket that resets on the minute
boundary) is used deliberately: a fixed bucket lets a caller burst up to
`calls_per_minute` calls right at the end of one bucket and another
`calls_per_minute` right at the start of the next, doubling the effective
rate across the boundary. Tracking actual call timestamps avoids that.
"""

from __future__ import annotations

import asyncio
import time

from mango.core.logging import get_logger

log = get_logger("limiter")

# The rolling window, in seconds, over which `calls_per_minute` is enforced.
_WINDOW_SECONDS = 60.0


class RateLimiter:
    """Sliding-window async rate limiter.

    Safe for concurrent use from multiple coroutines: an `asyncio.Lock`
    guards the shared timestamp history so acquire() calls are serialised
    around the decision of whether/how long to sleep.
    """

    def __init__(self, calls_per_minute: int) -> None:
        if calls_per_minute <= 0:
            raise ValueError(f"calls_per_minute must be positive, got {calls_per_minute}")

        self._calls_per_minute = calls_per_minute
        # Monotonic-clock timestamps of recent calls, oldest first. Bounded
        # to at most `calls_per_minute` entries once trimmed, since anything
        # older than the window is discarded on every acquire().
        self._call_times: list[float] = []
        self._lock = asyncio.Lock()

    def _trim_expired(self, now: float) -> None:
        """Drop timestamps older than the rolling window, in place.

        Keeps memory bounded regardless of how long the limiter lives.
        """
        cutoff = now - _WINDOW_SECONDS
        while self._call_times and self._call_times[0] <= cutoff:
            self._call_times.pop(0)

    async def acquire(self) -> None:
        """Block (if necessary) until it is safe to make another call.

        Uses a monotonic clock so a wall-clock adjustment (NTP sync, DST,
        manual change) can't wedge the limiter into sleeping for an
        arbitrarily wrong duration.
        """
        async with self._lock:
            now = time.monotonic()
            self._trim_expired(now)

            if len(self._call_times) < self._calls_per_minute:
                self._call_times.append(now)
                return

            # At budget: the oldest call in the window is the one that must
            # age out before another call is allowed. Sleep until exactly
            # that moment.
            oldest_call = self._call_times[0]
            sleep_seconds = (oldest_call + _WINDOW_SECONDS) - now
            if sleep_seconds > 0:
                log.debug(
                    "Rate limit reached (%s calls/min); sleeping %.3fs",
                    self._calls_per_minute,
                    sleep_seconds,
                )
                await asyncio.sleep(sleep_seconds)

            # Re-anchor to "now" after sleeping rather than trusting the
            # pre-sleep clock reading, then record this call.
            post_sleep_now = time.monotonic()
            self._trim_expired(post_sleep_now)
            self._call_times.append(post_sleep_now)

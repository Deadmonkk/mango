"""Tests for the mango infrastructure modules: logging and rate limiting."""

from __future__ import annotations

import asyncio
import logging
import sys

import pytest

from terminalq.mango import limiter as limiter_module
from terminalq.mango.limiter import RateLimiter
from terminalq.mango.logging import _resolve_log_level, get_logger, log


class FakeClock:
    """A controllable monotonic clock used to avoid real sleeping in tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Patch the limiter module's clock and sleep so tests run instantly.

    `asyncio.sleep` is replaced with a coroutine that advances the fake
    clock by the requested duration instead of actually waiting, so tests
    exercising "the limiter sleeps N seconds" complete immediately while
    still observing the sleep call and its duration.
    """
    clock = FakeClock()
    monkeypatch.setattr(limiter_module.time, "monotonic", clock.monotonic)

    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        clock.advance(seconds)

    monkeypatch.setattr(limiter_module.asyncio, "sleep", fake_sleep)
    clock.sleep_calls = sleep_calls  # type: ignore[attr-defined]
    return clock


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


class TestRateLimiterConstruction:
    def test_raises_value_error_for_zero_calls_per_minute(self) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ValueError):
            RateLimiter(calls_per_minute=0)

    def test_raises_value_error_for_negative_calls_per_minute(self) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ValueError):
            RateLimiter(calls_per_minute=-5)

    def test_accepts_positive_calls_per_minute(self) -> None:
        # Arrange / Act
        rate_limiter = RateLimiter(calls_per_minute=10)

        # Assert
        assert rate_limiter is not None


@pytest.mark.asyncio
class TestRateLimiterAcquire:
    async def test_calls_within_budget_do_not_sleep(self, fake_clock: FakeClock) -> None:
        # Arrange
        rate_limiter = RateLimiter(calls_per_minute=5)

        # Act
        for _ in range(5):
            await rate_limiter.acquire()

        # Assert
        assert fake_clock.sleep_calls == []  # type: ignore[attr-defined]

    async def test_exceeding_budget_sleeps(self, fake_clock: FakeClock) -> None:
        # Arrange
        rate_limiter = RateLimiter(calls_per_minute=2)

        # Act: two calls consume the budget at t=0, third must wait.
        await rate_limiter.acquire()
        await rate_limiter.acquire()
        await rate_limiter.acquire()

        # Assert
        sleep_calls = fake_clock.sleep_calls  # type: ignore[attr-defined]
        assert len(sleep_calls) == 1
        assert sleep_calls[0] == pytest.approx(60.0, abs=0.01)

    async def test_sliding_window_frees_capacity_as_time_advances(self, fake_clock: FakeClock) -> None:
        # Arrange
        rate_limiter = RateLimiter(calls_per_minute=1)
        await rate_limiter.acquire()  # consumes the only slot at t=0

        # Act: advance past the window without calling acquire again yet.
        fake_clock.advance(60.1)
        await rate_limiter.acquire()

        # Assert: no sleep was needed because the window had already freed up.
        assert fake_clock.sleep_calls == []  # type: ignore[attr-defined]

    async def test_old_timestamps_are_discarded(self, fake_clock: FakeClock) -> None:
        # Arrange
        rate_limiter = RateLimiter(calls_per_minute=3)
        await rate_limiter.acquire()
        await rate_limiter.acquire()

        # Act: advance beyond the window so both prior calls expire.
        fake_clock.advance(61.0)
        await rate_limiter.acquire()

        # Assert: only the most recent call remains tracked.
        assert len(rate_limiter._call_times) == 1

    async def test_concurrent_acquires_are_serialized_safely(self, fake_clock: FakeClock) -> None:
        # Arrange
        rate_limiter = RateLimiter(calls_per_minute=3)

        # Act: fire more concurrent acquires than the budget allows.
        await asyncio.gather(*(rate_limiter.acquire() for _ in range(6)))

        # Assert: no timestamp was lost/duplicated by a race on shared state,
        # and the limiter had to sleep to accommodate the overflow.
        assert len(rate_limiter._call_times) <= rate_limiter._calls_per_minute
        assert len(fake_clock.sleep_calls) >= 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------


class TestLogSetup:
    def test_log_exists_with_standard_logger_methods(self) -> None:
        # Arrange / Act / Assert
        assert isinstance(log, logging.Logger)
        assert callable(log.debug)
        assert callable(log.info)
        assert callable(log.warning)
        assert callable(log.error)

    def test_log_name_is_terminalq(self) -> None:
        # Arrange / Act / Assert
        assert log.name == "terminalq"

    def test_repeated_configuration_does_not_duplicate_handlers(self) -> None:
        # Arrange
        from terminalq.mango import logging as mango_logging

        handler_count_before = len(mango_logging.log.handlers)
        stream_handlers_before = [
            h for h in mango_logging.log.handlers if isinstance(h, logging.StreamHandler)
        ]

        # Act: calling the configuration entry point again must be a no-op,
        # even called multiple times (simulates repeated module import).
        mango_logging._configure_root_logger()
        mango_logging._configure_root_logger()
        mango_logging._configure_root_logger()

        # Assert: the handler set is unchanged. (Note: pytest's own log
        # capture plugin may attach additional handlers to this logger for
        # caplog/live-log support -- those are outside our control, so this
        # test only asserts that *our* configuration step is idempotent,
        # not that the logger has exactly one handler in total.)
        assert len(mango_logging.log.handlers) == handler_count_before
        stream_handlers_after = [
            h for h in mango_logging.log.handlers if isinstance(h, logging.StreamHandler)
        ]
        assert len(stream_handlers_after) == len(stream_handlers_before)

    def test_handler_writes_to_stderr_not_stdout(self) -> None:
        # Arrange / Act
        handler = log.handlers[0]

        # Assert
        assert handler.stream is sys.stderr
        assert handler.stream is not sys.stdout

    def test_logger_does_not_propagate_to_root(self) -> None:
        # Arrange / Act / Assert
        assert log.propagate is False

    def test_get_logger_returns_correctly_named_child(self) -> None:
        # Arrange / Act
        child = get_logger("fred")

        # Assert
        assert child.name == "terminalq.fred"

    def test_get_logger_child_inherits_root_effective_level(self) -> None:
        # Arrange / Act
        child = get_logger("yfinance")

        # Assert
        assert child.getEffectiveLevel() == log.getEffectiveLevel()


class TestLogLevelResolution:
    def test_defaults_to_info_when_env_var_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange
        monkeypatch.delenv("TERMINALQ_LOG_LEVEL", raising=False)

        # Act
        level = _resolve_log_level()

        # Assert
        assert level == logging.INFO

    def test_reads_debug_level_case_insensitively(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange
        monkeypatch.setenv("TERMINALQ_LOG_LEVEL", "debug")

        # Act
        level = _resolve_log_level()

        # Assert
        assert level == logging.DEBUG

    def test_reads_warning_level_uppercase(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange
        monkeypatch.setenv("TERMINALQ_LOG_LEVEL", "WARNING")

        # Act
        level = _resolve_log_level()

        # Assert
        assert level == logging.WARNING

    def test_falls_back_to_info_on_unrecognised_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange
        monkeypatch.setenv("TERMINALQ_LOG_LEVEL", "NOT_A_REAL_LEVEL")

        # Act
        level = _resolve_log_level()

        # Assert
        assert level == logging.INFO

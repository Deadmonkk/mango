"""Tests for the shared resilient HTTP layer (`mango.core.http`).

The behaviour that matters here is the retry CLASSIFICATION, not the happy
path: retrying a 403 wastes the run and can trip rate limits, while failing to
retry a ReadTimeout is what turned a transient blip into "data unavailable
(source failed)" in the 2026-08-17 report.
"""

import httpx
import pytest

from mango.core import http as mhttp


def _client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_returns_parsed_json_on_first_success():
    async with _client(lambda req: httpx.Response(200, json={"ok": 1})) as c:
        assert await mhttp.fetch_json("https://x.test/a", client=c) == {"ok": 1}


@pytest.mark.asyncio
async def test_retries_read_timeout_then_succeeds(monkeypatch):
    monkeypatch.setattr(mhttp, "_sleep", _no_sleep)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("boom", request=request)
        return httpx.Response(200, json={"recovered": True})

    async with _client(handler) as c:
        assert await mhttp.fetch_json("https://x.test/a", client=c) == {"recovered": True}
    assert calls["n"] == 2, "a ReadTimeout must be retried exactly once before succeeding"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_retries_transient_status_codes(monkeypatch, status):
    monkeypatch.setattr(mhttp, "_sleep", _no_sleep)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(status) if calls["n"] == 1 else httpx.Response(200, json={"ok": 1})

    async with _client(handler) as c:
        assert await mhttp.fetch_json("https://x.test/a", client=c) == {"ok": 1}
    assert calls["n"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_does_not_retry_client_errors(monkeypatch, status):
    """A 403 means 'your plan lacks this' — retrying burns quota and never wins."""
    monkeypatch.setattr(mhttp, "_sleep", _no_sleep)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(status)

    async with _client(handler) as c:
        with pytest.raises(httpx.HTTPStatusError):
            await mhttp.fetch_json("https://x.test/a", client=c)
    assert calls["n"] == 1, f"{status} must not be retried"


@pytest.mark.asyncio
async def test_does_not_retry_malformed_json(monkeypatch):
    monkeypatch.setattr(mhttp, "_sleep", _no_sleep)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, text="not json{")

    async with _client(handler) as c:
        with pytest.raises(ValueError):
            await mhttp.fetch_json("https://x.test/a", client=c)
    assert calls["n"] == 1, "a malformed body is a source bug, not a transient fault"


@pytest.mark.asyncio
async def test_raises_after_exhausting_attempts(monkeypatch):
    monkeypatch.setattr(mhttp, "_sleep", _no_sleep)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ConnectTimeout("down", request=request)

    async with _client(handler) as c:
        with pytest.raises(httpx.ConnectTimeout):
            await mhttp.fetch_json("https://x.test/a", client=c, attempts=3)
    assert calls["n"] == 3, "the caller's existing try/except must still see the real exception"


@pytest.mark.asyncio
async def test_honours_retry_after_header(monkeypatch):
    slept: list[float] = []

    async def record(seconds):
        slept.append(seconds)

    monkeypatch.setattr(mhttp, "_sleep", record)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"ok": 1})

    async with _client(handler) as c:
        await mhttp.fetch_json("https://x.test/a", client=c)
    assert slept == [2.0], "an explicit Retry-After must win over computed backoff"


def test_backoff_grows_and_stays_bounded():
    delays = [mhttp._backoff_seconds(i) for i in range(6)]
    assert delays[0] < delays[1] < delays[2], "backoff must grow with attempt number"
    assert all(d <= mhttp.MAX_BACKOFF_SECONDS for d in delays)
    assert all(d > 0 for d in delays)


def test_jitter_desynchronises_concurrent_callers():
    """70 providers retrying in lockstep would re-collide; jitter prevents that."""
    assert len({mhttp._backoff_seconds(2) for _ in range(20)}) > 1


async def _no_sleep(_seconds: float) -> None:
    return None

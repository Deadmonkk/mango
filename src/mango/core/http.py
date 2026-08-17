"""Shared resilient HTTP layer — one retry/backoff policy for every provider.

WHY THIS EXISTS
---------------
FR fans out to ~70 external sources. Even at a 2% per-source failure rate the
chance that all 70 succeed is 0.98**70 ~= 24%, so three runs in four showed at
least one "data unavailable (source failed)" row. Most of those were not real
outages: a measured sample of the FRED release-calendar endpoint on 2026-08-17
failed 1 call in 6 with a bare ReadTimeout, and succeeded on the very next try.

Before this module, resilience was a property of each provider rather than of
the platform: of 19 provider modules, only three (coingecko, crypto_funding,
finnhub) retried anything. A single dropped packet against any of the other 16
became a permanent gap in the report. One implementation here fixes all of them.

WHAT THIS DOES NOT DO
---------------------
It does not swallow errors. `fetch_json` raises the real exception once the
attempts are exhausted, because callers already convert exceptions into
`{"error": ...}` payloads and `scripts/fr_collect.py:safe()` already guarantees
a failed source cannot abort a run. Adding a second layer of swallowing here
would only hide which source died. Degrading loudly is the existing contract;
this module's job is to stop TRANSIENT blips from becoming degradations at all.

RETRY CLASSIFICATION
--------------------
Retrying the wrong thing is worse than not retrying. A 403 from a premium-walled
endpoint will never succeed (see `providers/finnhub.py`, which learned this the
hard way and documents not retrying its calendar 403), and hammering it burns
quota that `core/usage_tracker.py` is counting against a free-tier ceiling. So
only faults that a later attempt could plausibly survive are retried.
"""

import asyncio
import random

import httpx

from mango.core.logging import log

# One connect/read budget for every provider. 15s is long enough for FRED's
# slower series endpoints and short enough that three attempts cannot stall a
# report past its ~70s collection budget.
DEFAULT_TIMEOUT_SECONDS = 15.0

# Total attempts, not retries: 2 means one retry. Measured single-call failure
# rates are low but non-trivial, and failures are overwhelmingly independent, so
# the first retry captures nearly all of the available recovery. A third attempt
# is the tail-insurance for a provider having a genuinely bad minute.
DEFAULT_ATTEMPTS = 3

BACKOFF_BASE_SECONDS = 0.25
MAX_BACKOFF_SECONDS = 8.0
# Jitter fraction of the computed delay. Without it, a burst of providers that
# fail together (a flapping local link) retries in lockstep and collides again.
JITTER_FRACTION = 0.3

# Transient by status: the server is up but cannot serve this request *now*.
# 429 is included because the pipeline's rate limiter is advisory, not a
# guarantee — a shared upstream budget can still push us over.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Transient by exception: nothing was served at all. httpx.TimeoutException is
# the parent of Connect/Read/Write/PoolTimeout; ConnectError covers DNS and
# refused connections; ReadError/RemoteProtocolError cover a reset mid-response.
RETRYABLE_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)


def _backoff_seconds(attempt: int) -> float:
    """Jittered exponential backoff for a zero-indexed attempt number.

    The cap is applied AFTER jitter, not before: capping first lets a 30%
    jitter push the delay 30% past the documented ceiling, which makes
    MAX_BACKOFF_SECONDS a suggestion rather than a bound.
    """
    base = BACKOFF_BASE_SECONDS * (2**attempt)
    return min(base * (1 + random.uniform(0, JITTER_FRACTION)), MAX_BACKOFF_SECONDS)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """The server's own Retry-After, in seconds, when it sent a usable one.

    An explicit instruction from the server beats our computed guess. Only the
    delta-seconds form is honoured; the HTTP-date form is rare here and parsing
    it wrong would sleep for hours.
    """
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return min(float(raw), MAX_BACKOFF_SECONDS)
    except (TypeError, ValueError):
        return None


async def _sleep(seconds: float) -> None:
    """Indirection so tests can run the retry path without real delays."""
    await asyncio.sleep(seconds)


def _is_retryable_status(exc: BaseException) -> bool:
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in RETRYABLE_STATUS
    )


async def fetch_json(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    attempts: int = DEFAULT_ATTEMPTS,
    client: httpx.AsyncClient | None = None,
    method: str = "GET",
    json_body: dict | list | None = None,
    follow_redirects: bool = True,
):
    """Request `url` and return parsed JSON, retrying only transient failures.

    Raises the final exception when every attempt fails, so an existing
    `except Exception` in the calling provider keeps behaving exactly as before
    — this is a drop-in replacement for a bare `client.get(...)`, not a new
    error-handling contract.

    Pass `client` to reuse a caller's session (and in tests, a MockTransport).
    """
    owned = client is None
    session = client or httpx.AsyncClient(timeout=timeout, follow_redirects=follow_redirects)
    try:
        return await _attempt_loop(
            session, url, params, headers, timeout, attempts,
            decode=None, method=method, json_body=json_body,
        )
    finally:
        if owned:
            await session.aclose()


async def fetch_text(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    attempts: int = DEFAULT_ATTEMPTS,
    client: httpx.AsyncClient | None = None,
    follow_redirects: bool = True,
) -> str:
    """Same retry policy as `fetch_json`, for endpoints that return HTML/CSV.

    Scraped pages (e.g. the ETF-flow tables) need identical transport
    resilience; only the decode step differs, so the policy must not fork.
    """
    owned = client is None
    session = client or httpx.AsyncClient(timeout=timeout, follow_redirects=follow_redirects)
    try:
        return await _attempt_loop(
            session, url, params, headers, timeout, attempts, decode=lambda r: r.text
        )
    finally:
        if owned:
            await session.aclose()


async def _attempt_loop(
    session, url, params, headers, timeout, attempts, decode=None,
    method="GET", json_body=None,
):
    last_exc: BaseException | None = None
    for attempt in range(attempts):
        try:
            response = await session.request(
                method, url, params=params, headers=headers,
                json=json_body, timeout=timeout,
            )
            response.raise_for_status()
            # A body that will not parse is a source bug, not a transient
            # fault — retrying returns the identical bytes. Let it raise.
            return decode(response) if decode else response.json()
        except Exception as exc:  # noqa: BLE001 — re-raised below unless retryable
            if not (isinstance(exc, RETRYABLE_EXCEPTIONS) or _is_retryable_status(exc)):
                raise
            last_exc = exc
            if attempt == attempts - 1:
                break
            delay = _backoff_for(exc, attempt)
            log.debug(
                "http: %s on %s (attempt %d/%d); retrying in %.2fs",
                type(exc).__name__, url, attempt + 1, attempts, delay,
            )
            await _sleep(delay)

    log.warning(
        "http: giving up on %s after %d attempts (%s)",
        url, attempts, type(last_exc).__name__,
    )
    raise last_exc  # type: ignore[misc]


def _backoff_for(exc: BaseException, attempt: int) -> float:
    if isinstance(exc, httpx.HTTPStatusError):
        explicit = _retry_after_seconds(exc.response)
        if explicit is not None:
            return explicit
    return _backoff_seconds(attempt)

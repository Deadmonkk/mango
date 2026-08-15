"""Client helpers for the CoinGecko public API.

Docs: https://docs.coingecko.com/reference/introduction

This is a clean-room implementation written directly from a written
specification, not from any existing CoinGecko client in this codebase
family. CoinGecko's free/keyless tier is generous but rate-limits
aggressively (HTTP 429 on bursts), so the two responsibilities this module
owns are: (1) map friendly ticker symbols to CoinGecko's internal coin ids,
and (2) perform a single GET with retry/backoff, proactive rate limiting,
and caching, always returning a plain dict rather than raising.

Error convention (fixed by the sole consumer, ``crypto_analytics.py``):
on any failure ``_fetch`` returns ``{"_error": "<message>"}`` — note the
leading underscore, which distinguishes it from the ``{"error": ...}``
convention used elsewhere in this codebase (e.g. ``mango.core.fred``).
The consumer specifically tests for the ``"_error"`` key to decide whether
to fall back to Yahoo Finance, so the spelling here is load-bearing, not a
style choice.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

import httpx

from mango.core import cache
from mango.core.limiter import RateLimiter
from mango.core.logging import get_logger

log = get_logger("coingecko")

# --- API configuration -------------------------------------------------

BASE_URL = "https://api.coingecko.com/api/v3"

# CoinGecko's public (keyless) tier is documented as roughly 30 calls/minute.
# Staying comfortably under that (rather than exactly at it) leaves headroom
# for other concurrent callers sharing the same process and reduces how often
# the 429 retry path below is even needed.
COINGECKO_RATE_LIMIT_PER_MINUTE = 25

# One shared limiter for every call this module makes.
_limiter = RateLimiter(COINGECKO_RATE_LIMIT_PER_MINUTE)

# Generous but bounded — a hung connection must not hang the caller forever.
REQUEST_TIMEOUT_SECONDS = 15.0

# Market data (price, volume, market cap) moves continuously, so the cache
# window is short: long enough to avoid re-fetching the same URL+params
# multiple times within one report-generation run, short enough that a
# cached read is never mistaken for a fresh quote.
MARKET_DATA_CACHE_TTL_SECONDS = 60

# --- Retry/backoff configuration ---------------------------------------

# A small bounded number of attempts. CoinGecko's free tier 429s are usually
# transient (a burst from another caller sharing the IP), so a few retries
# with backoff resolve most of them without holding the caller hostage
# indefinitely on a host that is genuinely down.
MAX_RETRY_ATTEMPTS = 4

# Exponential backoff base, in seconds: attempt N sleeps roughly
# BACKOFF_BASE_SECONDS * 2**N before retrying, unless the server's own
# Retry-After header says otherwise.
BACKOFF_BASE_SECONDS = 1.0

# Upper bound on any single backoff sleep, so a misbehaving Retry-After
# value (or a large computed exponential) can't stall a report for minutes.
MAX_BACKOFF_SECONDS = 30.0

HTTP_TOO_MANY_REQUESTS = 429

# --- Symbol -> CoinGecko coin id resolution -----------------------------

# Friendly ticker -> CoinGecko coin id. Only the non-obvious mappings need to
# be listed explicitly; many coin ids do equal their lowercase ticker (e.g.
# "SOL" -> "solana" is the exception that proves the rule -- it does NOT
# match its ticker, which is exactly why it's listed here). Symbols not in
# this map fall through to a lowercase guess in `_resolve_id`.
_SYMBOL_TO_COIN_ID: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "ripple",  # non-obvious: ticker XRP, coin id "ripple"
    "AVAX": "avalanche-2",  # non-obvious: suffixed to disambiguate from another "avalanche"
    "DOGE": "dogecoin",
    "BNB": "binancecoin",
    "ADA": "cardano",
    "LINK": "chainlink",
    "MATIC": "matic-network",
    "DOT": "polkadot",
    "LTC": "litecoin",
    "TRX": "tron",
    "UNI": "uniswap",
    "ATOM": "cosmos",
    # Non-obvious slugs: the lowercase-ticker fallback resolves to
    # "render"/"ondo", which are 404s on CoinGecko, so get_crypto_deep failed
    # for them until they were mapped explicitly.
    "RENDER": "render-token",
    "ONDO": "ondo-finance",
}


def _resolve_id(symbol: str) -> str:
    """Resolve a ticker symbol to its CoinGecko coin id.

    Case-insensitive on input. An unknown symbol falls through to
    ``symbol.lower()`` — many coin ids equal their lowercase ticker (e.g.
    a hypothetical "FOO" ticker is plausibly the "foo" coin id), so that is
    a reasonable best-effort guess rather than an error. Callers that need
    certainty should confirm the id independently; this function never
    raises on an unknown symbol.
    """
    return _SYMBOL_TO_COIN_ID.get(symbol.upper(), symbol.lower())


# --- Fetch with retry/backoff, rate limiting, and caching ---------------


def _cache_key(url: str, params: dict) -> str:
    """Stable cache key derived from the URL and params.

    Params are sorted before hashing so equivalent requests with keys
    supplied in a different order still hit the same cache entry.
    """
    canonical = json.dumps({"url": url, "params": params}, sort_keys=True, default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"coingecko_{digest}"


def _error(message: str) -> dict:
    return {"_error": message}


def _retry_after_seconds(response: httpx.Response, attempt: int) -> float:
    """Seconds to sleep before retrying a 429, honouring Retry-After when present.

    Falls back to exponential backoff (``BACKOFF_BASE_SECONDS * 2**attempt``)
    when the header is absent or unparsable. Either way the result is capped
    at ``MAX_BACKOFF_SECONDS``.
    """
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return min(float(retry_after), MAX_BACKOFF_SECONDS)
        except (TypeError, ValueError):
            pass
    return min(BACKOFF_BASE_SECONDS * (2**attempt), MAX_BACKOFF_SECONDS)


async def _fetch(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    """GET ``url`` with ``params`` and return the decoded JSON dict.

    Never raises: any failure (rate limit exhausted, HTTP error, timeout,
    connection error, malformed JSON) becomes ``{"_error": "<message>"}``.
    Successful responses are cached; failures are not (the cache module
    itself refuses to persist an ``_error`` payload).

    ``client`` is supplied by the caller rather than created here so that
    callers can share one ``httpx.AsyncClient`` across multiple requests
    (e.g. concurrent fetches within a single report run).
    """
    key = _cache_key(url, params)
    cached = cache.get(key)
    if cached is not None:
        return cached

    last_error: str = "unknown error"
    for attempt in range(MAX_RETRY_ATTEMPTS):
        await _limiter.acquire()
        try:
            response = await client.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            log.warning("CoinGecko request failed (%s): %s", url, last_error)
            return _error(last_error)

        if response.status_code == HTTP_TOO_MANY_REQUESTS:
            if attempt == MAX_RETRY_ATTEMPTS - 1:
                last_error = "rate limited (429): retries exhausted"
                log.warning("CoinGecko rate limit retries exhausted for %s", url)
                return _error(last_error)
            sleep_seconds = _retry_after_seconds(response, attempt)
            log.warning(
                "CoinGecko rate limited (429) on %s, attempt %d/%d — backing off %.2fs",
                url,
                attempt + 1,
                MAX_RETRY_ATTEMPTS,
                sleep_seconds,
            )
            await asyncio.sleep(sleep_seconds)
            continue

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            last_error = str(exc)
            log.warning("CoinGecko HTTP error for %s: %s", url, last_error)
            return _error(last_error)

        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = f"malformed JSON response: {exc}"
            log.warning("CoinGecko response decode failed for %s: %s", url, last_error)
            return _error(last_error)

        cache.set(key, data, MARKET_DATA_CACHE_TTL_SECONDS)
        return data

    # Defensive: the loop above always returns on its final iteration, but a
    # fallback keeps this function honest about never raising.
    return _error(last_error)

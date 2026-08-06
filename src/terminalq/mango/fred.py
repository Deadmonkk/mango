"""Client for the St. Louis Fed's FRED (Federal Reserve Economic Data) API.

Docs: https://fred.stlouisfed.org/docs/api/fred/

This is a clean-room implementation written directly from a written
specification, not from any existing FRED client in this codebase family.
Every public function is defensive: providers in this stack return
``{"error": ...}`` payloads instead of raising, so a network failure, a
missing API key, or a malformed FRED response degrades to an error dict
rather than propagating an exception to the caller.

Other modules in this package register additional friendly aliases by doing
``SERIES_MAP.update(...)`` at import time, so ``SERIES_MAP`` is kept as a
plain mutable module-level dict rather than, say, a frozen mapping.
"""

from __future__ import annotations

import asyncio

import httpx

from terminalq.mango import cache
from terminalq.mango.limiter import RateLimiter
from terminalq.mango.logging import get_logger
from terminalq.mango.redact import redact_text

import os

log = get_logger("fred")

# --- API configuration -------------------------------------------------

BASE_URL = "https://api.stlouisfed.org/fred"

# Read once at import time; tests monkeypatch this module attribute directly
# rather than the environment, mirroring the pattern already used for this
# constant elsewhere in the codebase (see tests/test_release_calendar.py).
FRED_API_KEY: str = os.environ.get("FRED_API_KEY", "")

# FRED's documented rate limit is 120 requests/minute per API key.
FRED_RATE_LIMIT_PER_MINUTE = 120

# One shared limiter for every call this module makes.
_limiter = RateLimiter(FRED_RATE_LIMIT_PER_MINUTE)

# Generous but bounded — FRED is generally fast, but a hung connection should
# not hang the caller indefinitely.
REQUEST_TIMEOUT_SECONDS = 15.0

# Observations move slowly (most series are daily-or-slower), so a
# moderate TTL avoids re-fetching on every call within a report run without
# serving stale data for long.
OBSERVATIONS_CACHE_TTL_SECONDS = 900

# FRED's own marker for a missing observation in a series (e.g. a holiday on
# a daily series, or a not-yet-released period).
MISSING_VALUE_MARKER = "."

# --- Alias registry ------------------------------------------------------

# Friendly alias -> official FRED series ID. Plain mutable dict: other
# modules extend this at import time via SERIES_MAP.update(...).
SERIES_MAP: dict[str, str] = {
    "10y_yield": "DGS10",
    "2y_yield": "DGS2",
    "30y_yield": "DGS30",
    "consumer_sentiment": "UMCSENT",
    "core_cpi": "CPILFESL",
    "cpi": "CPIAUCSL",
    "fed_funds": "DFF",
    "gdp": "GDP",  # nominal GDP — distinct from real_gdp below
    "housing_starts": "HOUST",
    "initial_claims": "ICSA",
    "nonfarm_payrolls": "PAYEMS",
    "pce": "PCE",
    "ppi": "PPIACO",
    "real_gdp": "GDPC1",  # real (inflation-adjusted) GDP — distinct from gdp above
    "unemployment": "UNRATE",
    "yield_spread": "T10Y2Y",
}

# The 11 series shown on the general economic dashboard.
_DASHBOARD_ALIASES = [
    "gdp",
    "cpi",
    "core_cpi",
    "fed_funds",
    "10y_yield",
    "2y_yield",
    "yield_spread",
    "initial_claims",
    "unemployment",
    "nonfarm_payrolls",
    "consumer_sentiment",
]


def _resolve_series_id(name: str) -> str:
    """Resolve a friendly alias to its official FRED series ID.

    Alias lookup is case-insensitive (aliases are conventionally lowercase,
    but a caller might type "CPI"). Anything not a known alias is assumed to
    already be a raw FRED series ID (e.g. "SAHMREALTIME", "PSAVERT") and is
    returned unchanged, preserving its case — FRED series IDs are
    case-sensitive.
    """
    resolved = SERIES_MAP.get(name.lower())
    return resolved if resolved is not None else name


def _no_api_key_error(series_id: str) -> dict:
    return {
        "error": "FRED_API_KEY not configured. Get a free key at "
        "https://fred.stlouisfed.org/docs/api/api_key.html",
        "series_id": series_id,
        "source": "fred",
    }


def _parse_observations(payload: dict) -> list[dict]:
    """Convert FRED's raw observation rows into ``{"date", "value"}`` dicts.

    FRED encodes a missing observation as the literal string ``"."`` rather
    than null or 0 — those rows are dropped entirely rather than coerced to
    a fabricated numeric value, since a fake zero in an economic series is
    far more misleading than a shorter series.
    """
    observations: list[dict] = []
    for row in payload.get("observations", []):
        raw_value = row.get("value")
        if raw_value is None or raw_value == MISSING_VALUE_MARKER:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        observations.append({"date": row.get("date"), "value": value})
    return observations


async def _fetch_observations(client: httpx.AsyncClient, resolved_id: str, limit: int) -> dict:
    """Fetch raw observations for a series, newest first."""
    await _limiter.acquire()
    response = await client.get(
        f"{BASE_URL}/series/observations",
        params={
            "series_id": resolved_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


async def _fetch_metadata(client: httpx.AsyncClient, resolved_id: str) -> dict:
    """Fetch series-level metadata (title/units/frequency) — a separate endpoint."""
    await _limiter.acquire()
    response = await client.get(
        f"{BASE_URL}/series",
        params={
            "series_id": resolved_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


async def get_series(series_id: str, limit: int = 10) -> dict:
    """Fetch observations (and metadata) for an alias or raw FRED series ID.

    Returns, on success::

        {
          "series_id": "<resolved id>",
          "observations": [{"date": "YYYY-MM-DD", "value": <float>}, ...],  # newest first
          "title": "<series title>",
          "units": "<units string>",
          "frequency": "<frequency string>",
        }

    Observations and metadata are two separate FRED endpoints, fetched
    concurrently. If metadata fails but observations succeed, the
    observations are still returned with empty-string metadata fields —
    partial data beats none. Never raises: any failure becomes
    ``{"error": ..., "series_id": ..., "source": "fred"}``.
    """
    resolved = _resolve_series_id(series_id)

    if not FRED_API_KEY:
        return _no_api_key_error(resolved)

    cache_key = f"fred_series_{resolved}_{limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    async with httpx.AsyncClient() as client:
        obs_result, meta_result = await asyncio.gather(
            _fetch_observations(client, resolved, limit),
            _fetch_metadata(client, resolved),
            return_exceptions=True,
        )

    if isinstance(obs_result, BaseException):
        message = redact_text(str(obs_result))
        log.warning("FRED get_series failed for %s: %s", resolved, message)
        return {"error": message, "series_id": resolved, "source": "fred"}

    observations = _parse_observations(obs_result)

    title, units, frequency = "", "", ""
    if isinstance(meta_result, BaseException):
        log.warning(
            "FRED metadata fetch failed for %s: %s", resolved, redact_text(str(meta_result))
        )
    else:
        seriess = meta_result.get("seriess") or []
        if seriess:
            title = seriess[0].get("title", "")
            units = seriess[0].get("units", "")
            frequency = seriess[0].get("frequency", "")

    result = {
        "series_id": resolved,
        "observations": observations,
        "title": title,
        "units": units,
        "frequency": frequency,
    }
    cache.set(cache_key, result, OBSERVATIONS_CACHE_TTL_SECONDS)
    return result


def _dashboard_indicator(result: dict | BaseException) -> dict:
    """Shape one series' get_series() result into a dashboard indicator entry."""
    if isinstance(result, BaseException):
        return {"error": redact_text(str(result))}
    if "error" in result:
        return {"error": result["error"]}

    observations = result.get("observations", [])
    latest = observations[0]["value"] if observations else None
    previous = observations[1]["value"] if len(observations) > 1 else None
    change = round(latest - previous, 4) if latest is not None and previous is not None else None

    return {
        "latest_value": latest,
        "latest_date": observations[0]["date"] if observations else None,
        "previous_value": previous,
        "change": change,
    }


async def get_economic_dashboard() -> dict:
    """Fetch the 11 headline economic indicators concurrently.

    A failed series becomes ``{"error": ...}`` under its own alias rather
    than failing the whole dashboard.
    """
    results = await asyncio.gather(
        *(get_series(alias, limit=2) for alias in _DASHBOARD_ALIASES),
        return_exceptions=True,
    )
    indicators = {
        alias: _dashboard_indicator(result)
        for alias, result in zip(_DASHBOARD_ALIASES, results)
    }
    return {"indicators": indicators, "source": "fred"}

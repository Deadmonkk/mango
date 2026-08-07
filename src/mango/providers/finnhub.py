"""Client for the Finnhub stock market data API.

Docs: https://finnhub.io/docs/api

This is a clean-room implementation written directly from the public API
docs plus this package's existing house style (see ``mango.core.fred`` for
the pattern this mirrors: never raise, cache only clean payloads, redact
secrets before anything touches a log line or an error string). It is not
derived from any other Finnhub client in this codebase family.

Every public function is defensive: a network failure, a missing API key,
or a malformed upstream response degrades to an ``{"error": ...}`` dict
rather than propagating an exception to the caller. A batch call degrades
per-item instead of failing the whole batch, so one bad symbol never takes
down the other 10.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import httpx

from mango.core import cache
from mango.core.env import load_env
from mango.core.limiter import RateLimiter
from mango.core.logging import get_logger
from mango.core.redact import redact_text

log = get_logger("finnhub")

# --- API configuration ----------------------------------------------------

BASE_URL = "https://finnhub.io/api/v1"

# Load ~/.env before resolving the key. Without this the key is found only
# when some other module happened to load the dotfile first — see
# mango.core.env's docstring for the incident that made this explicit
# rather than inherited.
load_env()
FINNHUB_API_KEY: str = os.environ.get("FINNHUB_API_KEY", "")

# Finnhub's free tier documents a 60 requests/minute cap. One request in
# reserve avoids tripping the upstream limit on timing jitter around the
# window boundary (the RateLimiter's sliding window is precise, but a
# handful of concurrent batch calls issued in the same event-loop tick can
# still land inside the same millisecond).
FINNHUB_RATE_LIMIT_PER_MINUTE = 55
_limiter = RateLimiter(FINNHUB_RATE_LIMIT_PER_MINUTE)

# Finnhub is generally fast; a hung connection should not hang the caller.
REQUEST_TIMEOUT_SECONDS = 10.0

# --- Cache TTLs -------------------------------------------------------------
# Each TTL reflects how fast the underlying data actually moves, not a
# single blanket number:

# A live quote changes every trade. A short TTL still saves the duplicate
# calls a single report run makes for the same symbol (e.g. FR pulling SPY
# in one section and the sector rotation panel elsewhere) without serving a
# stale price across a full report.
QUOTE_CACHE_TTL_SECONDS = 30

# Company profile (name, exchange, sector, share count) is effectively
# static intraday — it only changes on corporate actions. A day-long TTL
# avoids re-fetching it on every single run that touches the symbol.
PROFILE_CACHE_TTL_SECONDS = 86_400

# News accrues throughout the day but not sub-minute; a 15-minute window
# balances freshness against not re-fetching on every report re-run.
NEWS_CACHE_TTL_SECONDS = 900

# Earnings history/estimates and analyst recommendation trends are both
# updated at most a few times a day (new estimates, revised recs) — an
# hour-long TTL is generous without being stale for the reports that use it.
EARNINGS_CACHE_TTL_SECONDS = 3_600
RATINGS_CACHE_TTL_SECONDS = 3_600

# The economic calendar is premium-walled on the free tier (see
# get_economic_calendar's docstring) and its error payload is never cached
# per mango.cache_guard, so this TTL only matters on a paid key.
CALENDAR_CACHE_TTL_SECONDS = 3_600

# --- Other constants ---------------------------------------------------

DEFAULT_NEWS_LOOKBACK_DAYS = 7
DEFAULT_CALENDAR_LOOKAHEAD_DAYS = 7
HTTP_FORBIDDEN = 403
_DATE_FORMAT = "%Y-%m-%d"


def _no_api_key_error(**context: object) -> dict:
    return {
        "error": "FINNHUB_API_KEY not configured. Get a free key at https://finnhub.io/register",
        "source": "finnhub",
        **context,
    }


def _error_dict(exc: BaseException, **context: object) -> dict:
    """Turn any exception into the project's standard error-dict shape.

    ``redact_text`` runs on the exception's string form before it goes
    anywhere — httpx bakes request params (including ``token=<key>``) into
    its exception messages, so an unredacted error string is exactly how a
    key leaks into a saved report artifact.
    """
    message = redact_text(str(exc))
    return {"error": message, "source": "finnhub", **context}


def _is_forbidden(exc: BaseException) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == HTTP_FORBIDDEN


async def _fetch_json(client: httpx.AsyncClient, path: str, params: dict) -> dict | list:
    """GET a Finnhub endpoint and return its decoded JSON body.

    The token is passed as a request parameter (never interpolated into the
    URL string by hand) so it lives only in httpx's own param encoding —
    that's still visible in a raised exception's message, which is why every
    caller of this function routes exceptions through ``_error_dict``/
    ``redact_text`` before they're surfaced or logged.
    """
    await _limiter.acquire()
    response = await client.get(
        f"{BASE_URL}{path}",
        params={**params, "token": FINNHUB_API_KEY},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def _date_range(days: int, *, forward: bool) -> tuple[str, str]:
    """Return (from, to) ISO date strings spanning ``days`` back or forward from today."""
    today = datetime.now(timezone.utc).date()
    other = today + timedelta(days=days) if forward else today - timedelta(days=days)
    start, end = (today, other) if forward else (other, today)
    return start.strftime(_DATE_FORMAT), end.strftime(_DATE_FORMAT)


# --- get_quote / get_quotes_batch ------------------------------------------


def _shape_quote(symbol: str, payload: dict) -> dict:
    """Map Finnhub's terse /quote fields (c/h/l/o/pc/d/dp) to named keys.

    Finnhub returns all-zero fields for an unrecognized symbol rather than
    an HTTP error, so a quote of all zeros is passed through as-is (it is
    not this function's job to guess whether that means "invalid symbol" or
    "genuinely halted at zero") — callers can detect it if they need to.
    """
    return {
        "symbol": symbol,
        "current_price": payload.get("c"),
        "change": payload.get("d"),
        "percent_change": payload.get("dp"),
        "high": payload.get("h"),
        "low": payload.get("l"),
        "open": payload.get("o"),
        "previous_close": payload.get("pc"),
        "source": "finnhub",
    }


async def get_quote(symbol: str) -> dict:
    """Fetch a real-time quote for one symbol.

    Returns, on success::

        {
          "symbol": "AAPL",
          "current_price": 227.5,
          "change": 1.2,
          "percent_change": 0.53,
          "high": 228.1,
          "low": 225.9,
          "open": 226.0,
          "previous_close": 226.3,
          "source": "finnhub",
        }

    Never raises: any failure becomes
    ``{"error": ..., "symbol": symbol, "source": "finnhub"}`` — "symbol" is
    always present, on both the success and error paths, because
    ``get_quotes_batch`` callers build a ``{q["symbol"]: q for q in quotes}``
    lookup and a missing key on a failed leg would silently drop that symbol.
    """
    if not FINNHUB_API_KEY:
        return _no_api_key_error(symbol=symbol)

    cache_key = f"finnhub_quote_{symbol}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    async with httpx.AsyncClient() as client:
        try:
            payload = await _fetch_json(client, "/quote", {"symbol": symbol})
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Finnhub get_quote failed for %s: %s", symbol, redact_text(str(exc)))
            return _error_dict(exc, symbol=symbol)

    if not isinstance(payload, dict):
        return _error_dict(ValueError("unexpected response shape"), symbol=symbol)

    result = _shape_quote(symbol, payload)
    cache.set(cache_key, result, QUOTE_CACHE_TTL_SECONDS)
    return result


async def get_quotes_batch(symbols: list[str]) -> list[dict]:
    """Fetch quotes for multiple symbols concurrently.

    Always returns one entry per input symbol, in order, each carrying
    ``symbol`` whether it succeeded or failed — a partial failure degrades
    per-item, it never fails the whole batch. ``get_quote`` already never
    raises, so the ``isinstance`` check below is a defensive backstop, not
    the primary error path.
    """
    results = await asyncio.gather(
        *(get_quote(symbol) for symbol in symbols), return_exceptions=True
    )
    return [
        result if not isinstance(result, BaseException) else _error_dict(result, symbol=symbol)
        for symbol, result in zip(symbols, results)
    ]


# --- get_company_profile ----------------------------------------------------


async def get_company_profile(symbol: str) -> dict:
    """Fetch static company profile info (/stock/profile2).

    Returns, on success::

        {
          "symbol": "AAPL",
          "name": "Apple Inc",
          "country": "US",
          "currency": "USD",
          "exchange": "NASDAQ NMS - GLOBAL MARKET",
          "industry": "Technology",
          "ipo": "1980-12-12",
          "market_cap": 3500000.0,   # millions USD, per Finnhub's own units
          "shares_outstanding": 15000.0,  # millions
          "website": "https://www.apple.com/",
          "logo": "https://...",
          "source": "finnhub",
        }

    Finnhub returns an empty object ``{}`` for an unrecognized symbol rather
    than an HTTP error, so that case is surfaced explicitly as an error dict
    instead of returning a profile of all-None fields.
    """
    if not FINNHUB_API_KEY:
        return _no_api_key_error(symbol=symbol)

    cache_key = f"finnhub_profile_{symbol}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    async with httpx.AsyncClient() as client:
        try:
            payload = await _fetch_json(client, "/stock/profile2", {"symbol": symbol})
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Finnhub get_company_profile failed for %s: %s", symbol, redact_text(str(exc)))
            return _error_dict(exc, symbol=symbol)

    if not isinstance(payload, dict) or not payload:
        return {"error": f"no profile data found for {symbol}", "symbol": symbol, "source": "finnhub"}

    result = {
        "symbol": symbol,
        "name": payload.get("name"),
        "country": payload.get("country"),
        "currency": payload.get("currency"),
        "exchange": payload.get("exchange"),
        "industry": payload.get("finnhubIndustry"),
        "ipo": payload.get("ipo"),
        "market_cap": payload.get("marketCapitalization"),
        "shares_outstanding": payload.get("shareOutstanding"),
        "website": payload.get("weburl"),
        "logo": payload.get("logo"),
        "source": "finnhub",
    }
    cache.set(cache_key, result, PROFILE_CACHE_TTL_SECONDS)
    return result


# --- get_company_news --------------------------------------------------


async def get_company_news(symbol: str, days: int = DEFAULT_NEWS_LOOKBACK_DAYS) -> dict:
    """Fetch recent company news (/company-news) over the trailing ``days``.

    Returns, on success::

        {
          "symbol": "AAPL",
          "from_date": "2026-07-31",
          "to_date": "2026-08-07",
          "count": 12,
          "articles": [
            {
              "headline": "...", "summary": "...", "source": "Reuters",
              "url": "https://...", "datetime": "2026-08-06T14:03:00+00:00",
              "category": "company",
            },
            ...
          ],
          "source": "finnhub",
        }
    """
    if not FINNHUB_API_KEY:
        return _no_api_key_error(symbol=symbol)

    from_date, to_date = _date_range(days, forward=False)
    cache_key = f"finnhub_news_{symbol}_{from_date}_{to_date}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    async with httpx.AsyncClient() as client:
        try:
            payload = await _fetch_json(
                client, "/company-news", {"symbol": symbol, "from": from_date, "to": to_date}
            )
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Finnhub get_company_news failed for %s: %s", symbol, redact_text(str(exc)))
            return _error_dict(exc, symbol=symbol)

    if not isinstance(payload, list):
        return _error_dict(ValueError("unexpected response shape"), symbol=symbol)

    articles = [
        {
            "headline": item.get("headline"),
            "summary": item.get("summary"),
            "source": item.get("source"),
            "url": item.get("url"),
            "datetime": _epoch_to_iso(item.get("datetime")),
            "category": item.get("category"),
        }
        for item in payload
    ]
    result = {
        "symbol": symbol,
        "from_date": from_date,
        "to_date": to_date,
        "count": len(articles),
        "articles": articles,
        "source": "finnhub",
    }
    cache.set(cache_key, result, NEWS_CACHE_TTL_SECONDS)
    return result


def _epoch_to_iso(epoch_seconds: object) -> str | None:
    """Convert Finnhub's Unix-epoch timestamps to ISO 8601, tolerating bad input."""
    if not isinstance(epoch_seconds, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


# --- get_earnings ------------------------------------------------------


async def get_earnings(symbol: str) -> dict:
    """Fetch historical quarterly EPS actual-vs-estimate (/stock/earnings).

    Returns, on success::

        {
          "symbol": "AAPL",
          "earnings": [
            {"period": "2026-06-27", "actual": 1.4, "estimate": 1.35,
             "surprise": 0.05, "surprise_percent": 3.7},
            ...
          ],
          "source": "finnhub",
        }
    """
    if not FINNHUB_API_KEY:
        return _no_api_key_error(symbol=symbol)

    cache_key = f"finnhub_earnings_{symbol}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    async with httpx.AsyncClient() as client:
        try:
            payload = await _fetch_json(client, "/stock/earnings", {"symbol": symbol})
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Finnhub get_earnings failed for %s: %s", symbol, redact_text(str(exc)))
            return _error_dict(exc, symbol=symbol)

    if not isinstance(payload, list):
        return _error_dict(ValueError("unexpected response shape"), symbol=symbol)

    earnings = [
        {
            "period": item.get("period"),
            "actual": item.get("actual"),
            "estimate": item.get("estimate"),
            "surprise": item.get("surprise"),
            "surprise_percent": item.get("surprisePercent"),
        }
        for item in payload
    ]
    result = {"symbol": symbol, "earnings": earnings, "source": "finnhub"}
    cache.set(cache_key, result, EARNINGS_CACHE_TTL_SECONDS)
    return result


# --- get_analyst_ratings -------------------------------------------------


async def get_analyst_ratings(symbol: str) -> dict:
    """Fetch analyst recommendation trends (/stock/recommendation).

    Returns, on success::

        {
          "symbol": "AAPL",
          "ratings": [
            {"period": "2026-08-01", "strong_buy": 12, "buy": 18, "hold": 5,
             "sell": 1, "strong_sell": 0},
            ...
          ],
          "source": "finnhub",
        }
    """
    if not FINNHUB_API_KEY:
        return _no_api_key_error(symbol=symbol)

    cache_key = f"finnhub_ratings_{symbol}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    async with httpx.AsyncClient() as client:
        try:
            payload = await _fetch_json(client, "/stock/recommendation", {"symbol": symbol})
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Finnhub get_analyst_ratings failed for %s: %s", symbol, redact_text(str(exc)))
            return _error_dict(exc, symbol=symbol)

    if not isinstance(payload, list):
        return _error_dict(ValueError("unexpected response shape"), symbol=symbol)

    ratings = [
        {
            "period": item.get("period"),
            "strong_buy": item.get("strongBuy"),
            "buy": item.get("buy"),
            "hold": item.get("hold"),
            "sell": item.get("sell"),
            "strong_sell": item.get("strongSell"),
        }
        for item in payload
    ]
    result = {"symbol": symbol, "ratings": ratings, "source": "finnhub"}
    cache.set(cache_key, result, RATINGS_CACHE_TTL_SECONDS)
    return result


# --- get_economic_calendar -----------------------------------------------


async def get_economic_calendar(days: int = DEFAULT_CALENDAR_LOOKAHEAD_DAYS) -> dict:
    """Fetch upcoming high-impact economic releases (/calendar/economic).

    This endpoint is premium-walled on Finnhub's free tier and returns
    HTTP 403 there in normal operation — that is an expected condition for a
    free-tier key, not a crash. It is surfaced as a plain error dict and is
    NOT retried (a 403 means "your plan doesn't include this," retrying
    changes nothing and only burns rate-limit budget). Callers on the free
    tier are expected to fall back to a FRED-derived release calendar.
    """
    if not FINNHUB_API_KEY:
        return _no_api_key_error()

    from_date, to_date = _date_range(days, forward=True)
    cache_key = f"finnhub_calendar_{from_date}_{to_date}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    async with httpx.AsyncClient() as client:
        try:
            payload = await _fetch_json(
                client, "/calendar/economic", {"from": from_date, "to": to_date}
            )
        except httpx.HTTPStatusError as exc:
            if _is_forbidden(exc):
                log.info("Finnhub economic calendar is premium-walled on this key (403); not retrying.")
                return {
                    "error": "Finnhub economic calendar requires a premium plan (HTTP 403).",
                    "source": "finnhub",
                }
            log.warning("Finnhub get_economic_calendar failed: %s", redact_text(str(exc)))
            return _error_dict(exc)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Finnhub get_economic_calendar failed: %s", redact_text(str(exc)))
            return _error_dict(exc)

    if not isinstance(payload, dict):
        return _error_dict(ValueError("unexpected response shape"))

    raw_events = payload.get("economicCalendar") or []
    events = [
        {
            "date": item.get("time"),
            "country": item.get("country"),
            "event": item.get("event"),
            "impact": item.get("impact"),
            "actual": item.get("actual"),
            "estimate": item.get("estimate"),
            "prev": item.get("prev"),
            "unit": item.get("unit"),
        }
        for item in raw_events
    ]
    result = {
        "from_date": from_date,
        "to_date": to_date,
        "count": len(events),
        "events": events,
        "source": "finnhub",
    }
    cache.set(cache_key, result, CALENDAR_CACHE_TTL_SECONDS)
    return result

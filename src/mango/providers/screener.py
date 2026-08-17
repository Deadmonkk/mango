"""S&P 500 stock screener: sector and market-cap filtering.

Clean-room implementation written directly from a written specification, not
from any prior screener provider in this codebase family. This module's
return shape is NOT fixed by an existing saved payload (unlike
`mango.providers.technical` / `mango.providers.search`), so the shape below
is this module's own design, chosen to mirror the `{"results", "count",
"criteria", "is_complete", "source"}` envelope the specification asked for.

Design choices, and why
------------------------
Constituent list: scraped from Wikipedia's "List of S&P 500 companies"
(the conventional free source for this data) via `mango.core.html.table_rows`
— no third-party HTML parser dependency, matching this pack's existing
style. The page's GICS-sector column means sector filtering never needs a
second network call, only the market-cap filter does.

Market-cap enrichment: `mango.providers.finnhub.get_company_profile` (already
owned in this pack, already rate-limited, already returns `market_cap` in
millions USD). yfinance was the other option; Finnhub was preferred because
it is a single small JSON response per symbol against a keyed, documented
rate limit, versus yfinance's heavier per-ticker `.info` scrape against an
undocumented limit — better fit for enriching up to ~60 symbols per screen.

Fetch budget: enriching all ~500 constituents on every call would take
multiple rate-limit windows. `SCREENER_FETCH_BUDGET` caps how many
sector-filtered candidates get a market-cap lookup per call; `is_complete`
is False exactly when that budget — not a user filter or `limit` — is what
cut the result set short.

Every public function is defensive: a network failure, a changed page
layout, or a missing API key degrades to an ``{"error": ...}`` dict rather
than raising, mirroring the convention used throughout `mango.core`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from mango.core import http

from mango.core import cache
from mango.core.html import BROWSER_HEADERS, table_rows
from mango.core.logging import get_logger
from mango.core.redact import redact_text
from mango.providers import finnhub

log = get_logger("screener")

SOURCE = "wikipedia+finnhub"

CONSTITUENTS_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
REQUEST_TIMEOUT_SECONDS = 15.0

# The constituent list changes a handful of times a year (index
# additions/removals), nothing like daily-market-data cadence — cache
# aggressively so a normal report run never re-scrapes Wikipedia.
CONSTITUENTS_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days
CONSTITUENTS_CACHE_KEY = "sp500_constituents"

# How many sector-filtered candidates get a market-cap lookup per call.
# Sized to fit inside one Finnhub rate-limit window (55 req/min, see
# mango.providers.finnhub) so a single screen call doesn't have to sleep
# through multiple windows.
SCREENER_FETCH_BUDGET = 55

DEFAULT_LIMIT = 20


def _error(message: str, **context: object) -> dict:
    return {"error": message, "source": SOURCE, **context}


# --- constituent list (Wikipedia) -------------------------------------


async def _fetch_constituents_html() -> str:
    return await http.fetch_text(
        CONSTITUENTS_URL, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS
    )


def _header_index(header: list[str], *keywords: str) -> int | None:
    """Index of the first header cell containing any of `keywords` (case-insensitive)."""
    for i, cell in enumerate(header):
        lowered = cell.lower()
        if any(keyword in lowered for keyword in keywords):
            return i
    return None


def _parse_constituents(html_text: str) -> list[dict[str, str]] | None:
    """Parse the constituent table into `{symbol, name, sector}` rows.

    Column positions are looked up by header text, not fixed indices — a
    reordered or added column on Wikipedia's side should not break parsing,
    only a renamed/removed one should (and that case returns None, treated
    as a normal failure by the caller, not a crash).
    """
    rows = table_rows(html_text)
    if not rows:
        return None

    header = [cell.strip() for cell in rows[0]]
    symbol_idx = _header_index(header, "symbol", "ticker")
    name_idx = _header_index(header, "security", "company", "name")
    sector_idx = _header_index(header, "sector")
    if symbol_idx is None or name_idx is None or sector_idx is None:
        return None

    required_width = max(symbol_idx, name_idx, sector_idx) + 1
    constituents: list[dict[str, str]] = []
    for row in rows[1:]:
        if len(row) < required_width:
            continue
        symbol = row[symbol_idx].strip()
        if not symbol:
            continue
        constituents.append(
            {"symbol": symbol, "name": row[name_idx].strip(), "sector": row[sector_idx].strip()}
        )
    return constituents or None


async def get_sp500_constituents() -> dict:
    """Fetch the current S&P 500 constituent list (symbol, name, GICS sector).

    Returns, on success::

        {"constituents": [{"symbol", "name", "sector"}, ...], "count": int, "source": SOURCE}

    A layout change on the source page or a network failure is a normal
    failure, not a crash: both become ``{"error": ..., "source": SOURCE}``.
    """
    cached = cache.get(CONSTITUENTS_CACHE_KEY)
    if cached is not None:
        return cached

    try:
        html_text = await _fetch_constituents_html()
    except (httpx.HTTPError, ValueError) as exc:
        message = redact_text(str(exc))
        log.warning("Screener: failed to fetch S&P 500 constituent list: %s", message)
        return _error(f"failed to fetch S&P 500 constituent list: {message}")

    constituents = _parse_constituents(html_text)
    if constituents is None:
        log.warning("Screener: could not parse S&P 500 constituent table (layout may have changed)")
        return _error("could not parse S&P 500 constituent table (source layout may have changed)")

    result = {"constituents": constituents, "count": len(constituents), "source": SOURCE}
    cache.set(CONSTITUENTS_CACHE_KEY, result, CONSTITUENTS_CACHE_TTL_SECONDS)
    return result


# --- market-cap enrichment -----------------------------------------------


async def _enrich_with_market_cap(candidates: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Attach `market_cap` (millions USD) to each candidate via Finnhub.

    A candidate whose profile fetch failed, or came back with no market-cap
    figure, is dropped rather than kept with a fabricated value — same
    "None beats a wrong number" rule as the technical-indicator provider.
    """
    profiles = await asyncio.gather(
        *(finnhub.get_company_profile(c["symbol"]) for c in candidates)
    )
    enriched: list[dict[str, Any]] = []
    for candidate, profile in zip(candidates, profiles):
        if "error" in profile:
            continue
        market_cap = profile.get("market_cap")
        if market_cap is None:
            continue
        enriched.append({**candidate, "market_cap": market_cap})
    return enriched


def _apply_market_cap_filter(
    rows: list[dict[str, Any]], min_market_cap: float, max_market_cap: float
) -> list[dict[str, Any]]:
    filtered = rows
    if min_market_cap > 0:
        filtered = [r for r in filtered if r["market_cap"] >= min_market_cap]
    if max_market_cap > 0:
        filtered = [r for r in filtered if r["market_cap"] <= max_market_cap]
    return filtered


# --- public entry point --------------------------------------------------


async def screen_stocks(
    sector: str = "",
    min_market_cap: float = 0,
    max_market_cap: float = 0,
    limit: int = DEFAULT_LIMIT,
) -> dict:
    """Screen S&P 500 constituents by sector and/or market cap (millions USD).

    Returns, on success::

        {
          "results": [{"symbol", "name", "sector", "market_cap"}, ...],
          "count": int, "criteria": {...}, "is_complete": bool, "source": SOURCE,
        }

    `market_cap` and `max_market_cap` are in millions USD, matching
    `mango.providers.finnhub.get_company_profile`'s own units. `is_complete`
    is False only when `SCREENER_FETCH_BUDGET` — not the sector filter or
    `limit` — is what cut the result set short; a sector-only screen (no
    market-cap bounds) never needs the budget and is always complete.

    Never raises: a constituent-list failure, or a market-cap filter
    requested with no `FINNHUB_API_KEY` configured, becomes an
    ``{"error": ...}`` dict.
    """
    criteria = {
        "sector": sector,
        "min_market_cap": min_market_cap,
        "max_market_cap": max_market_cap,
        "limit": limit,
    }

    constituents_result = await get_sp500_constituents()
    if "error" in constituents_result:
        return _error(constituents_result["error"], criteria=criteria)

    all_constituents = constituents_result["constituents"]
    sector_filtered = (
        [c for c in all_constituents if sector.lower() in c["sector"].lower()]
        if sector
        else all_constituents
    )

    wants_market_cap_filter = min_market_cap > 0 or max_market_cap > 0
    if not wants_market_cap_filter:
        results = [{**c, "market_cap": None} for c in sector_filtered[:limit]]
        return {
            "results": results,
            "count": len(results),
            "criteria": criteria,
            "is_complete": True,
            "source": SOURCE,
        }

    if not finnhub.FINNHUB_API_KEY:
        return _error(
            "FINNHUB_API_KEY not configured; cannot apply a market-cap filter "
            "(get a free key at https://finnhub.io/register)",
            criteria=criteria,
        )

    truncated_by_budget = len(sector_filtered) > SCREENER_FETCH_BUDGET
    candidates = sector_filtered[:SCREENER_FETCH_BUDGET]

    enriched = await _enrich_with_market_cap(candidates)
    qualifying = _apply_market_cap_filter(enriched, min_market_cap, max_market_cap)

    results = qualifying[:limit]
    return {
        "results": results,
        "count": len(results),
        "criteria": criteria,
        "is_complete": not truncated_by_budget,
        "source": SOURCE,
    }

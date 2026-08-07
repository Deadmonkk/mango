"""High-level CoinGecko API surface: quotes, market overview, deep dives,
derivatives, dominance, trending, and a screener.

Clean-room implementation written directly from a written specification and
CoinGecko's public docs (https://docs.coingecko.com/reference/introduction),
not from any existing CoinGecko client in this codebase family.

This module owns the *shape* of the data (what a "deep dive" or a "market
overview" looks like); ``mango.core.coingecko`` owns the *mechanics* of
talking to CoinGecko (symbol -> coin id resolution, rate limiting, 429
retry/backoff, and caching via ``_fetch``). Every request in this module goes
through that shared ``_fetch`` so none of those concerns are duplicated here.

Error convention (deliberately the OPPOSITE spelling of the core layer):
every public function in this module returns ``{"error": "...", "source":
"coingecko"}`` on failure — no leading underscore. The core layer's
``_fetch`` returns ``{"_error": "..."}`` (underscore) because its sole
existing consumer, ``crypto_analytics.py``, tests for that exact key to
decide whether to fall back to Yahoo Finance. This module is not that
consumer: it translates ``_error`` into the normal ``error`` spelling at the
boundary (see ``_as_error``) so callers of *this* module see the
project-wide convention, and so nothing downstream mistakes a translated
CoinGecko failure for the specific signal that triggers the Yahoo fallback.
"""

from __future__ import annotations

from typing import Any

import httpx

from mango.core.coingecko import BASE_URL, _fetch, _resolve_id
from mango.core.logging import get_logger
from mango.ext_settings import (
    CRYPTO_ALTCOIN_SEASON_THRESHOLD,
    CRYPTO_FDV_DILUTION_WARNING,
    CRYPTO_FUNDING_CROWDED_LONG,
    CRYPTO_FUNDING_CROWDED_SHORT,
)

log = get_logger("coingecko_api")

# --- Shared request configuration ---------------------------------------

VS_CURRENCY = "usd"

# Per-request timeout for the (few) calls this module makes that don't go
# through CoinGecko at all (Alternative.me's Fear & Greed Index). Mirrors
# REQUEST_TIMEOUT_SECONDS in the core layer.
NON_COINGECKO_TIMEOUT_SECONDS = 15.0

FEAR_GREED_URL = "https://api.alternative.me/fng/"

# --- Endpoints -------------------------------------------------------------

_GLOBAL_ENDPOINT = f"{BASE_URL}/global"
_MARKETS_ENDPOINT = f"{BASE_URL}/coins/markets"
_DERIVATIVES_ENDPOINT = f"{BASE_URL}/derivatives"
_TRENDING_ENDPOINT = f"{BASE_URL}/search/trending"


def _coin_detail_endpoint(coin_id: str) -> str:
    return f"{BASE_URL}/coins/{coin_id}"


# --- Stablecoins used for the "stablecoin dominance" proxy ---------------

# CoinGecko coin ids for the four stablecoins the dominance note advertises
# ("USDT+USDC+BUSD+DAI"). Fixed, not derived, because CoinGecko has no
# reliable "is this a stablecoin" flag on the markets endpoint that would
# reproduce exactly this basket.
_STABLECOIN_IDS: list[str] = ["tether", "usd-coin", "binance-usd", "dai"]

# --- Derivatives dashboard: which base assets to summarize -----------------

# CoinGecko's /derivatives endpoint returns thousands of individual futures
# and perpetual contracts across every exchange it tracks; the FR report only
# ever surfaces a handful of majors, so we group down to just these rather
# than returning an unbounded per-contract dump.
_DERIVATIVES_TRACKED_SYMBOLS: list[str] = ["BTC", "ETH", "SOL", "BNB", "XRP"]

# Funding rates on perpetual futures settle 3x/day (every 8h) on essentially
# every exchange CoinGecko aggregates. Annualizing a per-8h rate is therefore
# rate * 3 * 365.
_FUNDING_SETTLEMENTS_PER_DAY = 3
_DAYS_PER_YEAR = 365
FUNDING_ANNUALIZATION_FACTOR = _FUNDING_SETTLEMENTS_PER_DAY * _DAYS_PER_YEAR

# --- Dominance / altcoin-season judgment thresholds -------------------------

# BTC dominance level above which we call it "high — flight to BTC quality".
# CoinGecko has no canonical threshold for this; 50% (BTC being more than
# half of all crypto market cap) is a defensible, round judgment call, not a
# published standard. Documented here rather than left as a bare literal.
BTC_DOMINANCE_HIGH_PCT = 50.0

# Stablecoin share of total market cap above which we flag "elevated — cash
# rotation out of crypto". Also a judgment call, not a CoinGecko standard.
STABLECOIN_DOMINANCE_ELEVATED_PCT = 15.0

# How many of the largest non-BTC, non-stablecoin coins to sample when
# computing the "altcoin season" ratio (coins beating BTC's 30d return).
# CoinGecko's own altcoin-season definition (used by blockchaincenter.net)
# samples the top 50; we use a smaller, cheaper sample since this endpoint
# already makes several other calls. Judgment call, documented rather than
# a bare literal.
ALTCOIN_SEASON_SAMPLE_SIZE = 20

# --- Developer-activity signal threshold ------------------------------------

# Minimum 4-week commit count to call a project's development "active" rather
# than "quiet". Judgment call (CoinGecko provides no such signal itself).
DEV_ACTIVITY_MIN_COMMITS_4W = 1

# --- Screener defaults -------------------------------------------------------

# CoinGecko's own hard cap on `per_page` for /coins/markets.
SCREEN_CANDIDATE_POOL_SIZE = 250

# sort_by name -> normalized market-item field it sorts on. Unrecognized
# sort_by values fall back to "market_cap" rather than raising.
_SORT_KEY_MAP: dict[str, str] = {
    "market_cap": "market_cap",
    "volume": "total_volume",
    "price_change_24h": "price_change_pct_24h",
    "price_change_7d": "price_change_pct_7d",
    "price_change_30d": "price_change_pct_30d",
}

_BILLION = 1_000_000_000


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _as_error(payload: dict) -> dict | None:
    """Translate the core layer's ``_error`` convention into this layer's.

    Returns a normal ``{"error": ..., "source": "coingecko"}`` dict if
    ``payload`` is a failed ``_fetch`` result, else ``None``. This is the one
    place the underscore-to-no-underscore translation happens — see the
    module docstring for why the spelling difference is load-bearing.
    """
    if isinstance(payload, dict) and "_error" in payload:
        return {"error": payload["_error"], "source": "coingecko"}
    return None


def _safe_float(value: Any) -> float | None:
    """Best-effort float coercion that never raises.

    Some CoinGecko endpoints (notably /search/trending, across API
    revisions) have been observed to return numeric fields as either raw
    numbers or formatted strings (e.g. ``"$64,941.20"``). Accepting both
    keeps this module from breaking on a formatting change it doesn't
    control.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace("$", "").replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _funding_signal(rate_8h_pct: float) -> str:
    """Interpret an average 8h funding rate as a positioning signal.

    Thresholds come from ``mango.ext_settings`` (shared with the rest of the
    pack) so a "crowded" call here means the same thing it means anywhere
    else funding rates are read.
    """
    if rate_8h_pct >= CRYPTO_FUNDING_CROWDED_LONG:
        return f"crowded LONG — longs paying {rate_8h_pct:.4f}%/8h, short squeeze risk"
    if rate_8h_pct <= CRYPTO_FUNDING_CROWDED_SHORT:
        return f"crowded SHORT — shorts paying {abs(rate_8h_pct):.4f}%/8h, long squeeze risk"
    if rate_8h_pct > 0:
        return "mild bullish bias"
    if rate_8h_pct < 0:
        return "mild bearish bias"
    return "neutral"


def _normalize_market_item(item: dict) -> dict:
    """Map one raw ``/coins/markets`` entry to this module's flat quote shape.

    Shared by ``get_crypto_quote``, ``get_crypto_batch``, and
    ``screen_cryptos`` so the three functions can never drift into
    incompatible shapes for the same underlying data.
    """
    return {
        "symbol": str(item.get("symbol", "")).upper(),
        "coin_id": item.get("id"),
        "name": item.get("name"),
        "current_price": item.get("current_price"),
        "market_cap": item.get("market_cap"),
        "market_cap_rank": item.get("market_cap_rank"),
        "total_volume": item.get("total_volume"),
        "high_24h": item.get("high_24h"),
        "low_24h": item.get("low_24h"),
        "price_change_24h": item.get("price_change_24h"),
        "price_change_pct_24h": _round_or_none(item.get("price_change_percentage_24h"), 1),
        "price_change_pct_7d": _round_or_none(item.get("price_change_percentage_7d_in_currency"), 1),
        "price_change_pct_30d": _round_or_none(item.get("price_change_percentage_30d_in_currency"), 1),
        "circulating_supply": item.get("circulating_supply"),
        "total_supply": item.get("total_supply"),
        "ath": item.get("ath"),
        "ath_change_pct": item.get("ath_change_percentage"),
        "source": "coingecko",
    }


def _round_or_none(value: Any, digits: int) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


async def _fetch_markets(
    client: httpx.AsyncClient,
    *,
    ids: str | None = None,
    category: str | None = None,
    per_page: int = SCREEN_CANDIDATE_POOL_SIZE,
) -> dict | list:
    params: dict[str, Any] = {
        "vs_currency": VS_CURRENCY,
        "order": "market_cap_desc",
        "per_page": per_page,
        "page": 1,
        "price_change_percentage": "7d,30d",
        "sparkline": "false",
    }
    if ids:
        params["ids"] = ids
    if category:
        params["category"] = category
    return await _fetch(client, _MARKETS_ENDPOINT, params)


# ---------------------------------------------------------------------------
# get_crypto_quote / get_crypto_batch
# ---------------------------------------------------------------------------


async def get_crypto_batch(symbols: list[str]) -> list[dict]:
    """Market snapshot for a list of ticker symbols in one request.

    Returns a plain list of items shaped like ``_normalize_market_item`` on
    success — deliberately a bare list, not a dict wrapper, even though nothing
    else in this module returns a bare list. This is evidence-driven, not a
    style choice: a real saved payload (``crypto_batch`` in a saved
    ``fr_raw_*.json``) is a literal JSON array with no wrapping key, and the
    collector that produces it (``fr_collect.py``) stores this function's
    return value completely unmodified — so a dict-wrapped return here would
    silently break that consumer. An unresolvable/unknown symbol is simply
    absent from the result (partial success is not an error — CoinGecko
    itself just omits ids it doesn't recognize rather than erroring).

    On failure the list contract is preserved so callers that iterate the
    result never crash: a single-element list carrying the translated error,
    ``[{"error": "...", "source": "coingecko"}]``.
    """
    if not symbols:
        return []

    ids = ",".join(_resolve_id(s) for s in symbols)
    async with httpx.AsyncClient() as client:
        data = await _fetch_markets(client, ids=ids, per_page=len(symbols))

    error = _as_error(data) if isinstance(data, dict) else None
    if error:
        return [error]
    if not isinstance(data, list):
        log.warning("Unexpected /coins/markets response shape for batch: %r", type(data))
        return []

    return [_normalize_market_item(item) for item in data]


async def get_crypto_quote(symbol: str) -> dict:
    """Market snapshot for a single ticker symbol.

    Delegates to ``get_crypto_batch`` for a single symbol so the two
    functions can never disagree on field names or units.
    """
    batch = await get_crypto_batch([symbol])
    if not batch:
        return {"error": f"no market data found for symbol '{symbol}'", "source": "coingecko"}
    return batch[0]


# ---------------------------------------------------------------------------
# get_crypto_market_overview
# ---------------------------------------------------------------------------


def _fear_greed_classification_to_signal(classification: str) -> str:
    """Map Alternative.me's title-cased classification to a lowercase signal.

    Alternative.me's classifications are fixed strings ("Extreme Fear",
    "Fear", "Neutral", "Greed", "Extreme Greed"); lower-casing is a safe,
    lossless transform and matches the one classification->signal pairing
    ("Fear" -> "fear") observed in a real saved payload.
    """
    return classification.lower()


async def _fetch_fear_greed(client: httpx.AsyncClient, limit: int) -> tuple[dict | None, list[dict]]:
    """Fetch Alternative.me's Fear & Greed Index.

    Returns ``(current, history)`` where ``current`` is the newest reading
    (with a ``signal`` key added) and ``history`` is that same current
    reading followed by the remaining ``limit - 1`` days *without* a
    ``signal`` key — matching the shape observed in a real saved
    ``crypto_market_overview`` payload, where only the first (current) entry
    of the 7-day series carries a ``signal``. On any failure both values are
    empty (``None`` / ``[]``) — a Fear & Greed outage degrades the overview,
    it does not fail it.
    """
    raw = await _fetch(client, FEAR_GREED_URL, {"limit": limit, "format": "json"})
    if _as_error(raw) is not None:
        return None, []

    entries = raw.get("data") if isinstance(raw, dict) else None
    if not entries:
        return None, []

    parsed = []
    for entry in entries:
        try:
            value = int(entry["value"])
        except (KeyError, TypeError, ValueError):
            continue
        parsed.append(
            {
                "value": value,
                "classification": entry.get("value_classification", ""),
                "date": entry.get("timestamp", ""),
            }
        )
    if not parsed:
        return None, []

    current = dict(parsed[0])
    current["signal"] = _fear_greed_classification_to_signal(current["classification"])
    history = [current] + parsed[1:limit]
    return current, history


async def get_crypto_market_overview() -> dict:
    """Total market cap, 24h volume/change, dominance, and Fear & Greed.

    Combines CoinGecko's ``/global`` with Alternative.me's Fear & Greed
    Index — the two sources a real saved overview payload cites
    (``"source": "coingecko + alternative.me"``).
    """
    async with httpx.AsyncClient() as client:
        global_raw = await _fetch(client, _GLOBAL_ENDPOINT, {})
        error = _as_error(global_raw)
        if error:
            return error

        global_data = global_raw.get("data") if isinstance(global_raw, dict) else None
        if not isinstance(global_data, dict):
            return {"error": "malformed /global response", "source": "coingecko"}

        total_market_cap_usd = (global_data.get("total_market_cap") or {}).get("usd")
        stablecoins_proxy = await _fetch_stablecoin_dominance_pct(client, total_market_cap_usd)
        fg_current, fg_history = await _fetch_fear_greed(client, limit=7)

    market_cap_change_pct = global_data.get("market_cap_change_percentage_24h_usd", 0.0) or 0.0
    market_cap_percentage = global_data.get("market_cap_percentage", {}) or {}

    return {
        "total_market_cap_usd": total_market_cap_usd,
        "total_volume_24h_usd": (global_data.get("total_volume") or {}).get("usd"),
        "market_cap_change_24h_pct": market_cap_change_pct,
        "market_cap_signal": "expanding" if market_cap_change_pct > 0 else "contracting" if market_cap_change_pct < 0 else "flat",
        "dominance": {
            "btc": market_cap_percentage.get("btc"),
            "eth": market_cap_percentage.get("eth"),
            "stablecoins_proxy": stablecoins_proxy if stablecoins_proxy is not None else 0.0,
        },
        "fear_greed_current": fg_current,
        "fear_greed_7d": fg_history,
        "active_cryptocurrencies": global_data.get("active_cryptocurrencies"),
        "note": "Stablecoin dominance = USDT+USDC+BUSD+DAI. Rising = cash rotation out of crypto.",
        "source": "coingecko + alternative.me",
    }


# ---------------------------------------------------------------------------
# get_crypto_deep
# ---------------------------------------------------------------------------

# market_data field -> (nested-by-currency?, returns key)
_RETURNS_FIELD_MAP: list[tuple[str, bool, str]] = [
    ("price_change_percentage_1h_in_currency", True, "1h"),
    ("price_change_percentage_24h", False, "24h"),
    ("price_change_percentage_7d", False, "7d"),
    ("price_change_percentage_14d", False, "14d"),
    ("price_change_percentage_30d", False, "30d"),
    ("price_change_percentage_60d", False, "60d"),
    ("price_change_percentage_200d", False, "200d"),
    ("price_change_percentage_1y", False, "1y"),
]

_RETURNS_ROUND_DIGITS = 5


def _extract_returns(market_data: dict) -> dict[str, float | None]:
    returns: dict[str, float | None] = {}
    for field, nested, key in _RETURNS_FIELD_MAP:
        raw = market_data.get(field)
        if nested and isinstance(raw, dict):
            raw = raw.get(VS_CURRENCY)
        returns[key] = _round_or_none(raw, _RETURNS_ROUND_DIGITS)
    return returns


def _dilution_signal(ratio: float | None) -> str:
    if ratio is None:
        return "unknown — fully diluted valuation not available"
    if ratio >= CRYPTO_FDV_DILUTION_WARNING:
        return "high dilution risk — large gap between FDV and market cap"
    return "low dilution risk — most supply already circulating"


def _build_supply_block(market_data: dict) -> dict:
    circulating = market_data.get("circulating_supply")
    total = market_data.get("total_supply")
    market_cap_usd = (market_data.get("market_cap") or {}).get(VS_CURRENCY)
    fdv_usd = (market_data.get("fully_diluted_valuation") or {}).get(VS_CURRENCY)

    circulating_pct_of_total = None
    if circulating is not None and total:
        circulating_pct_of_total = round((circulating / total) * 100, 2)

    fdv_ratio = None
    if fdv_usd is not None and market_cap_usd:
        fdv_ratio = round(fdv_usd / market_cap_usd, 4)

    return {
        "circulating": circulating,
        "total": total,
        "max": market_data.get("max_supply"),
        "circulating_pct_of_total": circulating_pct_of_total,
        "market_cap_usd": market_cap_usd,
        "fully_diluted_valuation_usd": fdv_usd,
        "fdv_to_market_cap_ratio": fdv_ratio,
        "dilution_signal": _dilution_signal(fdv_ratio),
    }


def _build_community_block(community_data: dict) -> dict:
    return {
        "reddit_subscribers": community_data.get("reddit_subscribers", 0) or 0,
        "reddit_active_48h": community_data.get("reddit_accounts_active_48h", 0) or 0,
        "twitter_followers": community_data.get("twitter_followers"),
        "telegram_users": community_data.get("telegram_channel_user_count"),
    }


def _build_developer_block(developer_data: dict) -> dict:
    commits_4w = developer_data.get("commit_count_4_weeks", 0) or 0
    return {
        "github_stars": developer_data.get("stars"),
        "github_forks": developer_data.get("forks"),
        "commits_4_weeks": commits_4w,
        "pull_requests_merged": developer_data.get("pull_requests_merged"),
        "contributors": developer_data.get("pull_request_contributors"),
        "dev_signal": "active" if commits_4w >= DEV_ACTIVITY_MIN_COMMITS_4W else "quiet",
    }


async def get_crypto_deep(symbol: str) -> dict:
    """Full profile for one coin: price, supply/dilution, multi-window
    returns, ATH/ATL, community, and developer activity.
    """
    coin_id = _resolve_id(symbol)
    params = {
        "localization": "false",
        "tickers": "false",
        "market_data": "true",
        "community_data": "true",
        "developer_data": "true",
        "sparkline": "false",
    }
    async with httpx.AsyncClient() as client:
        raw = await _fetch(client, _coin_detail_endpoint(coin_id), params)

    error = _as_error(raw)
    if error:
        return error
    if not isinstance(raw, dict) or "market_data" not in raw:
        return {"error": f"no coin data found for symbol '{symbol}'", "source": "coingecko"}

    market_data = raw.get("market_data") or {}
    community_data = raw.get("community_data") or {}
    developer_data = raw.get("developer_data") or {}

    return {
        "symbol": str(raw.get("symbol", symbol)).upper(),
        "name": raw.get("name"),
        "price_usd": (market_data.get("current_price") or {}).get(VS_CURRENCY),
        "supply": _build_supply_block(market_data),
        "returns": _extract_returns(market_data),
        "ath_usd": (market_data.get("ath") or {}).get(VS_CURRENCY),
        "ath_change_pct": (market_data.get("ath_change_percentage") or {}).get(VS_CURRENCY),
        "atl_usd": (market_data.get("atl") or {}).get(VS_CURRENCY),
        "community": _build_community_block(community_data),
        "developer": _build_developer_block(developer_data),
        "source": "coingecko",
    }


# ---------------------------------------------------------------------------
# get_crypto_derivatives_dashboard
# ---------------------------------------------------------------------------


def _summarize_derivatives(entries: list[dict]) -> dict | None:
    funding_rates = [e["funding_rate"] for e in entries if isinstance(e.get("funding_rate"), (int, float))]
    open_interests = [e["open_interest"] for e in entries if isinstance(e.get("open_interest"), (int, float))]

    if not funding_rates:
        return None

    avg_funding = round(sum(funding_rates) / len(funding_rates), 6)
    return {
        "avg_funding_rate_8h_pct": avg_funding,
        "avg_funding_annualized_pct": round(avg_funding * FUNDING_ANNUALIZATION_FACTOR, 2),
        "total_open_interest_usd": sum(open_interests) if open_interests else 0.0,
        "exchanges_tracked": len(entries),
        "signal": _funding_signal(avg_funding),
    }


async def get_crypto_derivatives_dashboard() -> dict:
    """Aggregated perpetual-futures funding & open interest for the majors.

    Grouped by base asset (``index_id``) across every exchange CoinGecko's
    ``/derivatives`` endpoint tracks for that asset. Assets in
    ``_DERIVATIVES_TRACKED_SYMBOLS`` with zero matching contracts are simply
    omitted (partial coverage, not an error).
    """
    async with httpx.AsyncClient() as client:
        raw = await _fetch(client, _DERIVATIVES_ENDPOINT, {})

    error = _as_error(raw)
    if error:
        return error
    if not isinstance(raw, list):
        return {"error": "malformed /derivatives response", "source": "coingecko"}

    by_symbol: dict[str, list[dict]] = {sym: [] for sym in _DERIVATIVES_TRACKED_SYMBOLS}
    for entry in raw:
        base = str(entry.get("index_id", "")).upper()
        if base in by_symbol:
            by_symbol[base].append(entry)

    derivatives: dict[str, dict] = {}
    for symbol, entries in by_symbol.items():
        summary = _summarize_derivatives(entries)
        if summary is not None:
            derivatives[symbol] = summary

    return {
        "derivatives": derivatives,
        "note": (
            "Funding rates in percent per 8h. >0: longs pay shorts (bullish tilt). "
            "<0: shorts pay longs. Historical avg ~0.01%/8h (~11%/yr annualized)."
        ),
        "source": "coingecko",
    }


# ---------------------------------------------------------------------------
# get_crypto_dominance
# ---------------------------------------------------------------------------


def _altcoin_season_signal(ratio: float) -> str:
    if ratio >= CRYPTO_ALTCOIN_SEASON_THRESHOLD:
        return "alt season — broad rotation out of BTC"
    if ratio <= (1 - CRYPTO_ALTCOIN_SEASON_THRESHOLD):
        return "BTC season — money concentrating in Bitcoin"
    return "mixed — no clear rotation"


async def _fetch_stablecoin_dominance_pct(client: httpx.AsyncClient, total_market_cap_usd: float | None) -> float | None:
    if not total_market_cap_usd:
        return None
    data = await _fetch_markets(client, ids=",".join(_STABLECOIN_IDS), per_page=len(_STABLECOIN_IDS))
    if _as_error(data) is not None or not isinstance(data, list):
        return None
    stablecoin_mcap = sum(item.get("market_cap") or 0 for item in data)
    return round((stablecoin_mcap / total_market_cap_usd) * 100, 2)


async def _fetch_altcoin_season_detail(client: httpx.AsyncClient) -> dict:
    """Sample the largest non-BTC, non-stablecoin coins and compare their
    30d return against BTC's 30d return.
    """
    data = await _fetch_markets(client, per_page=ALTCOIN_SEASON_SAMPLE_SIZE + 1 + len(_STABLECOIN_IDS))
    if _as_error(data) is not None or not isinstance(data, list):
        return {"coins_beating_btc_30d": 0, "coins_measured": 0, "ratio": 0.0, "btc_30d_return_pct": 0}

    excluded_ids = {"bitcoin", *_STABLECOIN_IDS}
    btc_30d = 0.0
    for item in data:
        if item.get("id") == "bitcoin":
            btc_30d = item.get("price_change_percentage_30d_in_currency") or 0.0
            break

    sample = [item for item in data if item.get("id") not in excluded_ids][:ALTCOIN_SEASON_SAMPLE_SIZE]
    measured = [item for item in sample if item.get("price_change_percentage_30d_in_currency") is not None]
    beating = sum(1 for item in measured if item["price_change_percentage_30d_in_currency"] > btc_30d)

    ratio = round(beating / len(measured), 2) if measured else 0.0
    return {
        "coins_beating_btc_30d": beating,
        "coins_measured": len(measured),
        "ratio": ratio,
        "btc_30d_return_pct": round(btc_30d, 1),
    }


async def get_crypto_dominance() -> dict:
    """BTC/ETH/stablecoin dominance and the altcoin-season read."""
    async with httpx.AsyncClient() as client:
        global_raw = await _fetch(client, _GLOBAL_ENDPOINT, {})
        error = _as_error(global_raw)
        if error:
            return error

        global_data = global_raw.get("data") if isinstance(global_raw, dict) else None
        if not isinstance(global_data, dict):
            return {"error": "malformed /global response", "source": "coingecko"}

        market_cap_percentage = global_data.get("market_cap_percentage", {}) or {}
        btc_pct = market_cap_percentage.get("btc", 0.0) or 0.0
        eth_pct = market_cap_percentage.get("eth", 0.0) or 0.0
        total_mcap = (global_data.get("total_market_cap") or {}).get(VS_CURRENCY)

        stablecoins_pct = await _fetch_stablecoin_dominance_pct(client, total_mcap)
        altcoin_detail = await _fetch_altcoin_season_detail(client)

    stablecoins_pct = stablecoins_pct if stablecoins_pct is not None else 0.0
    altcoins_other_pct = round(100 - btc_pct - eth_pct - stablecoins_pct, 2)

    return {
        "dominance": {
            "btc_pct": round(btc_pct, 2),
            "eth_pct": round(eth_pct, 2),
            "stablecoins_pct": stablecoins_pct,
            "altcoins_other_pct": altcoins_other_pct,
        },
        "signals": {
            "btc_dominance": "high — flight to BTC quality" if btc_pct >= BTC_DOMINANCE_HIGH_PCT else "normal",
            "stablecoin_dominance": (
                "elevated — cash rotation out of crypto"
                if stablecoins_pct >= STABLECOIN_DOMINANCE_ELEVATED_PCT
                else "normal"
            ),
            "altcoin_season": _altcoin_season_signal(altcoin_detail["ratio"]),
        },
        "altcoin_season_detail": altcoin_detail,
        "source": "coingecko",
    }


# ---------------------------------------------------------------------------
# get_crypto_trending
# ---------------------------------------------------------------------------


async def get_crypto_trending() -> dict:
    """The coins currently most-searched on CoinGecko."""
    async with httpx.AsyncClient() as client:
        raw = await _fetch(client, _TRENDING_ENDPOINT, {})

    error = _as_error(raw)
    if error:
        return error
    if not isinstance(raw, dict):
        return {"error": "malformed /search/trending response", "source": "coingecko"}

    coins_raw = raw.get("coins") or []
    trending_coins = []
    for wrapper in coins_raw:
        item = wrapper.get("item") if isinstance(wrapper, dict) else None
        if not isinstance(item, dict):
            continue
        data = item.get("data") or {}
        change_24h = data.get("price_change_percentage_24h")
        if isinstance(change_24h, dict):
            change_24h = change_24h.get(VS_CURRENCY.upper()) or change_24h.get(VS_CURRENCY)
        trending_coins.append(
            {
                "name": item.get("name"),
                "symbol": str(item.get("symbol", "")).upper(),
                "market_cap_rank": item.get("market_cap_rank"),
                "price_usd": _safe_float(data.get("price")),
                "change_24h_pct": _safe_float(change_24h),
                "change_7d_pct": None,  # not provided by /search/trending
                "market_cap_usd": _safe_float(data.get("market_cap")),
            }
        )

    return {
        "trending_coins": trending_coins,
        "note": "Most searched on CoinGecko in last 24h. Trending coins often move 24-48h after appearing here.",
        "source": "coingecko",
    }


# ---------------------------------------------------------------------------
# screen_cryptos
# ---------------------------------------------------------------------------


def _passes_market_cap_bounds(market_cap: float | None, min_b: float, max_b: float) -> bool:
    if market_cap is None:
        return False
    if min_b > 0 and market_cap < min_b * _BILLION:
        return False
    if max_b > 0 and market_cap > max_b * _BILLION:
        return False
    return True


async def screen_cryptos(
    category: str = "",
    min_market_cap_b: float = 0,
    max_market_cap_b: float = 0,
    sort_by: str = "market_cap",
    limit: int = 20,
) -> dict:
    """Filter and rank coins by market-cap band, optional category, and metric.

    No saved reference payload exists for this endpoint (it is a screener,
    not one of the FR digest sources), so the response envelope
    (``cryptos`` / ``count`` / ``filters``) is this module's own design
    choice, mirroring the list-plus-metadata convention used elsewhere in
    this codebase family's screeners. Each entry reuses the exact
    ``_normalize_market_item`` shape as ``get_crypto_batch`` so a screener
    result and a batch quote are always interchangeable.
    """
    async with httpx.AsyncClient() as client:
        raw = await _fetch_markets(client, category=category or None, per_page=SCREEN_CANDIDATE_POOL_SIZE)

    error = _as_error(raw) if isinstance(raw, dict) else None
    if error:
        return error
    if not isinstance(raw, list):
        return {"cryptos": [], "count": 0, "source": "coingecko"}

    normalized = [_normalize_market_item(item) for item in raw]
    filtered = [
        item for item in normalized if _passes_market_cap_bounds(item["market_cap"], min_market_cap_b, max_market_cap_b)
    ]

    sort_key = _SORT_KEY_MAP.get(sort_by, "market_cap")
    filtered.sort(key=lambda item: item.get(sort_key) if item.get(sort_key) is not None else float("-inf"), reverse=True)

    limited = filtered[: max(limit, 0)]

    return {
        "cryptos": limited,
        "count": len(limited),
        "filters": {
            "category": category or None,
            "min_market_cap_b": min_market_cap_b,
            "max_market_cap_b": max_market_cap_b,
            "sort_by": sort_key,
        },
        "source": "coingecko",
    }

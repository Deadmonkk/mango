"""DefiLlama provider — DeFi TVL and stablecoin supply overviews, free and unauthenticated."""

import asyncio

import httpx
from terminalq.logging_config import log
from terminalq.rate_limiter import RateLimiter

from terminalq import cache
from terminalq.ext_settings import (
    CACHE_TTL_DEFI,
    CACHE_TTL_STABLECOINS,
    DEFILLAMA_RATE_LIMIT,
    STABLECOIN_GROWTH_SIGNAL_PCT,
    TOP_STABLECOINS_LIMIT,
)

BASE_URL = "https://api.llama.fi"
STABLECOINS_BASE_URL = "https://stablecoins.llama.fi"
_rate_limiter = RateLimiter(calls_per_minute=DEFILLAMA_RATE_LIMIT)

TOP_CHAINS_LIMIT = 10


async def _fetch(client: httpx.AsyncClient, url: str) -> dict | list:
    """Rate-limited HTTP GET with error handling."""
    await _rate_limiter.acquire()
    log.debug("DefiLlama request: %s", url)
    try:
        resp = await client.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except httpx.TimeoutException:
        log.warning("DefiLlama timeout: %s", url)
        return {"_error": "Request timed out"}
    except httpx.HTTPStatusError as e:
        log.warning("DefiLlama HTTP %d: %s", e.response.status_code, url)
        return {"_error": f"HTTP {e.response.status_code}"}
    except httpx.ConnectError:
        log.error("DefiLlama connection failed: %s", url)
        return {"_error": "Connection failed"}


def _pct_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return (current - previous) / previous * 100


async def get_defi_overview() -> dict:
    """Get total DeFi TVL, top chains by TVL, and TVL trend (capital flow signal).

    Returns:
        Dict with total TVL, 1d/7d/30d % change, top chains by TVL share,
        and a trend signal interpretation.
    """
    cache_key = "defillama_overview"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    async with httpx.AsyncClient() as client:
        chains_data, history_data = await asyncio.gather(
            _fetch(client, f"{BASE_URL}/v2/chains"),
            _fetch(client, f"{BASE_URL}/v2/historicalChainTvl"),
        )

    if isinstance(chains_data, dict) and "_error" in chains_data:
        return {"error": chains_data["_error"], "source": "defillama"}
    if isinstance(history_data, dict) and "_error" in history_data:
        return {"error": history_data["_error"], "source": "defillama"}

    total_tvl = sum(chain.get("tvl", 0) for chain in chains_data)

    top_chains = sorted(chains_data, key=lambda c: c.get("tvl", 0), reverse=True)[:TOP_CHAINS_LIMIT]
    top_chains_out = [
        {
            "name": chain.get("name"),
            "tvl_usd": chain.get("tvl"),
            "pct_share": round(chain.get("tvl", 0) / total_tvl * 100, 2) if total_tvl else None,
        }
        for chain in top_chains
    ]

    history_data = sorted(history_data, key=lambda d: d["date"])
    latest_tvl = history_data[-1]["tvl"] if history_data else None

    change_1d = change_7d = change_30d = None
    if len(history_data) >= 2:
        change_1d = _pct_change(history_data[-1]["tvl"], history_data[-2]["tvl"])
    if len(history_data) >= 8:
        change_7d = _pct_change(history_data[-1]["tvl"], history_data[-8]["tvl"])
    if len(history_data) >= 31:
        change_30d = _pct_change(history_data[-1]["tvl"], history_data[-31]["tvl"])

    trend_signal = "insufficient data"
    if change_7d is not None:
        if change_7d > 5:
            trend_signal = "TVL rising — capital flowing into DeFi, often coincides with alt season"
        elif change_7d < -5:
            trend_signal = "TVL falling — capital leaving DeFi, often coincides with risk-off conditions"
        else:
            trend_signal = "TVL roughly flat — no strong capital flow signal"

    result = {
        "total_tvl_usd": latest_tvl,
        "tvl_change_1d_pct": round(change_1d, 4) if change_1d is not None else None,
        "tvl_change_7d_pct": round(change_7d, 4) if change_7d is not None else None,
        "tvl_change_30d_pct": round(change_30d, 4) if change_30d is not None else None,
        "top_chains": top_chains_out,
        "trend_signal": trend_signal,
        "source": "defillama",
    }
    cache.set(cache_key, result, CACHE_TTL_DEFI)
    return result


def _history_supply(entry: dict) -> float | None:
    """Extract total pegged-USD supply from a stablecoincharts history entry."""
    for key in ("totalCirculatingUSD", "totalCirculating"):
        value = entry.get(key)
        if isinstance(value, dict) and value.get("peggedUSD") is not None:
            return float(value["peggedUSD"])
    return None


async def get_stablecoins_overview() -> dict:
    """Get total stablecoin supply, top stablecoins by share, and supply growth trend.

    Stablecoin supply is crypto's 'dry powder' — growth means new money is
    entering the ecosystem's waiting room; contraction means capital is
    leaving crypto entirely.
    """
    cache_key = "defillama_stablecoins"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    async with httpx.AsyncClient() as client:
        assets_data, history_data = await asyncio.gather(
            _fetch(client, f"{STABLECOINS_BASE_URL}/stablecoins?includePrices=false"),
            _fetch(client, f"{STABLECOINS_BASE_URL}/stablecoincharts/all"),
        )

    if isinstance(assets_data, dict) and "_error" in assets_data:
        return {"error": assets_data["_error"], "source": "defillama"}
    if isinstance(history_data, dict) and "_error" in history_data:
        return {"error": history_data["_error"], "source": "defillama"}

    pegged = assets_data.get("peggedAssets", []) if isinstance(assets_data, dict) else []
    assets = []
    for asset in pegged:
        supply = (asset.get("circulating") or {}).get("peggedUSD")
        if supply:
            assets.append({"name": asset.get("name"), "symbol": asset.get("symbol"), "supply_usd": supply})
    assets_total = sum(a["supply_usd"] for a in assets)

    top_stablecoins = sorted(assets, key=lambda a: a["supply_usd"], reverse=True)[:TOP_STABLECOINS_LIMIT]
    for asset in top_stablecoins:
        asset["pct_share"] = round(asset["supply_usd"] / assets_total * 100, 2) if assets_total else None

    history = (
        sorted(
            (e for e in history_data if _history_supply(e) is not None),
            key=lambda e: int(e["date"]),
        )
        if isinstance(history_data, list)
        else []
    )
    supplies = [_history_supply(e) for e in history]
    latest_supply = supplies[-1] if supplies else assets_total

    change_7d = _pct_change(supplies[-1], supplies[-8]) if len(supplies) >= 8 else None
    change_30d = _pct_change(supplies[-1], supplies[-31]) if len(supplies) >= 31 else None

    trend_signal = "insufficient data"
    if change_30d is not None:
        if change_30d > STABLECOIN_GROWTH_SIGNAL_PCT:
            trend_signal = "supply expanding — new money entering crypto's waiting room (bullish dry powder)"
        elif change_30d < -STABLECOIN_GROWTH_SIGNAL_PCT:
            trend_signal = "supply contracting — capital leaving the crypto ecosystem entirely (bearish)"
        else:
            trend_signal = "supply roughly flat — no strong capital flow signal"

    result = {
        "total_supply_usd": latest_supply,
        "supply_change_7d_pct": round(change_7d, 4) if change_7d is not None else None,
        "supply_change_30d_pct": round(change_30d, 4) if change_30d is not None else None,
        "top_stablecoins": top_stablecoins,
        "trend_signal": trend_signal,
        "note": "Total circulating supply of USD-pegged stablecoins across all chains. Growth = dry powder building; contraction = exit from crypto.",
        "source": "defillama",
    }
    cache.set(cache_key, result, CACHE_TTL_STABLECOINS)
    return result

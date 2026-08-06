"""CFTC Commitment of Traders provider — futures positioning, free and unauthenticated."""

import httpx
from terminalq.mango.logging import log

from terminalq.mango import cache
from terminalq.ext_settings import CACHE_TTL_COT, COT_LARGE_SPEC_EXTREME_RATIO

BASE_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"

# Friendly name -> exact market_and_exchange_names value (Legacy Futures Only dataset)
_MARKET_MAP = {
    "btc": "BITCOIN - CHICAGO MERCANTILE EXCHANGE",
    "sp500": "E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE",
    "nasdaq": "NASDAQ-100 STOCK INDEX (MINI) - CHICAGO MERCANTILE EXCHANGE",
    "10y_note": "10-YEAR U.S. TREASURY NOTES - CHICAGO BOARD OF TRADE",
    "eurusd": "EURO FX - CHICAGO MERCANTILE EXCHANGE",
    "gold": "GOLD - COMMODITY EXCHANGE INC.",
    "wti": "CRUDE OIL, LIGHT SWEET - NEW YORK MERCANTILE EXCHANGE",
}


def _net(record: dict, long_field: str, short_field: str) -> tuple[int, int, int]:
    long_pos = int(record[long_field])
    short_pos = int(record[short_field])
    return long_pos, short_pos, long_pos - short_pos


async def get_cot_report(market: str) -> dict:
    """Get CFTC Commitment of Traders positioning for a market.

    Args:
        market: One of btc, sp500, nasdaq, 10y_note, eurusd, gold, wti.

    Returns:
        Dict with open interest and net positioning (long - short) for
        commercials, large speculators, and small speculators, including
        week-over-week change and a crowding signal.
    """
    market_key = market.lower()
    market_name = _MARKET_MAP.get(market_key)
    if market_name is None:
        return {
            "error": f"Unknown market '{market}'. Valid options: {', '.join(sorted(_MARKET_MAP))}",
            "source": "cftc",
        }

    cache_key = f"cftc_cot_{market_key}"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    params = {
        "$where": f"market_and_exchange_names='{market_name}'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": "2",
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(BASE_URL, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        log.warning("CFTC timeout for market %s", market_name)
        return {"error": "Request timed out", "source": "cftc"}
    except httpx.HTTPStatusError as e:
        log.warning("CFTC HTTP %d for market %s", e.response.status_code, market_name)
        return {"error": f"HTTP {e.response.status_code}", "source": "cftc"}
    except httpx.ConnectError:
        log.error("CFTC connection failed for market %s", market_name)
        return {"error": "Connection failed", "source": "cftc"}

    if not data:
        return {"error": f"No COT data found for {market_name}", "source": "cftc"}

    latest = data[0]
    prior = data[1] if len(data) > 1 else None

    open_interest = int(latest["open_interest_all"])
    comm_long, comm_short, comm_net = _net(latest, "comm_positions_long_all", "comm_positions_short_all")
    large_long, large_short, large_net = _net(latest, "noncomm_positions_long_all", "noncomm_positions_short_all")
    small_long, small_short, small_net = _net(latest, "nonrept_positions_long_all", "nonrept_positions_short_all")

    comm_net_change = large_net_change = small_net_change = None
    if prior is not None:
        _, _, prior_comm_net = _net(prior, "comm_positions_long_all", "comm_positions_short_all")
        _, _, prior_large_net = _net(prior, "noncomm_positions_long_all", "noncomm_positions_short_all")
        _, _, prior_small_net = _net(prior, "nonrept_positions_long_all", "nonrept_positions_short_all")
        comm_net_change = comm_net - prior_comm_net
        large_net_change = large_net - prior_large_net
        small_net_change = small_net - prior_small_net

    large_spec_pct_of_oi = round(large_net / open_interest * 100, 2) if open_interest else None

    signal = "neutral — large speculator positioning within normal range"
    if large_spec_pct_of_oi is not None:
        threshold_pct = COT_LARGE_SPEC_EXTREME_RATIO * 100
        if large_spec_pct_of_oi > threshold_pct:
            signal = f"crowded long — large speculators net long {large_spec_pct_of_oi}% of open interest"
        elif large_spec_pct_of_oi < -threshold_pct:
            signal = f"crowded short — large speculators net short {abs(large_spec_pct_of_oi)}% of open interest"

    result = {
        "market": market_key,
        "market_name": market_name,
        "report_date": latest["report_date_as_yyyy_mm_dd"][:10],
        "open_interest": open_interest,
        "commercial": {"long": comm_long, "short": comm_short, "net": comm_net, "net_change": comm_net_change},
        "large_speculators": {
            "long": large_long,
            "short": large_short,
            "net": large_net,
            "net_change": large_net_change,
        },
        "small_speculators": {
            "long": small_long,
            "short": small_short,
            "net": small_net,
            "net_change": small_net_change,
        },
        "large_spec_pct_of_oi": large_spec_pct_of_oi,
        "signal": signal,
        "note": "Net = long - short. Commercials are typically 'smart money' hedgers; large speculators are funds/CTAs; small speculators are retail.",
        "source": "cftc",
    }
    cache.set(cache_key, result, CACHE_TTL_COT)
    return result

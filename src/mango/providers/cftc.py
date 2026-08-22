"""CFTC Commitment of Traders provider — futures positioning, free and unauthenticated.

Positioning normalization: a raw net-contracts figure (or even net as a % of
open interest) means little on its own — what matters is how crowded that
positioning is *relative to the market's own history*. Every report ranks
today's large-speculator and commercial skew against ~5 years of weekly
reports pulled in the same call, the same way get_metric_context ranks a
FRED series against its own history.
"""

import httpx

from mango.analytics.percentiles import percentile_rank
from mango.core import http
from mango.core.logging import log

from mango.core import cache
from mango.ext_settings import CACHE_TTL_COT, COT_HISTORY_LIMIT, COT_LARGE_SPEC_EXTREME_RATIO

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

# Markets where listed futures are a small fraction of total activity, so the
# "commercial = smart money" premise the whole report leans on doesn't hold.
# Spot/forward/swap FX trades OTC in volume that dwarfs CME futures — this is
# a dataset-representativeness problem, not something a threshold/lookback
# tweak can fix.
_STRUCTURALLY_UNRELIABLE = {
    "eurusd": (
        "COT positioning is structurally unreliable for FX: listed currency futures are a "
        "small fraction of total EUR/USD activity, which trades OTC (spot/forward/swap) in "
        "volume that dwarfs CME futures. The commercial-net signal below reflects only the "
        "futures-market slice, not the market that actually sets price — read it as low-confidence "
        "context, not a positioning edge."
    ),
}

_TIMING_CAVEAT = (
    "Commercial positioning tends to reach bullish extremes ~2 weeks before price bottoms and "
    "bearish extremes ~2 weeks before price tops, but this report itself lags ~3 weeks behind "
    "real-time positioning. Hedgers build/unwind extremes over multiple weeks, so the signal stays "
    "valid past the reporting lag — treat this as a slow, multi-week gauge, never a timing trigger."
)


def _net(record: dict, long_field: str, short_field: str) -> tuple[int, int, int]:
    long_pos = int(record[long_field])
    short_pos = int(record[short_field])
    return long_pos, short_pos, long_pos - short_pos


def _pct_of_oi(record: dict, long_field: str, short_field: str) -> float | None:
    """Net position (long - short) for one group, as a % of that report's open interest."""
    oi = int(record.get("open_interest_all", 0))
    if not oi:
        return None
    _, _, net = _net(record, long_field, short_field)
    return net / oi * 100


async def get_cot_report(market: str) -> dict:
    """Get CFTC Commitment of Traders positioning for a market.

    Args:
        market: One of btc, sp500, nasdaq, 10y_note, eurusd, gold, wti.

    Returns:
        Dict with open interest and net positioning (long - short) for
        commercials, large speculators, and small speculators, including
        week-over-week change, a fixed-threshold crowding signal, each
        group's net-of-OI percentile vs ~5 years of that market's own history,
        a timing_caveat (report lag vs commercial lead time), and a
        reliability_caveat (non-null for markets like FX where listed futures
        don't represent the real market).
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
        "$limit": str(COT_HISTORY_LIMIT),
    }

    try:
        data = await http.fetch_json(BASE_URL, params=params, timeout=10)
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
    commercial_pct_of_oi = round(comm_net / open_interest * 100, 2) if open_interest else None

    signal = "neutral — large speculator positioning within normal range"
    if large_spec_pct_of_oi is not None:
        threshold_pct = COT_LARGE_SPEC_EXTREME_RATIO * 100
        if large_spec_pct_of_oi > threshold_pct:
            signal = f"crowded long — large speculators net long {large_spec_pct_of_oi}% of open interest"
        elif large_spec_pct_of_oi < -threshold_pct:
            signal = f"crowded short — large speculators net short {abs(large_spec_pct_of_oi)}% of open interest"

    # Normalize against the market's own history: a raw net-of-OI figure is close
    # to meaningless on its own — the signal is where it ranks vs its own past.
    large_history = [v for v in (_pct_of_oi(r, "noncomm_positions_long_all", "noncomm_positions_short_all") for r in data) if v is not None]
    comm_history = [v for v in (_pct_of_oi(r, "comm_positions_long_all", "comm_positions_short_all") for r in data) if v is not None]
    large_spec_percentile = percentile_rank(large_history, large_spec_pct_of_oi) if large_spec_pct_of_oi is not None else None
    commercial_percentile = percentile_rank(comm_history, commercial_pct_of_oi) if commercial_pct_of_oi is not None else None
    history_observations = len(data)
    history_start_date = data[-1]["report_date_as_yyyy_mm_dd"][:10] if data else None

    percentile_signal = None
    if large_spec_percentile is not None:
        if large_spec_percentile >= 90:
            percentile_signal = (
                f"large speculators at the {large_spec_percentile}th percentile of net-long positioning "
                f"over {history_observations} weeks since {history_start_date} — historically crowded long"
            )
        elif large_spec_percentile <= 10:
            percentile_signal = (
                f"large speculators at the {large_spec_percentile}th percentile of net-long positioning "
                f"over {history_observations} weeks since {history_start_date} — historically crowded short"
            )
        else:
            percentile_signal = (
                f"large speculators at the {large_spec_percentile}th percentile vs "
                f"{history_observations} weeks of history — unremarkable"
            )

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
        "commercial_pct_of_oi": commercial_pct_of_oi,
        "large_spec_pct_of_oi_percentile": large_spec_percentile,
        "commercial_pct_of_oi_percentile": commercial_percentile,
        "history_observations": history_observations,
        "history_start_date": history_start_date,
        "signal": signal,
        "percentile_signal": percentile_signal,
        "timing_caveat": _TIMING_CAVEAT,
        "reliability_caveat": _STRUCTURALLY_UNRELIABLE.get(market_key),
        "note": (
            "Net = long - short. Commercials are typically 'smart money' hedgers; large "
            "speculators are funds/CTAs; small speculators are retail. 'signal' uses a fixed "
            f"±{COT_LARGE_SPEC_EXTREME_RATIO * 100:.0f}%-of-OI threshold; 'percentile_signal' ranks "
            "today's positioning against the market's own history instead — the raw net level "
            "means little without that context."
        ),
        "source": "cftc",
    }
    cache.set(cache_key, result, CACHE_TTL_COT)
    return result

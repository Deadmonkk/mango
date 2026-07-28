"""Sector rotation — the 11 SPDR sector ETFs vs SPY (Yahoo Finance, free, no key).

Sector leadership confirms or contradicts the macro regime: defensives
(staples, utilities, health care) leading = risk-off under the surface
even when the index looks calm; cyclicals leading = risk-on confirmed.
"""

import asyncio
import statistics

from terminalq.config import CACHE_TTL_SECTORS
from terminalq.logging_config import log

from terminalq import cache
from terminalq._lazy_yfinance import yfinance

SECTOR_ETFS = {
    "XLK": "Technology",
    "XLY": "Consumer Discretionary",
    "XLC": "Communication Services",
    "XLF": "Financials",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLE": "Energy",
    "XLV": "Health Care",
    "XLP": "Consumer Staples",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
}

# Classic economically-sensitive vs recession-resistant groups. XLK/XLC are
# treated as growth (neither bucket); XLRE is rate-sensitive (neither bucket).
CYCLICALS = ("XLY", "XLF", "XLI", "XLB", "XLE")
DEFENSIVES = ("XLP", "XLU", "XLV")

_BENCHMARK = "SPY"
_HORIZONS = {"1mo": 21, "3mo": 63, "6mo": 126}  # trading days
_FETCH_PERIOD = "7mo"  # enough daily bars to cover the longest horizon
_TOP_N = 3


async def _fetch_returns(symbol: str) -> dict | None:
    """Total return % over each horizon for one symbol, or None if unavailable."""

    def _closes() -> list[float]:
        history = yfinance.Ticker(symbol).history(period=_FETCH_PERIOD, auto_adjust=True)
        return [] if history.empty else [float(c) for c in history["Close"]]

    try:
        closes = await asyncio.to_thread(_closes)
    except Exception as e:  # yfinance raises a grab-bag of exception types
        log.warning("Sectors: Yahoo fetch failed for %s: %s", symbol, e)
        return None

    longest = max(_HORIZONS.values())
    if len(closes) < longest + 1:
        log.warning("Sectors: insufficient history for %s (%d bars)", symbol, len(closes))
        return None

    last = closes[-1]
    return {label: round((last / closes[-(days + 1)] - 1) * 100, 2) for label, days in _HORIZONS.items()}


def _relative_row(etf: str, returns: dict, benchmark: dict) -> dict:
    return {
        "etf": etf,
        "sector": SECTOR_ETFS[etf],
        "return_1mo_pct": returns["1mo"],
        "return_3mo_pct": returns["3mo"],
        "return_6mo_pct": returns["6mo"],
        "relative_1mo_pct": round(returns["1mo"] - benchmark["1mo"], 2),
        "relative_3mo_pct": round(returns["3mo"] - benchmark["3mo"], 2),
        "relative_6mo_pct": round(returns["6mo"] - benchmark["6mo"], 2),
    }


def _cyclical_defensive_spread(rows: list[dict]) -> float | None:
    """Average 3-month relative return of cyclicals minus defensives, in pp."""
    cyclical = [r["relative_3mo_pct"] for r in rows if r["etf"] in CYCLICALS]
    defensive = [r["relative_3mo_pct"] for r in rows if r["etf"] in DEFENSIVES]
    if not cyclical or not defensive:
        return None
    return round(statistics.mean(cyclical) - statistics.mean(defensive), 2)


def _rotation_signal(spread: float | None) -> str:
    if spread is None:
        return "cyclical vs defensive read unavailable — too many sectors missing"
    if spread > 0:
        return f"cyclicals beating defensives by {spread}pp over 3 months — risk-on leadership under the surface"
    if spread < 0:
        return f"defensives beating cyclicals by {abs(spread)}pp over 3 months — risk-off rotation under the surface"
    return "cyclicals and defensives even — no clear rotation signal"


async def get_sector_rotation() -> dict:
    """Get sector rotation: each sector ETF vs SPY over 1/3/6 months.

    Returns:
        Dict with per-sector absolute and relative returns (ranked by
        3-month relative strength), leaders/laggards, the cyclical-vs-
        defensive spread, and a rotation signal — or an error dict.
    """
    cache_key = "sector_rotation"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    benchmark, *sector_returns = await asyncio.gather(
        _fetch_returns(_BENCHMARK),
        *[_fetch_returns(etf) for etf in SECTOR_ETFS],
    )
    if benchmark is None:
        return {
            "error": f"Could not fetch benchmark {_BENCHMARK} from Yahoo Finance",
            "source": "yahoo_finance",
        }

    rows = [
        _relative_row(etf, returns, benchmark)
        for etf, returns in zip(SECTOR_ETFS, sector_returns)
        if returns is not None
    ]
    if not rows:
        return {"error": "No sector ETF data available from Yahoo Finance", "source": "yahoo_finance"}

    ranked = sorted(rows, key=lambda r: r["relative_3mo_pct"], reverse=True)
    spread = _cyclical_defensive_spread(rows)

    result = {
        "benchmark": {"symbol": _BENCHMARK, **{f"return_{k}_pct": v for k, v in benchmark.items()}},
        "sectors": ranked,
        "leaders_3mo": ranked[:_TOP_N],
        "laggards_3mo": ranked[-_TOP_N:],
        "cyclical_vs_defensive_3mo_pct": spread,
        "signal": _rotation_signal(spread),
        "note": (
            "Relative = sector return minus SPY over the same window (percentage points). "
            "Defensive leadership (staples/utilities/health care) is a classic risk-off tell "
            "even when the index is flat; cyclical leadership confirms risk-on."
        ),
        "source": "yahoo_finance",
    }
    cache.set(cache_key, result, CACHE_TTL_SECTORS)
    return result

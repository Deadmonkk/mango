"""Business-cycle & recession dashboard — where are we in the cycle?

Combines six free FRED-sourced recession signals into one rules-based
verdict: Sahm rule, both yield-curve spreads, jobless-claims trend,
Chicago Fed financial conditions (NFCI), and the Atlanta Fed GDPNow
nowcast. Fills the 'cycle position' layer of a top-down framework.
"""

import asyncio
import statistics

from terminalq.logging_config import log

from terminalq import cache
from terminalq.ext_settings import (
    CACHE_TTL_CYCLE,
    CLAIMS_DETERIORATION_PCT,
    CLAIMS_LOOKBACK_WEEKS,
    SAHM_TRIGGER_PP,
)
from terminalq.providers import fred

_CLAIMS_AVG_WEEKS = 4  # standard 4-week moving average for jobless claims
_CLAIMS_PRIOR_OFFSET = 13  # compare against the 4-week average ~3 months earlier


async def _latest_values(series_id: str, limit: int) -> list[float] | None:
    """Newest-first values for a FRED series, or None if unavailable."""
    result = await fred.get_series(series_id, limit=limit)
    if "error" in result:
        log.warning("Cycle: FRED series %s unavailable: %s", series_id, result["error"])
        return None
    values = [obs["value"] for obs in result.get("observations", [])]
    return values or None


def _claims_trend_pct(values: list[float] | None) -> float | None:
    """Percent change of the 4-week claims average vs ~3 months earlier."""
    needed = _CLAIMS_PRIOR_OFFSET + _CLAIMS_AVG_WEEKS
    if not values or len(values) < needed:
        return None
    recent = statistics.mean(values[:_CLAIMS_AVG_WEEKS])
    prior = statistics.mean(values[_CLAIMS_PRIOR_OFFSET:needed])
    if prior == 0:
        return None
    return round((recent / prior - 1) * 100, 1)


def _signal(name: str, value: float | None, triggered: bool | None, meaning: str) -> dict:
    return {"name": name, "value": value, "triggered": triggered, "meaning": meaning}


def _build_signals(
    sahm: list[float] | None,
    t10y2y: list[float] | None,
    t10y3m: list[float] | None,
    claims: list[float] | None,
    nfci: list[float] | None,
    gdpnow: list[float] | None,
) -> list[dict]:
    """Evaluate each recession signal; value=None + triggered=None when data failed."""
    unavailable = "data unavailable (source failed)"
    signals = []

    if sahm:
        value = round(sahm[0], 2)
        fired = value >= SAHM_TRIGGER_PP
        meaning = (
            "unemployment has risen enough off its low to historically mark a recession start"
            if fired
            else "no recessionary rise in unemployment"
        )
        signals.append(_signal("sahm_rule", value, fired, meaning))
    else:
        signals.append(_signal("sahm_rule", None, None, unavailable))

    for name, values in (("yield_curve_10y2y", t10y2y), ("yield_curve_10y3m", t10y3m)):
        if values:
            value = round(values[0], 2)
            fired = value < 0
            meaning = (
                "inverted — bond market pricing a slowdown and future rate cuts"
                if fired
                else "positively sloped — no inversion warning"
            )
            signals.append(_signal(name, value, fired, meaning))
        else:
            signals.append(_signal(name, None, None, unavailable))

    trend = _claims_trend_pct(claims)
    if trend is not None:
        fired = trend >= CLAIMS_DETERIORATION_PCT
        meaning = (
            "jobless claims rising fast — the labor market is cracking"
            if fired
            else "jobless claims stable — layoffs contained"
        )
        signals.append(_signal("claims_trend", trend, fired, meaning))
    else:
        signals.append(_signal("claims_trend", None, None, unavailable))

    if nfci:
        value = round(nfci[0], 2)
        fired = value > 0
        meaning = (
            "financial conditions tighter than average — credit headwind"
            if fired
            else "financial conditions looser than average — supportive"
        )
        signals.append(_signal("financial_conditions", value, fired, meaning))
    else:
        signals.append(_signal("financial_conditions", None, None, unavailable))

    if gdpnow:
        value = round(gdpnow[0], 2)
        fired = value < 0
        meaning = (
            "Atlanta Fed GDPNow shows the current quarter contracting"
            if fired
            else "Atlanta Fed GDPNow shows positive current-quarter growth"
        )
        signals.append(_signal("gdp_nowcast", value, fired, meaning))
    else:
        signals.append(_signal("gdp_nowcast", None, None, unavailable))

    return signals


def _verdict(active: int, available: int) -> str:
    if active == 0:
        return f"expansion — no recession signals active ({available} checked)"
    if active <= 2:
        return f"late-cycle caution — {active} of {available} recession signals active"
    if active <= 4:
        return f"recession risk ELEVATED — {active} of {available} recession signals active"
    return f"recession signals FLASHING — {active} of {available} active"


async def get_cycle_position() -> dict:
    """Get the business-cycle dashboard: six recession signals + one verdict.

    Returns:
        Dict with per-signal detail, active/available counts, and a
        rules-based verdict — or an error dict if all sources failed.
    """
    cache_key = "cycle_position"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    sahm, t10y2y, t10y3m, claims, nfci, gdpnow = await asyncio.gather(
        _latest_values("SAHMREALTIME", 1),
        _latest_values("T10Y2Y", 1),
        _latest_values("T10Y3M", 1),
        _latest_values("ICSA", CLAIMS_LOOKBACK_WEEKS),
        _latest_values("NFCI", 1),
        _latest_values("GDPNOW", 1),
    )

    signals = _build_signals(sahm, t10y2y, t10y3m, claims, nfci, gdpnow)
    evaluated = [s for s in signals if s["triggered"] is not None]
    if not evaluated:
        return {
            "error": "All cycle data sources failed (FRED unreachable or FRED_API_KEY missing)",
            "source": "fred",
        }

    active = sum(1 for s in evaluated if s["triggered"])
    result = {
        "signals": signals,
        "signals_active": active,
        "signals_available": len(evaluated),
        "verdict": _verdict(active, len(evaluated)),
        "note": (
            "Rules-based recession dashboard. Sahm rule >= 0.50 has marked every US recession "
            "since 1970 with no false positives. Yield-curve inversion typically leads recessions "
            "by 6-18 months. Claims trend compares the 4-week average vs ~3 months ago. "
            "NFCI > 0 = tighter-than-average financial conditions. GDPNow is the Atlanta Fed's "
            "live estimate of current-quarter GDP growth."
        ),
        "source": "fred",
    }
    cache.set(cache_key, result, CACHE_TTL_CYCLE)
    return result

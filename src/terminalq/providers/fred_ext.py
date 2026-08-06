"""FRED extensions — themed dashboards, full history, and percentile context.

Everything here is this pack's own work. It lives in its own module rather than
inside upstream's ``fred.py`` for two reasons: the pack must be publishable
without vendoring upstream files, and CI reconstructs a CLEAN upstream checkout,
so anything added to an upstream file simply does not exist when the suite runs.

Builds on upstream's ``fred.get_series`` for the actual HTTP calls; the aliases
these dashboards use are registered in ``SERIES_MAP`` below and merged into
upstream's map on import.
"""
from __future__ import annotations

import asyncio
import datetime as dt

import httpx

from terminalq.mango import cache
from terminalq.analytics import fred_archive
from terminalq.ext_settings import CACHE_TTL_ECONOMIC, FRED_API_KEY
from terminalq.mango.logging import log
from terminalq.providers.fred import BASE_URL, SERIES_MAP, _resolve_series_id, get_series

# Aliases this pack adds. Merged into upstream's SERIES_MAP on import so
# `get_series("hy_spread")` resolves without upstream having to know about them.
EXTRA_SERIES = {
    # --- CPI components ---
    "cpi_shelter": "CUSR0000SAH1",
    "cpi_energy": "CPIENGSL",
    "cpi_food": "CPIFABSL",
    "cpi_core_goods": "CUSR0000SACL1E",
    "cpi_services": "CUSR0000SASLE",
    # --- JOLTS (JTSLDL is layoffs & discharges; JTSLAL does not exist) ---
    "jolts_openings": "JTSJOL",
    "jolts_hires": "JTSHIL",
    "jolts_layoffs": "JTSLDL",
    "jolts_quits": "JTSQUL",
    "wage_growth": "AHETPI",
    # --- Credit spreads (ICE BofA, license-restricted to a rolling 3y window) ---
    "ig_spread": "BAMLC0A0CM",
    "hy_spread": "BAMLH0A0HYM2",
    "bb_spread": "BAMLH0A1HYBB",
    "b_spread": "BAMLH0A2HYB",
    "ccc_spread": "BAMLH0A3HYC",
    # --- Consumer health (DRCLACBS is CONSUMER loans; DRBLACBS is business) ---
    "debt_service_ratio": "TDSP",
    "cc_delinquency": "DRCCLACBS",
    "mortgage_delinquency": "DRSFRMACBS",
    "consumer_delinquency": "DRCLACBS",
    # --- Fiscal ---
    "federal_debt_gdp": "GFDEGDQ188S",
    "federal_deficit": "MTSDS133FMS",
    # --- Commodities (gold intentionally absent: FRED's LBMA series was
    #     discontinued, so it is sourced from Yahoo instead) ---
    "wti_oil": "DCOILWTICO",
    "gasoline_price": "GASREGCOVW",
    "dollar_index": "DTWEXBGS",
    # --- Liquidity ---
    "fed_balance_sheet": "WALCL",
    "reverse_repo": "RRPONTSYD",
    "treasury_general_account": "WTREGEN",
    # --- Rates ---
    "tips_10y": "DFII10",
    "tips_5y": "DFII5",
    "breakeven_10y": "T10YIE",
    "breakeven_5y": "T5YIE",
}

SERIES_MAP.update(EXTRA_SERIES)


# ORIGIN NOTE
#
#
# These eleven functions were lost when this file was reverted to upstream
# state while carrying uncommitted local work. They are rebuilt from the
# surviving specification: the test suite (tests/test_fred.py,
# test_metric_context.py, test_release_calendar.py) and the exact output
# shapes recorded in ~/Desktop/TerminalIQ Reports/.briefs/fr_raw_*.json.
# Behaviour is pinned by those tests; series IDs are pinned by the `title`
# strings in the saved payloads.
# ===========================================================================

# Dashboard groups: alias -> FRED series ID. Identified from the `title`
# recorded against each alias in the saved raw payloads.
_CPI_COMPONENT_SERIES = ["cpi", "core_cpi", "cpi_shelter", "cpi_energy", "cpi_food",
                         "cpi_core_goods", "cpi_services"]
_JOLTS_SERIES = ["jolts_openings", "jolts_hires", "jolts_layoffs", "jolts_quits", "wage_growth"]
_CREDIT_SPREAD_SERIES = ["ig_spread", "hy_spread", "bb_spread", "b_spread", "ccc_spread"]
_CONSUMER_HEALTH_SERIES = ["debt_service_ratio", "cc_delinquency", "mortgage_delinquency",
                           "consumer_delinquency"]
_FISCAL_SERIES = ["federal_debt_gdp", "federal_deficit"]
_COMMODITY_SERIES = ["wti_oil", "gasoline_price", "dollar_index"]
_LIQUIDITY_SERIES = ["fed_balance_sheet", "reverse_repo", "treasury_general_account"]
_RATES_SERIES = ["10y_yield", "2y_yield", "yield_spread", "tips_10y", "tips_5y",
                 "breakeven_10y", "breakeven_5y"]

HY_SPREAD_TIGHT = 3.5   # percent; below this the market is priced for calm
HY_SPREAD_STRESS = 6.0  # percent; above this credit is signalling stress


async def _indicator_group(aliases: list[str]) -> dict:
    """Fetch a group of FRED series and shape each as a dashboard indicator.

    Extends the plain latest/previous/change of `get_economic_dashboard` with
    title/units/frequency, which every themed dashboard carries.
    """
    results = await asyncio.gather(
        *[get_series(a, limit=2) for a in aliases], return_exceptions=True
    )
    indicators: dict[str, dict] = {}
    for alias, result in zip(aliases, results):
        if isinstance(result, BaseException) or not isinstance(result, dict) or "error" in result:
            err = str(result) if isinstance(result, BaseException) else result.get("error", "unknown error")
            indicators[alias] = {"error": err}
            continue
        obs = result.get("observations", [])
        latest = obs[0]["value"] if obs else None
        previous = obs[1]["value"] if len(obs) > 1 else None
        indicators[alias] = {
            "latest_value": latest,
            "latest_date": obs[0]["date"] if obs else None,
            "previous_value": previous,
            "change": round(latest - previous, 4) if latest is not None and previous is not None else None,
            "title": result.get("title", ""),
            "units": result.get("units", ""),
            "frequency": result.get("frequency", ""),
        }
    return indicators


def _no_key() -> dict:
    return {
        "error": "FRED_API_KEY not configured. Get a free key at "
                 "https://fred.stlouisfed.org/docs/api/api_key.html",
        "source": "fred",
    }


async def _themed_dashboard(cache_key: str, aliases: list[str], note: str) -> dict:
    if not FRED_API_KEY:
        return _no_key()
    cached = cache.get(cache_key)
    if cached:
        return cached
    indicators = await _indicator_group(aliases)
    result = {"indicators": indicators, "note": note, "source": "fred"}
    if any("latest_value" in v for v in indicators.values()):
        cache.set(cache_key, result, CACHE_TTL_ECONOMIC)
    return result


async def get_cpi_components_dashboard() -> dict:
    """CPI broken into shelter, energy, food, core goods and services."""
    return await _themed_dashboard(
        "fred_cpi_components", _CPI_COMPONENT_SERIES,
        "All CPI sub-indices on the same 1982-84=100 base. Compare monthly changes "
        "to gauge which components are accelerating.",
    )


async def get_jolts_dashboard() -> dict:
    """JOLTS labour-market flows plus production-worker wage growth."""
    if not FRED_API_KEY:
        return _no_key()
    cached = cache.get("fred_jolts")
    if cached:
        return cached
    result = {"indicators": await _indicator_group(_JOLTS_SERIES), "source": "fred"}
    if any("latest_value" in v for v in result["indicators"].values()):
        cache.set("fred_jolts", result, CACHE_TTL_ECONOMIC)
    return result


async def get_credit_spreads_dashboard() -> dict:
    """IG and high-yield option-adjusted spreads by rating tier."""
    result = await _themed_dashboard(
        "fred_credit_spreads", _CREDIT_SPREAD_SERIES,
        "Spreads in basis points (bps). Historical avg: IG ~145bps, HY ~500bps.",
    )
    hy = result.get("indicators", {}).get("hy_spread", {})
    value = hy.get("latest_value")
    if isinstance(value, (int, float)):
        if value < HY_SPREAD_TIGHT:
            hy["signal"] = "tight — markets complacent"
        elif value > HY_SPREAD_STRESS:
            hy["signal"] = "wide — credit stress building"
        else:
            hy["signal"] = "mid-range — no strong credit signal"
    return result


async def get_consumer_health_dashboard() -> dict:
    """Household debt service and delinquency rates."""
    return await _themed_dashboard(
        "fred_consumer_health", _CONSUMER_HEALTH_SERIES,
        "Debt service ratio: % of disposable income. Delinquency rates: % of loans "
        "30+ days past due. consumer_delinquency = all consumer loans at commercial banks.",
    )


async def get_fiscal_dashboard() -> dict:
    """Federal debt-to-GDP and the monthly budget balance."""
    return await _themed_dashboard(
        "fred_fiscal", _FISCAL_SERIES,
        "Deficit in $ millions (negative = deficit). Debt/GDP quarterly.",
    )


async def get_commodities_dashboard() -> dict:
    """WTI, gasoline and the dollar index from FRED; gold from Yahoo.

    FRED's LBMA gold series was discontinued, so gold is sourced separately and
    shaped like a FRED indicator by market_data.fetch_gold_dashboard_entry.
    """
    if not FRED_API_KEY:
        return _no_key()
    cached = cache.get("fred_commodities")
    if cached:
        return cached

    from terminalq.providers import market_data

    indicators, gold = await asyncio.gather(
        _indicator_group(_COMMODITY_SERIES),
        market_data.fetch_gold_dashboard_entry(),
        return_exceptions=True,
    )
    if isinstance(indicators, BaseException):
        return {"error": str(indicators), "source": "fred"}
    if not isinstance(gold, BaseException) and isinstance(gold, dict):
        indicators["gold_price"] = gold

    result = {
        "indicators": indicators,
        "note": "WTI in $/barrel. Gold in $/troy oz (Yahoo GC=F — FRED LBMA series "
                "discontinued). Gasoline in $/gallon (weekly). Dollar index: Jan 2006=100.",
        "source": "fred + yahoo_finance",
    }
    if any("latest_value" in v for v in indicators.values() if isinstance(v, dict)):
        cache.set("fred_commodities", result, CACHE_TTL_ECONOMIC)
    return result


async def get_liquidity_dashboard() -> dict:
    """Fed balance sheet, reverse repo and TGA, plus the net-liquidity proxy.

    WALCL and WTREGEN are reported in $ millions, RRPONTSYD in $ billions, so
    the first two are converted before the subtraction.
    """
    if not FRED_API_KEY:
        return _no_key()
    cached = cache.get("fred_liquidity")
    if cached:
        return cached

    indicators = await _indicator_group(_LIQUIDITY_SERIES)

    def bn(alias: str, divide: bool) -> float | None:
        v = indicators.get(alias, {}).get("latest_value")
        if not isinstance(v, (int, float)):
            return None
        return v / 1000 if divide else v

    walcl, rrp, tga = bn("fed_balance_sheet", True), bn("reverse_repo", False), bn("treasury_general_account", True)
    net = round(walcl - rrp - tga, 2) if None not in (walcl, rrp, tga) else None

    result = {
        "indicators": indicators,
        "net_liquidity_proxy_billions": net,
        "note": "Net liquidity proxy = Fed balance sheet (WALCL, converted to $bn) - reverse "
                "repo (RRP) - Treasury General Account (TGA). Rising net liquidity has "
                "historically supported risk assets.",
        "source": "fred",
    }
    if any("latest_value" in v for v in indicators.values()):
        cache.set("fred_liquidity", result, CACHE_TTL_ECONOMIC)
    return result


async def get_rates_dashboard() -> dict:
    """Nominal yields, TIPS real yields and breakeven inflation."""
    return await _themed_dashboard(
        "fred_rates", _RATES_SERIES,
        "Real yields (TIPS) = nominal yield minus expected inflation. Rising real yields "
        "are a headwind for gold, crypto, and long-duration growth equities. Rising "
        "breakevens mean the market expects more inflation.",
    )


async def get_series_history(series_id: str, start: str = "1900-01-01") -> dict:
    """Full observation history for a series, merged with the local archive.

    Vendor-licensed series can lose history without warning — ICE truncated
    every BAML* series to a rolling 3-year window in April 2026 — so live values
    are merged with the permanent local archive and the caller is told both the
    merged range (`start_date`) and what the live API alone would give today
    (`live_api_start_date`), so percentile claims can be honest about their base.
    """
    if not FRED_API_KEY:
        return _no_key()

    resolved = _resolve_series_id(series_id)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{BASE_URL}/series/observations",
                params={
                    "series_id": resolved,
                    "api_key": FRED_API_KEY,
                    "file_type": "json",
                    "observation_start": start,
                    "sort_order": "asc",
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:  # noqa: BLE001 — a failed history must not abort the report
        log.warning("FRED history fetch failed for %s: %s", resolved, e)
        return {"error": str(e), "series": resolved, "source": "fred"}

    dates: list[str] = []
    values: list[float] = []
    for obs in data.get("observations", []):
        raw = obs.get("value", ".")
        if raw == ".":
            continue
        try:
            values.append(float(raw))
            dates.append(obs["date"])
        except (ValueError, KeyError):
            continue

    live_start = dates[0] if dates else None
    merged_dates, merged_values = fred_archive.merge_and_persist(resolved, dates, values)
    archived = len(merged_values) > len(values) or (
        merged_dates and live_start and merged_dates[0] < live_start
    )

    return {
        "series": resolved,
        "start_date": merged_dates[0] if merged_dates else None,
        "live_api_start_date": live_start,
        "latest_date": merged_dates[-1] if merged_dates else None,
        "latest": merged_values[-1] if merged_values else None,
        "observations": len(merged_values),
        "values": merged_values,
        "dates": merged_dates,
        "source": "fred (live) + local archive" if archived else "fred",
    }


PERCENTILE_EXTREME_LOW = 10.0
PERCENTILE_EXTREME_HIGH = 90.0


def _percentile_interpretation(pct: float) -> str:
    if pct <= PERCENTILE_EXTREME_LOW:
        return "bottom decile vs history — extremely low"
    if pct >= PERCENTILE_EXTREME_HIGH:
        return "top decile vs history — extremely high"
    if pct >= 60:
        return "above its historical norm"
    if pct <= 40:
        return "below its historical norm"
    return "near its historical norm"


async def get_metric_context(indicator: str) -> dict:
    """Rank a metric's latest value against its own full history.

    A level means little without knowing where it sits historically — this is
    what turns "HY spread 2.78" into "4.9th percentile since 1996". Reports both
    the merged history range and the live-API range so a short window can never
    masquerade as a historical extreme.
    """
    history = await get_series_history(indicator)
    if "error" in history:
        return {"error": history["error"], "indicator": indicator, "source": "fred"}

    values = history.get("values") or []
    latest = history.get("latest")
    if not values or latest is None:
        return {"error": "no observations available", "indicator": indicator, "source": "fred"}

    at_or_below = sum(1 for v in values if v <= latest)
    pct = round(100 * at_or_below / len(values), 1)
    ordered = sorted(values)
    median = ordered[len(ordered) // 2]

    return {
        "indicator": indicator,
        "series": history.get("series"),
        "latest": latest,
        "latest_date": history.get("latest_date"),
        "percentile_since_start": pct,
        "history_start": history.get("start_date"),
        "live_api_start_date": history.get("live_api_start_date"),
        "observations": len(values),
        "min": min(values),
        "max": max(values),
        "median": median,
        "interpretation": _percentile_interpretation(pct),
        "note": "Percentile = share of all historical observations at or below the latest "
                "value. history_start is the merged (live + local archive) range; "
                "live_api_start_date is what FRED alone would serve today.",
        "source": "fred",
    }


# FRED release_id -> (display name, why it matters). Only high-impact releases;
# everything else on the calendar is noise for this purpose.
_HIGH_IMPACT_RELEASES = {
    10: ("CPI (inflation)", "the headline inflation print — drives the Fed path and real yields"),
    46: ("PPI (producer prices)", "pipeline inflation; feeds CPI with a lag"),
    50: ("Jobs Report (Employment Situation)", "payrolls and unemployment — the Fed's other mandate"),
    54: ("PCE (Fed's preferred inflation gauge)", "the inflation measure the Fed actually targets"),
    53: ("GDP", "headline growth; confirms or refutes the recession read"),
    192: ("JOLTS (job openings)", "labour-market tightness ahead of payrolls"),
    9: ("Retail Sales", "consumer spending — the largest component of GDP"),
}


async def get_release_calendar(days: int = 7) -> dict:
    """Upcoming high-impact US data releases from FRED's official schedule.

    Used as the free fallback when Finnhub's economic calendar is premium-walled.
    """
    if not FRED_API_KEY:
        return _no_key()

    today = dt.date.today()
    end = today + dt.timedelta(days=days)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{BASE_URL}/releases/dates",
                params={
                    "api_key": FRED_API_KEY,
                    "file_type": "json",
                    "realtime_start": today.isoformat(),
                    "realtime_end": end.isoformat(),
                    "include_release_dates_with_no_data": "true",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:  # noqa: BLE001
        log.warning("FRED release calendar failed: %s", e)
        return {"error": str(e), "source": "fred"}

    events = []
    for row in data.get("release_dates", []):
        rid, when = row.get("release_id"), row.get("date")
        if rid not in _HIGH_IMPACT_RELEASES or not when:
            continue
        if not (today.isoformat() <= when <= end.isoformat()):
            continue
        name, why = _HIGH_IMPACT_RELEASES[rid]
        events.append({"date": when, "event": name, "impact": "high", "why": why})

    events.sort(key=lambda e: e["date"])
    return {
        "events": events,
        "window_days": days,
        "note": "High-impact US releases from FRED's official schedule. Free fallback for "
                "the premium-walled Finnhub calendar.",
        "source": "fred",
    }

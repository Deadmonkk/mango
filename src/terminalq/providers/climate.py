"""Climate/weather production-risk monitor — NASA POWER API (free, no API key).

Tracks recent temperature and precipitation anomalies (vs each region's own
2001-2020 NASA POWER climatological normal — a 20-year period, confirmed
against the live API's own header metadata) across a fixed set of
commodity-production regions. This is NOT an ENSO/El Nino tracker — NASA does not publish the
Oceanic Nino Index (that's a NOAA CPC product). It IS a direct read on
realized weather conditions in the specific places that grow/produce the
commodities markets price, which is what actually moves futures, input
costs, and exposed equities regardless of which climate pattern (El Nino,
La Nina, an unrelated heat dome, etc.) is causing it.

COVERAGE (curated, not exhaustive): grains/oilseeds (corn, soybeans, wheat),
softs (palm oil, cocoa, coffee, sugar), a metals/battery-materials region
(copper + lithium, both mined in Chile's Atacama desert — water stress there
hits both), and one energy region (Permian Basin oil/gas). Each region's
`watch` field is tiered by where it sits in the value chain:
  - upstream: raw-material producers/miners/growers (public tickers only;
    many real-world growers and traders — e.g. Cargill, Codelco — are
    private/state-owned and so cannot appear here)
  - midstream: processors, refiners, or commodity traders
  - downstream: consumer-facing companies that buy the input as a cost
  - other_assets: adjacent asset classes (farmland REITs, country ETFs)
Every ticker below was checked against a live web search in 2026 rather than
pulled from training-data memory (training data is stale on M&A — e.g.
Pioneer Natural Resources no longer trades independently; ExxonMobil
absorbed it in 2024).

Data source: NASA POWER (power.larc.nasa.gov), NASA GMAO MERRA-2 reanalysis.
Two endpoints combined per region:
  - Daily point API: real observed T2M (2m temperature) and PRECTOTCORR
    (bias-corrected precipitation) for the trailing CLIMATE_LOOKBACK_DAYS.
  - Climatology point API: 2001-2020 monthly normals for the same point,
    used as the baseline the anomaly is measured against.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx
from terminalq.logging_config import log

from terminalq import cache
from terminalq.analytics import backtest_utils
from terminalq.ext_settings import (
    CACHE_TTL_CLIMATE,
    CACHE_TTL_STRESS_BACKTEST,
    CLIMATE_LOOKBACK_DAYS,
    CLIMATE_PRECIP_ANOMALY_WATCH_PCT,
    CLIMATE_PRECIP_MIN_NORMAL_MM,
    CLIMATE_TEMP_ANOMALY_WATCH_C,
)

BASE_URL = "https://power.larc.nasa.gov/api/temporal"

_MONTH_ABBR = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

# Fixed set of commodity/production regions worth watching. Not a
# recommendation — just a verified starting point for further research when
# a flag fires.
REGIONS: dict[str, dict] = {
    "us_corn_belt": {
        "label": "US Corn Belt (Iowa)",
        "lat": 42.0,
        "lon": -93.6,
        "watch": {
            "commodities": ["corn (ZC)", "soybeans (ZS)"],
            "upstream": ["DE (Deere — equipment)", "CTVA (Corteva — seed/crop inputs)"],
            "midstream": ["ADM (Archer-Daniels-Midland)", "BG (Bunge)"],
            "downstream": ["TSN (Tyson — livestock feed cost)"],
            "other_assets": ["FPI / LAND (farmland REITs)", "DBA (agriculture commodity ETF)"],
        },
    },
    "brazil_mato_grosso": {
        "label": "Mato Grosso, Brazil",
        "lat": -12.6,
        "lon": -56.1,
        "watch": {
            "commodities": ["soybeans (ZS)", "corn (ZC)"],
            "midstream": ["ADM (Archer-Daniels-Midland)", "BG (Bunge)"],
            "other_assets": ["EWZ (Brazil equity ETF)"],
        },
    },
    "argentina_pampas": {
        "label": "Pampas, Argentina",
        "lat": -34.6,
        "lon": -62.0,
        "watch": {
            "commodities": ["soybeans (ZS)", "wheat (ZW)", "corn (ZC)"],
            "midstream": ["BG (Bunge)", "AGRO (Adecoagro — grains/rice/sugar across S. America)"],
        },
    },
    "australia_wheat_belt": {
        "label": "Western Australia wheat belt",
        "lat": -31.9,
        "lon": 117.2,
        "watch": {
            "commodities": ["wheat (ZW)"],
            "midstream": ["GNC.AX (GrainCorp)"],
            "other_assets": ["EWA (Australia equity ETF)"],
        },
    },
    "indonesia_palm": {
        "label": "Sumatra, Indonesia",
        "lat": -0.6,
        "lon": 101.4,
        "watch": {
            "commodities": ["palm oil (FCPO)"],
            "midstream": ["WLMIY (Wilmar International)"],
        },
    },
    "ivory_coast_cocoa": {
        "label": "Ivory Coast cocoa belt",
        "lat": 6.8,
        "lon": -5.3,
        "watch": {
            "commodities": ["cocoa (CC)"],
            "downstream": ["HSY (Hershey)", "MDLZ (Mondelez)"],
        },
    },
    "vietnam_coffee": {
        "label": "Central Highlands, Vietnam",
        "lat": 12.7,
        "lon": 108.1,
        "watch": {
            "commodities": ["robusta coffee (RC)", "arabica coffee (KC)"],
            "downstream": ["NSRGY (Nestle)"],
        },
    },
    "brazil_sugar_belt": {
        "label": "São Paulo sugar/ethanol belt, Brazil",
        "lat": -21.2,
        "lon": -47.8,
        "watch": {
            "commodities": ["sugar (SB)", "ethanol"],
            "midstream": ["CZZ (Cosan)", "AGRO (Adecoagro)"],
            "other_assets": ["EWZ (Brazil equity ETF)"],
        },
    },
    "chile_atacama_copper_lithium": {
        "label": "Atacama Desert, Chile (copper + lithium)",
        "lat": -24.0,
        "lon": -69.0,
        "watch": {
            "commodities": ["copper (HG)", "lithium"],
            "upstream": [
                "BHP / RIO (co-own Escondida, world's largest copper mine)",
                "FCX (Freeport-McMoRan — world's largest publicly traded copper producer)",
                "SQM / ALB (SQM, Albemarle — both extract lithium from Salar de Atacama)",
            ],
            "other_assets": ["ECH (Chile equity ETF)"],
            "note": "Water scarcity is the shared climate risk here — both copper and lithium extraction are water-intensive in one of Earth's driest deserts.",
        },
    },
    "australia_pilbara_iron_ore": {
        "label": "Pilbara, Australia (iron ore)",
        "lat": -21.5,
        "lon": 119.0,
        "watch": {
            "commodities": ["iron ore"],
            "upstream": ["BHP (majority owner, Escondida + Pilbara)", "RIO (Rio Tinto)", "FMG.AX (Fortescue)"],
            "other_assets": ["EWA (Australia equity ETF)"],
        },
    },
    "us_permian_basin": {
        "label": "Permian Basin, US (oil + gas)",
        "lat": 31.9,
        "lon": -102.1,
        "watch": {
            "commodities": ["WTI crude oil (CL)", "natural gas (NG)"],
            "upstream": [
                "XOM (ExxonMobil — largest Permian producer after its 2024 Pioneer Natural Resources acquisition)",
                "CVX (Chevron)",
                "FANG (Diamondback Energy)",
            ],
        },
    },
    "us_gulf_coast": {
        "label": "US Gulf Coast (Louisiana)",
        "lat": 29.9,
        "lon": -90.1,
        "watch": {
            "commodities": ["natural gas (NG)"],
            "midstream": ["LNG (Cheniere Energy — LNG export terminals)"],
            "upstream": ["XOM (ExxonMobil)"],
        },
    },
}


def _fmt_date(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


async def _fetch_daily(client: httpx.AsyncClient, lat: float, lon: float, start: str, end: str) -> dict:
    resp = await client.get(
        f"{BASE_URL}/daily/point",
        params={
            "parameters": "T2M,PRECTOTCORR",
            "community": "AG",
            "longitude": lon,
            "latitude": lat,
            "start": start,
            "end": end,
            "format": "JSON",
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


async def _fetch_climatology(client: httpx.AsyncClient, lat: float, lon: float) -> dict:
    resp = await client.get(
        f"{BASE_URL}/climatology/point",
        params={
            "parameters": "T2M,PRECTOTCORR",
            "community": "AG",
            "longitude": lon,
            "latitude": lat,
            "format": "JSON",
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def _signal(temp_anomaly_c: float | None, precip_anomaly_pct: float | None) -> str:
    flags = []
    if temp_anomaly_c is not None and abs(temp_anomaly_c) >= CLIMATE_TEMP_ANOMALY_WATCH_C:
        direction = "hotter" if temp_anomaly_c > 0 else "cooler"
        flags.append(f"{direction} than normal by {abs(temp_anomaly_c):.1f}°C")
    if precip_anomaly_pct is not None and abs(precip_anomaly_pct) >= CLIMATE_PRECIP_ANOMALY_WATCH_PCT:
        direction = "wetter" if precip_anomaly_pct > 0 else "drier"
        flags.append(f"{direction} than normal by {abs(precip_anomaly_pct):.0f}%")
    if not flags:
        return "normal — within typical range for this time of year"
    return "FLAGGED — " + "; ".join(flags)


async def _region_reading(client: httpx.AsyncClient, key: str, region: dict, today: dt.date) -> dict:
    start = today - dt.timedelta(days=CLIMATE_LOOKBACK_DAYS)
    try:
        daily = await _fetch_daily(client, region["lat"], region["lon"], _fmt_date(start), _fmt_date(today))
        clim = await _fetch_climatology(client, region["lat"], region["lon"])
    except httpx.TimeoutException:
        log.warning("NASA POWER timeout for region %s", key)
        return {"label": region["label"], "error": "Request timed out", "source": "power.larc.nasa.gov"}
    except httpx.HTTPStatusError as e:
        log.warning("NASA POWER HTTP %d for region %s", e.response.status_code, key)
        return {"label": region["label"], "error": f"HTTP {e.response.status_code}", "source": "power.larc.nasa.gov"}
    except httpx.HTTPError as e:
        log.error("NASA POWER connection failed for region %s: %s", key, e)
        return {"label": region["label"], "error": "Connection failed", "source": "power.larc.nasa.gov"}

    try:
        temps = list(daily["properties"]["parameter"]["T2M"].values())
        precips = list(daily["properties"]["parameter"]["PRECTOTCORR"].values())
        temps = [t for t in temps if t not in (-999, -999.0)]
        precips = [p for p in precips if p not in (-999, -999.0)]
        avg_temp = sum(temps) / len(temps) if temps else None
        total_precip = sum(precips) if precips else None

        month_abbr = _MONTH_ABBR[today.month - 1]
        normal_temp = clim["properties"]["parameter"]["T2M"].get(month_abbr)
        normal_precip_daily_avg = clim["properties"]["parameter"]["PRECTOTCORR"].get(month_abbr)
    except (KeyError, TypeError, ZeroDivisionError):
        return {"label": region["label"], "error": "Unexpected response shape", "source": "power.larc.nasa.gov"}

    temp_anomaly = round(avg_temp - normal_temp, 2) if avg_temp is not None and normal_temp is not None else None
    normal_precip_total = (
        normal_precip_daily_avg * len(precips) if normal_precip_daily_avg is not None and precips else None
    )
    # Below CLIMATE_PRECIP_MIN_NORMAL_MM, the % anomaly is unstable (near-zero
    # denominator during a dry season can turn a trivial rain event into a
    # nonsensical "+400%"), so skip scoring it rather than flag a false signal.
    precip_anomaly_pct = (
        round(100 * (total_precip - normal_precip_total) / normal_precip_total, 1)
        if total_precip is not None and normal_precip_total and normal_precip_total >= CLIMATE_PRECIP_MIN_NORMAL_MM
        else None
    )

    return {
        "label": region["label"],
        "lookback_days": CLIMATE_LOOKBACK_DAYS,
        "avg_temp_c": round(avg_temp, 1) if avg_temp is not None else None,
        "normal_temp_c": round(normal_temp, 1) if normal_temp is not None else None,
        "temp_anomaly_c": temp_anomaly,
        "total_precip_mm": round(total_precip, 1) if total_precip is not None else None,
        "normal_precip_mm": round(normal_precip_total, 1) if normal_precip_total is not None else None,
        "precip_anomaly_pct": precip_anomaly_pct,
        "signal": _signal(temp_anomaly, precip_anomaly_pct),
        "watch": region["watch"],
        "source": "NASA POWER (power.larc.nasa.gov), GMAO MERRA-2",
    }


async def get_climate_risk_watch() -> dict:
    """Get weather/climate production-risk readings for key commodity regions.

    For each tracked region, compares the trailing CLIMATE_LOOKBACK_DAYS of
    observed temperature and precipitation (NASA POWER, MERRA-2 reanalysis)
    against that region's 2001-2020 climatological normal for the current
    month, and flags regions running hot/cold or wet/dry beyond the
    configured thresholds. Covers grains/oilseeds, softs (palm oil, cocoa,
    coffee, sugar), metals/battery materials (copper + lithium), and energy
    (Permian Basin oil/gas) — a curated set, not every commodity that
    exists. Each region lists the value chain a real anomaly there would
    plausibly move: upstream producers, midstream processors/traders,
    downstream consumer-facing buyers, and adjacent asset classes (farmland
    REITs, country ETFs) — a starting point for further research, not a
    trading signal on its own.

    This is a direct weather-conditions read, not an ENSO/El Nino index —
    NASA does not publish the Oceanic Nino Index; that is a NOAA CPC product.

    Returns:
        Dict with per-region readings and a top-level list of any flagged
        regions, or per-region error dicts if NASA POWER is unreachable.
    """
    cache_key = "climate_risk_watch"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    today = dt.date.today()
    async with httpx.AsyncClient() as client:
        keys = list(REGIONS.keys())
        results = await asyncio.gather(*(_region_reading(client, key, REGIONS[key], today) for key in keys))
        readings = dict(zip(keys, results))

    flagged = [
        r["label"] for r in readings.values() if isinstance(r.get("signal"), str) and r["signal"].startswith("FLAGGED")
    ]

    result = {
        "regions": readings,
        "flagged_regions": flagged,
        "note": (
            "Anomalies are vs each region's own 2001-2020 NASA POWER climatological normal "
            "for the current month, not vs ENSO/El Nino status directly. Thresholds "
            "(temp >=2.0C, precip >=60%) are fixed heuristics calibrated to cut noise from "
            "precipitation's naturally bursty 30-day variance, not statistically normalized "
            "z-scores — treat FLAGGED as 'worth a look', not 'confirmed anomalous'. Cross-check "
            "any flagged region against COT positioning, spot commodity prices, and "
            "get_climate_stress_backtest (real historical super-El-Nino price moves) before acting."
        ),
        "source": "NASA POWER (power.larc.nasa.gov) — free, no API key",
    }
    cache.set(cache_key, result, CACHE_TTL_CLIMATE)
    return result


# ---------------------------------------------------------------------------
# Historical stress-period backtest — did these region->ticker links actually
# move during a REAL, verified past super El Nino? Answers "how do I monitor
# this / verify it's not just a theoretical association" by downloading real
# price history for the exact stress window, not a live read.
# ---------------------------------------------------------------------------

# Dated, sourced ENSO event windows. ONI (Oceanic Nino Index) peak values are
# NOAA CPC figures, confirmed via live search — not from training-data memory.
# The month/date window is the conventional onset-to-decay period commentators
# use for these two events; it is NOT a claim that ONI was above threshold on
# every single day in the window.
STRESS_PERIODS: dict[str, dict] = {
    "el_nino_2015_16": {
        "label": "2015-16 Super El Nino",
        "start": "2015-10-01",
        "end": "2016-04-30",
        "description": "Peak ONI +2.6 to +2.8 (Nov-Dec 2015, NOAA CPC) — tied with 1997-98 as the strongest on record until this forecast cycle.",
    },
    "el_nino_1997_98": {
        "label": "1997-98 Super El Nino",
        "start": "1997-10-01",
        "end": "1998-04-30",
        "description": "Peak ONI +2.3 to +2.4 (Nov-Dec 1997, NOAA CPC) — the previous record-holder before 2015-16.",
    },
}

# Clean ticker-only map for backtesting (separate from the descriptive
# strings in REGIONS[...]["watch"], which aren't valid ticker symbols).
# commodity_proxy uses Yahoo continuous-futures symbols where one exists;
# left empty where no direct Yahoo-tradable proxy exists (e.g. palm oil,
# iron ore, lithium have no continuous futures ticker on Yahoo) rather than
# fabricate one.
BACKTEST_TICKERS: dict[str, dict] = {
    "us_corn_belt": {"commodity_proxy": ["ZC=F", "ZS=F"], "equities": ["ADM", "BG", "DE"]},
    "brazil_mato_grosso": {"commodity_proxy": ["ZS=F", "ZC=F"], "equities": ["ADM", "BG"]},
    "argentina_pampas": {"commodity_proxy": ["ZS=F", "ZW=F", "ZC=F"], "equities": ["BG"]},
    "australia_wheat_belt": {"commodity_proxy": ["ZW=F"], "equities": ["GNC.AX"]},
    "indonesia_palm": {"commodity_proxy": [], "equities": ["WLMIY"]},
    "ivory_coast_cocoa": {"commodity_proxy": ["CC=F"], "equities": ["HSY", "MDLZ"]},
    "vietnam_coffee": {"commodity_proxy": ["KC=F"], "equities": ["NSRGY"]},
    "brazil_sugar_belt": {"commodity_proxy": ["SB=F"], "equities": ["CZZ"]},
    "chile_atacama_copper_lithium": {"commodity_proxy": ["HG=F"], "equities": ["BHP", "RIO", "FCX", "SQM", "ALB"]},
    "australia_pilbara_iron_ore": {"commodity_proxy": [], "equities": ["BHP", "RIO"]},
    "us_permian_basin": {"commodity_proxy": ["CL=F", "NG=F"], "equities": ["XOM", "CVX"]},
    "us_gulf_coast": {"commodity_proxy": ["NG=F"], "equities": ["LNG", "XOM"]},
}


async def get_climate_stress_backtest(period: str = "el_nino_2015_16") -> dict:
    """Get REAL historical price moves for the climate-watch tickers during a
    verified past super El Nino, to check whether the region->commodity->
    equity links in get_climate_risk_watch are more than a theoretical
    association.

    Downloads actual daily closes (Yahoo Finance via yfinance) for each
    region's commodity-futures proxy and linked equities across the full
    dated event window, and reports each ticker's % change start-to-end of
    that window. This is realized history, not a live signal — it exists to
    let you see, region by region, how the actual raw-material price and the
    actual linked companies performed during the last comparable stress
    period, before trusting the same links on live data.

    Args:
        period: One of the keys in STRESS_PERIODS (currently "el_nino_2015_16"
            or "el_nino_1997_98"). Defaults to the more recent, better-documented
            event.
    """
    stress = STRESS_PERIODS.get(period)
    if stress is None:
        return {"error": f"Unknown period '{period}'. Valid options: {list(STRESS_PERIODS)}"}

    all_tickers = sorted({t for bt in BACKTEST_TICKERS.values() for t in bt["commodity_proxy"] + bt["equities"]})
    ticker_returns = dict(
        zip(
            all_tickers,
            await asyncio.gather(
                *(
                    backtest_utils.ticker_window_return(
                        t, stress["start"], stress["end"], "climate_backtest", CACHE_TTL_STRESS_BACKTEST
                    )
                    for t in all_tickers
                )
            ),
        )
    )

    regions_out = {}
    for key, region in REGIONS.items():
        bt = BACKTEST_TICKERS.get(key, {"commodity_proxy": [], "equities": []})
        regions_out[key] = {
            "label": region["label"],
            "commodity_proxy_returns": {t: ticker_returns[t] for t in bt["commodity_proxy"]},
            "equity_returns": {t: ticker_returns[t] for t in bt["equities"]},
        }

    return {
        "period": period,
        "label": stress["label"],
        "window": f"{stress['start']} to {stress['end']}",
        "description": stress["description"],
        "regions": regions_out,
        "note": (
            "pct_change is start-to-end close over the full window, not the peak move — a "
            "region that spiked mid-window and reverted will show a smaller number here than "
            "its worst/best point. Missing commodity_proxy entries mean no Yahoo-tradable "
            "continuous-futures symbol exists for that commodity (palm oil, iron ore, lithium); "
            "the linked equities still cover that exposure."
        ),
        "source": "Yahoo Finance historical daily closes (yfinance)",
    }

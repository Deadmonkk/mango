"""Generalized metric stress-period backtest — Phase 1 pilot (VIX, HY credit, CPI).

Answers "does this FR warning threshold actually mean anything for real
companies/sectors" by pulling REAL historical price moves for a curated set
of tickers during a verified past instance of that metric crossing its
warning level. Same pattern as providers/climate.py's ENSO backtest,
generalized so any FR metric can register a STRESS_EVENTS + METRIC_LINKS
entry rather than each metric growing its own bespoke module.

PHASE 1 (this file): VIX panic, high-yield credit stress, CPI/inflation
surge — chosen as the highest-value pilot per user direction (2026-07-06).
PHASE 2 (not built yet): Sahm rule, yield-curve inversion, PSAVERT, the
CCC-BB credit-quality gap, Fed path repricing — extend this same registry
pattern once Phase 1 is validated in real use.

FACT-CHECK DISCIPLINE: every date/value below was verified via live web
search or a direct FRED API query in 2026, not pulled from training-data
memory (see git history / conversation for the verification trail).
Notably: FRED's ICE BofA US High-Yield OAS series (BAMLH0A0HYM2) now carries
an ICE Data license note "Starting in April 2026, this series will only
include 3 years of observations" — confirmed via direct FRED API query —
so the 2008/2020 OAS peak values are NOT fetchable through FRED anymore.
The credit-stress event below uses HYG/JNK (high-yield bond ETF) price
drawdowns as the verifiable market-based proxy instead of the OAS series
itself.
"""

from __future__ import annotations

import asyncio

from terminalq.config import CACHE_TTL_STRESS_BACKTEST

from terminalq.analytics import backtest_utils

# ---------------------------------------------------------------------------
# STRESS_EVENTS — dated, sourced windows for each metric crossing a real
# warning threshold. "threshold_note" documents what conventionally counts
# as a warning level for that metric; it's descriptive context, not a claim
# that the metric stayed above threshold every single day of the window.
# ---------------------------------------------------------------------------
STRESS_EVENTS: dict[str, dict] = {
    "vix_2008_gfc": {
        "metric": "vix",
        "label": "2008 Financial Crisis (VIX panic)",
        "start": "2008-09-01",
        "end": "2008-12-31",
        "peak_value": "80.86 close on 2008-11-20 (89.53 intraday on 2008-10-24) — the pre-2020 all-time closing record",
        "threshold_note": "VIX >=30 is conventionally 'elevated/fear'; >=40 'panic'.",
        "source": "CNBC, Bloomberg, Macroption (verified via web search 2026-07-06)",
    },
    "vix_2020_covid": {
        "metric": "vix",
        "label": "March 2020 COVID crash (VIX panic)",
        "start": "2020-02-15",
        "end": "2020-04-15",
        "peak_value": "82.69 close on 2020-03-16 (83.56 intraday) — the current all-time closing record",
        "threshold_note": "VIX >=30 is conventionally 'elevated/fear'; >=40 'panic'.",
        "source": "CNBC, Bloomberg (verified via web search 2026-07-06)",
    },
    "credit_2008_gfc": {
        "metric": "credit_spreads",
        "label": "2008 Financial Crisis (credit stress)",
        "start": "2008-09-01",
        "end": "2009-03-09",
        "peak_value": "Using HYG/JNK price drawdown as proxy — FRED's HY OAS series (BAMLH0A0HYM2) no longer serves this far back (license restriction, confirmed via direct FRED API query 2026-07-06: 'observation_start: 2023-07-07')",
        "threshold_note": "HY spreads crossing ~600bps has preceded recession in ~85% of 1996-2024 instances (secondary-source citation, not FRED-verified for the exact 2008 peak).",
        "source": "Direct FRED API query 2026-07-06 (confirmed the access restriction); HYG/JNK prices via Yahoo Finance",
    },
    "credit_2020_covid": {
        "metric": "credit_spreads",
        "label": "March 2020 COVID crash (credit stress)",
        "start": "2020-02-15",
        "end": "2020-04-15",
        "peak_value": "Using HYG/JNK price drawdown as proxy (same FRED restriction as above)",
        "threshold_note": "Same ~600bps convention.",
        "source": "HYG/JNK prices via Yahoo Finance",
    },
    "cpi_2021_22_surge": {
        "metric": "cpi",
        "label": "2021-22 inflation surge",
        "start": "2021-06-01",
        "end": "2022-09-30",
        "peak_value": "CPI YoY peaked at 9.1% for the 12 months ended June 2022 — largest increase since Nov 1981",
        "threshold_note": "Fed's longer-run target is 2% YoY on PCE (not CPI directly) — confirmed via Federal Reserve FAQ.",
        "source": "BLS official release; Federal Reserve FAQ (verified via web search 2026-07-06)",
    },
}

# ---------------------------------------------------------------------------
# METRIC_LINKS — tickers a real move in this metric would plausibly affect.
# Kept to well-established, long-standing, high-confidence tickers (mega-cap
# names, decades-old sector ETFs) rather than fabricate niche associations —
# same discipline as climate.py's value-chain map.
# ---------------------------------------------------------------------------
METRIC_LINKS: dict[str, dict] = {
    "vix": {
        "label": "VIX / equity panic",
        "tickers": {
            "broad_market": ["SPY"],
            "high_beta_cyclicals": ["XLF (financials)", "XLY (discretionary)"],
            "defensives": ["XLP (staples)", "XLU (utilities)"],
            "vol_products": ["VIXY"],
        },
    },
    "credit_spreads": {
        "label": "High-yield credit stress",
        "tickers": {
            "hy_bond_proxy": ["HYG", "JNK"],
            "regional_banks": ["KRE (regional bank ETF)"],
            "leveraged_cyclicals": ["XLE (energy — HY-heavy issuer sector)"],
        },
    },
    "cpi": {
        "label": "CPI / inflation surge",
        "tickers": {
            "inflation_beneficiaries": ["XLE (energy)", "GLD (gold)"],
            "inflation_protected": ["TIP (TIPS ETF)"],
            "rate_sensitive_losers": ["XLK (tech/growth — multiple compression)", "XLRE (real estate)"],
        },
    },
}


def _flatten_tickers(metric_key: str) -> list[str]:
    links = METRIC_LINKS.get(metric_key, {})
    out: list[str] = []
    for group in links.get("tickers", {}).values():
        for entry in group:
            # entries are "TICKER (description)" — split off the bare symbol
            out.append(entry.split(" ", 1)[0])
    return sorted(set(out))


async def get_metric_stress_backtest(event: str) -> dict:
    """Get REAL historical price moves for a metric's linked tickers during a
    verified past instance of that metric crossing a real warning threshold.

    Args:
        event: Key in STRESS_EVENTS, e.g. "vix_2008_gfc", "vix_2020_covid",
            "credit_2008_gfc", "credit_2020_covid", "cpi_2021_22_surge".
    """
    stress = STRESS_EVENTS.get(event)
    if stress is None:
        return {"error": f"Unknown event '{event}'. Valid options: {list(STRESS_EVENTS)}"}

    metric_key = stress["metric"]
    links = METRIC_LINKS.get(metric_key)
    if links is None:
        return {"error": f"No ticker links registered for metric '{metric_key}' yet (Phase 2)."}

    tickers = _flatten_tickers(metric_key)
    returns = dict(
        zip(
            tickers,
            await asyncio.gather(
                *(
                    backtest_utils.ticker_window_return(
                        t, stress["start"], stress["end"], "stress_backtest", CACHE_TTL_STRESS_BACKTEST
                    )
                    for t in tickers
                )
            ),
        )
    )

    groups_out = {
        group_name: {entry.split(" ", 1)[0]: returns[entry.split(" ", 1)[0]] for entry in entries}
        for group_name, entries in links["tickers"].items()
    }

    return {
        "event": event,
        "metric": metric_key,
        "metric_label": links["label"],
        "event_label": stress["label"],
        "window": f"{stress['start']} to {stress['end']}",
        "peak_value": stress["peak_value"],
        "threshold_note": stress["threshold_note"],
        "groups": groups_out,
        "fact_source": stress["source"],
        "note": (
            "pct_change is start-to-end close over the full window, not the single worst/best "
            "day — a name that cratered mid-window and partly recovered will show a smaller "
            "number here than its point of maximum stress."
        ),
        "source": "Yahoo Finance historical daily closes (yfinance)",
    }

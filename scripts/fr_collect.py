#!/usr/bin/env python3
"""
fr_collect.py — out-of-context data collector for the FR / EOD reports.

WHY THIS EXISTS
---------------
The FR report pulls ~50 TerminalQ sources. When the model calls those as MCP
tools, all ~50 verbose JSON blobs land in the model's context window (~25k
tokens) and get re-sent every turn. This script runs the SAME provider
functions deterministically, OUTSIDE the model context, and emits ONE compact
"data brief" (~4k tokens) that contains every number the report cites.

FIDELITY GUARANTEE (do not weaken):
  * Numbers are extracted by EXPLICIT field paths from the real provider
    output — never summarized, rounded, or inferred here. Same source of truth
    as the MCP tools; this is a transport optimization, not a data change.
  * Pure-arithmetic derived figures (CCC-BB, realized tariff = duties/imports,
    net liquidity, ERP, etc.) are computed IN CODE — more precise than model
    mental math.
  * On ANY source failure, write the literal string "data unavailable
    (source failed)" for that field. NEVER invent, interpolate, or carry a
    stale value silently. This mirrors the Factual-Integrity rule in
    the project's report contract and is what preserves credibility.

USAGE
-----
    uv run --directory /path/to/terminalq python scripts/fr_collect.py --mode fr
    uv run --directory /path/to/terminalq python scripts/fr_collect.py --mode eod

OUTPUT (written to $FR_BRIEF_DIR, default ~/market-reports/.briefs/):
    fr_raw_YYYY-MM-DD.json     full raw provider payloads (audit trail, git-ignored)
    fr_brief_YYYY-MM-DD.md     compact complete brief the MODEL reads to write the report

The model's job after this runs: read fr_brief_*.md → interpret every metric in
plain English, build the synthesis, reason the two regime scores, set watch-items,
write + save the report. All judgment/quality work stays with the model; only the
raw-data transport is moved out of context.

======================================================================
BUILD STATUS: SKELETON. A fresh session must complete the TOOL_MAP by
reading src/terminalq/server.py (each @mcp.tool() body shows which
mango.providers.* function + args it calls) and wiring each FR source
below to its provider call. The plumbing, brief writer, and failure handling
are done. See FR_COLLECT_SPEC section at the bottom for the full field list.
======================================================================
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fr_sections import render_digest
from eod_report import EOD_PROSE_SLOTS, build_eod_report, eod_report_path
from fr_report import (
    PROSE_SLOTS,
    VALUES_PREFIX,
    build_report,
    extract_values,
    load_prior_values,
    report_path,
)  # noqa: E402

from mango.core.redact import redact  # noqa: E402
from mango.core import fred as mango_fred  # noqa: E402
from mango.core import fred  # noqa: E402

from mango.analytics import (  # noqa: E402
    correlation,
    correlation_regime,
    prediction_grader,
    regime_history,
)
from mango.providers import (  # noqa: E402
    cftc,
    climate,
    crypto_analytics,
    crypto_funding,
    cycle,
    defillama,
    etf_flows,
    fred_ext,
    gz_credit,
    market_data,
    mempool,
    options_flow,
    retail_sentiment,
    sectors,
    valuation,
)
from mango.providers import (  # noqa: E402
    # noqa: E402,
    coingecko,
    edgar,
    finnhub,
    search,
    technical,
)

# Output location. Set FR_BRIEF_DIR to point this at your own reports folder.
BRIEF_DIR = Path(os.getenv("FR_BRIEF_DIR", str(Path.home() / "market-reports" / ".briefs")))
FAIL = "data unavailable (source failed)"

# Tickers pulled for the EOD research-watchlist block. Set FR_WATCHLIST
# (comma-separated) to your own names; the default is an example only.
WATCHLIST = [t.strip().upper() for t in os.getenv("FR_WATCHLIST", "AAPL,MSFT,NVDA,JPM,XOM").split(",") if t.strip()]

# EOD §3 ranks gainers/losers. Ranking is a SORT, not a judgement, so the
# universe is fixed here and ordered in code rather than being chosen by the
# model from a screener each evening. Liquid large caps across every sector so
# the tails mean something. Override with FR_MOVERS.
_DEFAULT_MOVERS = (
    "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,AVGO,AMD,NFLX,"
    "JPM,BAC,GS,V,MA,BRK-B,"
    "XOM,CVX,COP,"
    "UNH,JNJ,LLY,PFE,"
    "WMT,COST,HD,PG,KO,MCD,"
    "CAT,BA,GE,HON,LMT,"
    "NEE,DUK,LIN,SHW,PLD,AMT"
)
MOVERS = [t.strip().upper() for t in os.getenv("FR_MOVERS", _DEFAULT_MOVERS).split(",") if t.strip()]


def a(coro_fn: Callable[..., Awaitable[Any]]) -> Callable[..., Any]:
    """Wrap an async provider function so safe() can call it synchronously."""

    def _runner(*args, **kwargs):
        return asyncio.run(coro_fn(*args, **kwargs))

    return _runner


def safe(fn, *args, **kwargs):
    """Call a provider fn; return its result or the FAIL sentinel. Never raise."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 — deliberate: a failed source must not abort the run
        return {"_error": str(e), "_value": FAIL}


def dig(obj, path, default=FAIL):
    """Extract obj by dotted/indexed path, e.g. 'indicators.hy_spread.latest_value'.
    Returns default (FAIL) on any miss — so a missing field can never become a guess."""
    cur = obj
    try:
        for key in path.split("."):
            cur = cur[int(key)] if key.lstrip("-").isdigit() else cur[key]
        return cur
    except (KeyError, IndexError, TypeError):
        return default


# ---------------------------------------------------------------------------
# TOOL_MAP — TO BE COMPLETED by a fresh session (read server.py for each).
# Each entry: label -> (callable, args, kwargs). Wire to mango.providers.*
# Example once discovered:
#   from mango.providers import fred
#   "credit_spreads": (fred.get_credit_spreads, (), {}),
# The FR source list (mirror the "FR" section of your report contract EXACTLY):
#   crypto (16): crypto_market_overview, fear_greed(limit=7), crypto_deep(BTC),
#     crypto_deep(ETH), crypto_technicals(BTC), btc_onchain, crypto_derivatives,
#     crypto_correlations, crypto_dominance, crypto_trending, defi_overview,
#     cot_report(btc), stablecoins, btc_etf_flows, btc_mempool,
#     crypto_batch(BTC,ETH,SOL,XRP,AVAX,DOGE,BNB)
#   macro/rates/cycle (12): macro_dashboard, cpi_components, jolts,
#     credit_spreads, consumer_health, fiscal_health, commodities, liquidity,
#     rates_dashboard, fed_path, cycle_position, metric_context(hy_spread)
#   EY-gap (11): metric_context(THREEFYTP10, PSAVERT, LES1252881600Q, REVOLSL,
#     BAMLH0A3HYC, BAMLH0A1HYBB, B235RC1Q027SBEA, IMPGS, LNS11300060, OPHNFB),
#     web_search(GSCPI)  [LABEL EXTERNAL in brief]
#   equities/cross-asset (17): market_overview, equity_sentiment,
#     retail_sentiment, market_valuation, technicals(SPY), dealer_gamma(SPY),
#     climate_risk, sector_rotation, style_box, asset_class_returns,
#     correlation_matrix, international_markets, economic_calendar,
#     cot_report(sp500), cot_report(gold), 13f_holdings(berkshire),
#     insider_transactions(NVDA)
#     [dealer_gamma + climate_risk added 2026-08-06: both are required by the
#      FR playbook (§6 gamma/VIX pairing, §7 ESG watch) but were never in this
#      map, so those sections rendered empty every run.]
#   self-learning (2, read-only): grade_predictions, get_regime_history(30)
#   For EOD mode: market_overview, equity_sentiment, technicals(SPY)[ATR],
#     sector_rotation, quotes_batch(11 SPDRs), quotes_batch(research
#     watchlist, see WATCHLIST below), quotes_batch(indices/cross-asset),
#     rates_dashboard, commodities, credit_spreads, asset_class_returns,
#     crypto_market_overview, crypto_batch, crypto_derivatives,
#     economic_calendar, grade_predictions
# ---------------------------------------------------------------------------
_EY_GAP_SERIES = [
    "THREEFYTP10",
    "PSAVERT",
    "LES1252881600Q",
    "REVOLSL",
    "BAMLH0A3HYC",
    "BAMLH0A1HYBB",
    "B235RC1Q027SBEA",
    "IMPGS",
    "LNS11300060",
    "OPHNFB",
]

TOOL_MAP_FR: dict = {
    # --- Crypto (16) ---
    "crypto_market_overview": (a(coingecko.get_crypto_market_overview), (), {}),
    "fear_greed": (a(crypto_analytics.get_fear_greed), (7,), {}),
    "crypto_deep_BTC": (a(coingecko.get_crypto_deep), ("BTC",), {}),
    "crypto_deep_ETH": (a(coingecko.get_crypto_deep), ("ETH",), {}),
    "crypto_technicals_BTC": (a(crypto_analytics.get_crypto_technicals), ("BTC",), {}),
    "btc_onchain": (a(crypto_analytics.get_btc_onchain), (), {}),
    "btc_valuation": (a(crypto_analytics.get_btc_valuation), (), {}),  # MVRV — Crypto Regime 30% leg
    "crypto_funding": (a(crypto_funding.get_btc_funding), ("BTC",), {}),  # OI-weighted, not an unweighted mean
    "crypto_funding_ETH": (a(crypto_funding.get_btc_funding), ("ETH",), {}),
    "gz_credit": (a(gz_credit.get_gz_credit_spread), (), {}),  # long-history credit ref (1973+), non-ICE

    "crypto_derivatives": (a(coingecko.get_crypto_derivatives_dashboard), (), {}),
    "crypto_correlations": (a(crypto_analytics.get_crypto_correlations), ("BTC",), {}),
    "crypto_dominance": (a(coingecko.get_crypto_dominance), (), {}),
    "crypto_trending": (a(coingecko.get_crypto_trending), (), {}),
    "defi_overview": (a(defillama.get_defi_overview), (), {}),
    "cot_report_btc": (a(cftc.get_cot_report), ("btc",), {}),
    "stablecoins": (a(defillama.get_stablecoins_overview), (), {}),
    "btc_etf_flows": (a(etf_flows.get_btc_etf_flows), (10,), {}),
    "btc_mempool": (a(mempool.get_btc_mempool), (), {}),
    "crypto_batch": (a(coingecko.get_crypto_batch), (["BTC", "ETH", "SOL", "XRP", "AVAX", "DOGE", "BNB"],), {}),
    # --- Macro, rates, cycle & liquidity (12) ---
    "macro_dashboard": (a(mango_fred.get_economic_dashboard), (), {}),
    "cpi_components": (a(fred_ext.get_cpi_components_dashboard), (), {}),
    "jolts": (a(fred_ext.get_jolts_dashboard), (), {}),
    "credit_spreads": (a(fred_ext.get_credit_spreads_dashboard), (), {}),
    "consumer_health": (a(fred_ext.get_consumer_health_dashboard), (), {}),
    "fiscal_health": (a(fred_ext.get_fiscal_dashboard), (), {}),
    "commodities": (a(fred_ext.get_commodities_dashboard), (), {}),
    "liquidity": (a(fred_ext.get_liquidity_dashboard), (), {}),
    "rates_dashboard": (a(fred_ext.get_rates_dashboard), (), {}),
    "fed_path": (a(market_data.get_fed_path), (), {}),
    "cycle_position": (a(cycle.get_cycle_position), (), {}),
    "mc_hy_spread": (a(fred_ext.get_metric_context), ("hy_spread",), {}),
    # Calendar priors for §11. get_event_scenarios can only anchor cpi/claims/
    # jobs/payroll, so PPI and retail sales returned a bare "—" every run and had
    # to be backfilled by hand on 2026-08-10. Pull them here instead.
    "mc_PPIFIS": (a(fred_ext.get_metric_context), ("PPIFIS",), {}),
    "mc_RSAFS": (a(fred_ext.get_metric_context), ("RSAFS",), {}),
    # --- EY-gap panel (11): 10 metric_context series + GSCPI web_search ---
    **{f"mc_{sid}": (a(fred_ext.get_metric_context), (sid,), {}) for sid in _EY_GAP_SERIES},
    "web_search_GSCPI_EXTERNAL": (
        a(search.web_search),
        ("New York Fed GSCPI Global Supply Chain Pressure Index latest value", 5),
        {},
    ),
    # --- Equities, valuation & cross-asset (15) ---
    "market_overview": (a(market_data.get_market_overview), (), {}),
    "equity_sentiment": (a(market_data.get_equity_sentiment), (), {}),
    "retail_sentiment": (a(retail_sentiment.get_retail_sentiment), (), {}),
    "market_valuation": (a(valuation.get_market_valuation), (), {}),
    "technicals_SPY": (a(technical.get_full_technicals), ("SPY",), {}),
    "dealer_gamma_SPY": (a(options_flow.get_dealer_gamma), ("SPY",), {}),  # §6 pairs with VIX
    "climate_risk": (a(climate.get_climate_risk_watch), (), {}),  # §7 ESG/production risk
    "sector_rotation": (a(sectors.get_sector_rotation), (), {}),
    "style_box": (a(market_data.get_style_box), (), {}),
    "asset_class_returns": (a(market_data.get_asset_class_returns), (), {}),
    "correlation_matrix": (a(correlation.get_cross_asset_correlation_matrix), ("",), {}),
    "correlation_regime": (a(correlation_regime.get_correlation_regime), ("",), {}),
    "international_markets": (a(market_data.get_international_markets), (), {}),
    "economic_calendar": (a(finnhub.get_economic_calendar), (7,), {}),
    # Finnhub's calendar is premium-walled on this key (persistent 403), which
    # left §11 with no event table for months. FRED's own release schedule
    # aggregates the BLS, BEA and Census calendars and needs only the key the
    # pipeline already uses, so it is gathered every run rather than as a
    # fallback nobody invokes.
    "release_calendar": (a(fred_ext.get_release_calendar), (7,), {}),
    "cot_report_sp500": (a(cftc.get_cot_report), ("sp500",), {}),
    "cot_report_gold": (a(cftc.get_cot_report), ("gold",), {}),
    "13f_holdings_berkshire": (a(edgar.get_13f_holdings), ("berkshire", 20), {}),
    "insider_transactions_NVDA": (a(edgar.get_insider_transactions), ("NVDA", 10), {}),
    # --- Self-learning (2, read-only) ---
    "grade_predictions": (a(prediction_grader.grade_open_predictions), (), {}),
    "regime_history_30": (a(regime_history.get_regime_history), (30,), {}),
}

TOOL_MAP_EOD: dict = {
    "market_overview": (a(market_data.get_market_overview), (), {}),
    "equity_sentiment": (a(market_data.get_equity_sentiment), (), {}),
    "technicals_SPY": (a(technical.get_full_technicals), ("SPY",), {}),
    "sector_rotation": (a(sectors.get_sector_rotation), (), {}),
    "quotes_batch_spdrs": (
        a(finnhub.get_quotes_batch),
        (["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLRE", "XLB", "XLC"],),
        {},
    ),
    "quotes_batch_watchlist": (
        a(finnhub.get_quotes_batch),
        (WATCHLIST,),
        {},
    ),
    "quotes_batch_movers": (a(finnhub.get_quotes_batch), (MOVERS,), {}),
    "rates_dashboard": (a(fred_ext.get_rates_dashboard), (), {}),
    "commodities": (a(fred_ext.get_commodities_dashboard), (), {}),
    "credit_spreads": (a(fred_ext.get_credit_spreads_dashboard), (), {}),
    "asset_class_returns": (a(market_data.get_asset_class_returns), (), {}),
    "crypto_market_overview": (a(coingecko.get_crypto_market_overview), (), {}),
    "crypto_batch": (a(coingecko.get_crypto_batch), (["BTC", "ETH", "SOL", "XRP", "AVAX", "DOGE", "BNB"],), {}),
    "crypto_derivatives": (a(coingecko.get_crypto_derivatives_dashboard), (), {}),
    "economic_calendar": (a(finnhub.get_economic_calendar), (7,), {}),
    "grade_predictions": (a(prediction_grader.grade_open_predictions), (), {}),
}


# ---------------------------------------------------------------------------
# DERIVED (pure arithmetic — compute in code, cite source fields in the brief)
# ---------------------------------------------------------------------------
def derive(raw: dict) -> dict:
    d = {}
    # CCC-BB credit-quality gap
    ccc = dig(raw.get("credit_spreads", {}), "indicators.ccc_spread.latest_value", None)
    bb = dig(raw.get("credit_spreads", {}), "indicators.bb_spread.latest_value", None)
    d["ccc_minus_bb_pp"] = (
        round(ccc - bb, 2) if isinstance(ccc, (int, float)) and isinstance(bb, (int, float)) else FAIL
    )
    # Realized effective tariff = customs duties / imports
    duties = dig(raw.get("mc_B235RC1Q027SBEA", {}), "latest", None)
    imports = dig(raw.get("mc_IMPGS", {}), "latest", None)
    d["realized_tariff_pct"] = (
        round(100 * duties / imports, 2) if isinstance(duties, (int, float)) and imports else FAIL
    )
    # Net liquidity = Fed BS(bn) - RRP(bn) - TGA(bn)  [already in liquidity payload]
    d["net_liquidity_b"] = dig(raw.get("liquidity", {}), "net_liquidity_proxy_billions", FAIL)
    # Equity risk premium from valuation payload (already computed there)
    d["erp_pp"] = dig(raw.get("market_valuation", {}), "equity_risk_premium_pct", FAIL)
    return d


# ---------------------------------------------------------------------------
# BRIEF WRITER — compact markdown the model reads. Keep field labels stable so
# the report-writing prompt can rely on them. Extend as TOOL_MAP is filled.
# ---------------------------------------------------------------------------
def write_brief(raw: dict, derived: dict, mode: str, date: str) -> str:
    lines = [f"# {mode.upper()} DATA BRIEF — {date}", ""]
    lines.append(
        "> Every value below is a live TerminalQ provider result from THIS run, "
        "extracted by explicit field path. `data unavailable (source failed)` = "
        "that source failed; do NOT fill it from memory. GSCPI + last30days are EXTERNAL."
    )
    lines.append("")
    lines.append("## Derived (computed in code)")
    for k, v in derived.items():
        lines.append(f"- **{k}**: {v}")
    lines.append("")
    lines.append("## Raw source digests")
    for label, payload in raw.items():
        if isinstance(payload, dict) and payload.get("_value") == FAIL:
            lines.append(f"- **{label}**: {FAIL} ({payload.get('_error', '')[:80]})")
        else:
            # Compact one-line JSON; the model parses the numbers it needs.
            lines.append(f"- **{label}**: {json.dumps(payload, separators=(',', ':'))[:1200]}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["fr", "eod"], required=True)
    ap.add_argument(
        "--emit-report",
        action="store_true",
        help="also write YYYY-MM-DD-{fr,eod}.md with every deterministic block "
             "populated and empty prose slots (see fr_report.py / eod_report.py).",
    )
    ap.add_argument(
        "--reports-dir",
        type=Path,
        default=Path(os.getenv("FR_REPORTS_DIR", str(BRIEF_DIR.parent))),
        help="where --emit-report writes the report (default: the briefs dir's parent)",
    )
    args = ap.parse_args()
    date = dt.date.today().isoformat()
    tool_map = TOOL_MAP_FR if args.mode == "fr" else TOOL_MAP_EOD

    if not tool_map:
        print("TOOL_MAP is empty — complete the wiring per the header/build spec first.", file=sys.stderr)
        return 2

    collected = {label: safe(fn, *a, **kw) for label, (fn, a, kw) in tool_map.items()}
    # Provider errors quote the failing URL, which for a keyed API carries the
    # key. These files persist in a user-facing folder, so scrub before any of
    # them is written — not at render time, when the raw dump has already landed.
    raw = redact(collected)
    derived = derive(raw)
    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    (BRIEF_DIR / f"{args.mode}_raw_{date}.json").write_text(json.dumps(raw, indent=2, default=str))
    brief = write_brief(raw, derived, args.mode, date)
    (BRIEF_DIR / f"{args.mode}_brief_{date}.md").write_text(brief)
    digest = render_digest(raw, derived, date, args.mode)
    digest_path = BRIEF_DIR / f"{args.mode}_digest_{date}.md"
    digest_path.write_text(digest)
    print(f"wrote {digest_path}  ({len(digest)} chars ~= {len(digest)//4} tok)")
    print(f"      raw brief kept as audit trail ({len(brief)} chars)")

    if args.mode == "fr":
        # The flat value snapshot is what the NEXT run diffs against, so it is
        # written on every FR run, not only when a report is emitted.
        values = extract_values(raw, derived)
        (BRIEF_DIR / f"{VALUES_PREFIX}{date}.json").write_text(json.dumps(values, indent=2, default=str))

    if args.emit_report and args.mode == "eod":
        report = build_eod_report(raw, derived, date)
        args.reports_dir.mkdir(parents=True, exist_ok=True)
        out_path = eod_report_path(args.reports_dir, date)
        out_path.write_text(report, encoding="utf-8")
        print(f"wrote {out_path}  ({len(report)} chars — deterministic blocks populated)")
        print(f"      prose slots to fill: {', '.join(k for k, _ in EOD_PROSE_SLOTS)}")
        return 0

    if args.emit_report:
        prior_values, prior_date = load_prior_values(BRIEF_DIR, date)
        report = build_report(raw, derived, date, prior_values, prior_date)
        args.reports_dir.mkdir(parents=True, exist_ok=True)
        out_path = report_path(args.reports_dir, date)
        out_path.write_text(report, encoding="utf-8")
        print(f"wrote {out_path}  ({len(report)} chars — deterministic blocks populated)")
        print(f"      prose slots to fill: {', '.join(k for k, _ in PROSE_SLOTS)}")
        print(f"      baseline for delta: {prior_date or 'none (first run)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

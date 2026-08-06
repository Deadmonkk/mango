"""FR section specs + the compact digest the model actually reads.

Each Field's `path` was verified against a real `fr_raw_*.json` payload — these
are the report's data contract. A path that stops resolving renders as the FAIL
sentinel rather than silently vanishing, so a provider schema change shows up in
the digest as a visible gap instead of a quietly missing row.
"""
from __future__ import annotations

from fr_render import (
    FAIL,
    Field,
    Section,
    crypto_components,
    dig,
    equity_components,
    render_anomalies,
    render_score_block,
    render_table,
)

# TEMPORARY GUARD: render_region_table(climate: dict) -> str is landing concurrently
# in fr_render.py (owned by another agent working in parallel). The signature is
# pinned, so we code against it now; if it hasn't landed yet, fall back to the FAIL
# sentinel rather than let the whole digest fail to import. Remove this try/except
# once fr_render.render_region_table is confirmed present.
try:
    from fr_render import render_region_table
except ImportError:
    def render_region_table(climate: dict) -> str:  # type: ignore[misc]
        return FAIL

PCT = "%"
USD = ""
PP = "pp"

SECTIONS: tuple[Section, ...] = (
    Section("1", "Macro Snapshot", (
        Field("Real GDP", "macro_dashboard", "indicators.gdp.latest_value"),
        Field("CPI (index)", "macro_dashboard", "indicators.cpi.latest_value"),
        Field("Core CPI (index)", "macro_dashboard", "indicators.core_cpi.latest_value"),
        Field("Unemployment", "macro_dashboard", "indicators.unemployment.latest_value", PCT),
        Field("Initial claims", "macro_dashboard", "indicators.initial_claims.latest_value"),
        Field("Nonfarm payrolls", "macro_dashboard", "indicators.nonfarm_payrolls.latest_value"),
        Field("Consumer sentiment", "macro_dashboard", "indicators.consumer_sentiment.latest_value"),
        Field("Realized effective tariff", "_derived", "realized_tariff_pct", PCT),
        Field("Prime-age LFPR", "mc_LNS11300060", "latest", PCT, read_path="interpretation"),
        Field("Productivity (OPHNFB)", "mc_OPHNFB", "latest", read_path="interpretation"),
    )),
    Section("2", "Cycle Position & Recession Risk", (
        Field("Recession signals active", "cycle_position", "signals_active", read_path="verdict"),
        Field("Signals available", "cycle_position", "signals_available"),
    )),
    Section("3", "Credit, Consumer & Fiscal", (
        Field("IG spread", "credit_spreads", "indicators.ig_spread.latest_value", PP),
        Field("HY spread", "credit_spreads", "indicators.hy_spread.latest_value", PP,
              read_path="indicators.hy_spread.signal"),
        Field("HY percentile vs history", "mc_hy_spread", "percentile_since_start", PCT,
              read_path="interpretation"),
        Field("BB spread", "credit_spreads", "indicators.bb_spread.latest_value", PP),
        Field("CCC spread", "credit_spreads", "indicators.ccc_spread.latest_value", PP),
        Field("CCC − BB gap", "_derived", "ccc_minus_bb_pp", PP),
        Field("Personal saving rate", "mc_PSAVERT", "latest", PCT, read_path="interpretation"),
        Field("Real weekly earnings", "mc_LES1252881600Q", "latest", read_path="interpretation"),
        Field("Revolving credit", "mc_REVOLSL", "latest", read_path="interpretation"),
        Field("GZ credit spread (1973+)", "gz_credit", "gz_spread.latest", PP),
        Field("GZ spread percentile", "gz_credit", "gz_spread.percentile_since_start", PCT),
        Field("Excess bond premium", "gz_credit", "excess_bond_premium.latest", PP,
              read_path="ebp_signal"),
    )),
    Section("4", "Liquidity, Rates & Fed Path", (
        Field("Net liquidity ($bn)", "liquidity", "net_liquidity_proxy_billions"),
        Field("10y Treasury", "rates_dashboard", "indicators.10y_yield.latest_value", PCT),
        Field("2y Treasury", "rates_dashboard", "indicators.2y_yield.latest_value", PCT),
        Field("10y−2y spread", "rates_dashboard", "indicators.yield_spread.latest_value", PP),
        Field("10y real yield (TIPS)", "rates_dashboard", "indicators.tips_10y.latest_value", PCT),
        Field("10y breakeven inflation", "rates_dashboard", "indicators.breakeven_10y.latest_value", PCT),
        Field("Term premium (Kim-Wright)", "mc_THREEFYTP10", "latest", PP, read_path="interpretation"),
        Field("Fed path implied rate", "fed_path", "front_month_implied_rate_pct", PCT,
              read_path="signal"),
        Field("Priced change to horizon", "fed_path", "end_of_horizon_change_bp", "bp"),
    )),
    Section("5", "Valuation", (
        Field("Shiller CAPE", "market_valuation", "cape.latest", read_path="cape.interpretation"),
        Field("CAPE percentile", "market_valuation", "cape.percentile", PCT),
        Field("CAPE range (1871–)", "market_valuation", "cape.min"),
        Field("CAPE max ever", "market_valuation", "cape.max"),
        Field("S&P earnings yield", "market_valuation", "earnings_yield_pct", PCT),
        Field("Equity risk premium", "_derived", "erp_pp", PP),
    )),
    Section("6", "Equities, Sectors & Sentiment", (
        Field("S&P 500", "market_overview", "markets.^GSPC.current"),
        Field("Nasdaq", "market_overview", "markets.^IXIC.current"),
        Field("Russell 2000", "market_overview", "markets.^RUT.current"),
        Field("VIX", "equity_sentiment", "vix_term_structure.vix",
              read_path="vix_term_structure.signal"),
        Field("VIX/VIX3M ratio", "equity_sentiment", "vix_term_structure.ratio"),
        Field("SKEW", "equity_sentiment", "skew.value", read_path="skew.signal"),
        Field("RSP vs SPY (1mo)", "equity_sentiment", "breadth.rsp_vs_spy_1mo_pct", PP,
              read_path="breadth.signal"),
        Field("AAII bull-bear spread", "retail_sentiment", "aaii_survey.bull_bear_spread", PP,
              read_path="aaii_survey.signal"),
        Field("SPY put/call", "retail_sentiment", "spy_put_call.ratio",
              read_path="spy_put_call.signal"),
        Field("Cyclicals vs defensives (3mo)", "sector_rotation", "cyclical_vs_defensive_3mo_pct", PP,
              read_path="signal"),
        Field("SPY RSI(14)", "technicals_SPY", "rsi.rsi", read_path="rsi.signal"),
        Field("SPY vs 200d SMA", "technicals_SPY", "sma.sma_200", read_path="overall_signal"),
        Field("SPY MACD", "technicals_SPY", "macd.histogram", read_path="macd.signal"),
        Field("SPY ATR(14)", "technicals_SPY", "atr.atr"),
        Field("Net dealer gamma (SPY)", "dealer_gamma_SPY", "net_dealer_gamma",
              read_path="signal"),
        Field("Net gamma regime", "dealer_gamma_SPY", "net_gamma_regime"),
        Field("Call wall (SPY)", "dealer_gamma_SPY", "call_wall"),
        Field("Put wall (SPY)", "dealer_gamma_SPY", "put_wall"),
        Field("Put/call OI ratio (SPY)", "dealer_gamma_SPY", "put_call_oi_ratio"),
    )),
    Section("7", "Commodities, Dollar & Climate Risk", (
        Field("WTI crude", "commodities", "indicators.wti_oil.latest_value"),
        Field("Gold", "commodities", "indicators.gold_price.latest_value"),
        Field("Gasoline", "commodities", "indicators.gasoline_price.latest_value"),
        Field("Dollar index", "commodities", "indicators.dollar_index.latest_value"),
    )),
    Section("8", "Crypto Pulse", (
        Field("Total crypto mkt cap", "crypto_market_overview", "total_market_cap_usd",
              read_path="market_cap_signal"),
        Field("24h change", "crypto_market_overview", "market_cap_change_24h_pct", PCT),
        Field("BTC dominance", "crypto_dominance", "dominance.btc_pct", PCT,
              read_path="signals.btc_dominance"),
        Field("ETH dominance", "crypto_dominance", "dominance.eth_pct", PCT),
        Field("Alt season", "crypto_dominance", "altcoin_season_detail.coins_beating_btc_30d",
              read_path="signals.altcoin_season"),
        Field("Fear & Greed", "fear_greed", "current.value", read_path="current.signal"),
        Field("F&G 7d trend", "fear_greed", "7d_trend"),
    )),
    Section("9", "BTC & ETH Deep Dive", (
        Field("BTC price", "crypto_technicals_BTC", "price_usd"),
        Field("BTC 200d SMA", "crypto_technicals_BTC", "moving_averages.sma_200"),
        Field("BTC vs 200d", "crypto_technicals_BTC", "moving_averages.distance_from_200d_ma_pct", PCT,
              read_path="moving_averages.distance_signal"),
        Field("BTC cross", "crypto_technicals_BTC", "moving_averages.cross_signal"),
        Field("BTC RSI(14)", "crypto_technicals_BTC", "momentum.rsi_14",
              read_path="momentum.rsi_signal"),
        Field("BTC funding (OI-weighted, ann.)", "crypto_funding", "funding_annualized_pct", PCT,
              read_path="signal"),
        Field("Funding venues weighted", "crypto_funding", "venues_weighted"),
        Field("Funding basis cross-check", "crypto_funding", "funding_8h_pct", read_path="cross_check"),
        Field("BTC open interest", "crypto_derivatives", "derivatives.BTC.total_open_interest_usd"),
        Field("ETH funding (OI-weighted, ann.)", "crypto_funding_ETH", "funding_annualized_pct", PCT,
              read_path="signal"),
        Field("BTC hash rate", "btc_onchain", "network.hash_rate_gh_s",
              read_path="network.hash_rate_signal"),
        Field("BTC MVRV", "btc_valuation", "mvrv", read_path="signal", decimals=4),
        Field("BTC realized price", "btc_valuation", "realized_price_usd"),
        Field("MVRV percentile vs history", "btc_valuation", "mvrv_percentile", PCT),
        Field("MVRV as-of / staleness", "btc_valuation", "as_of", read_path="staleness"),
        Field("MVRV source & agreement", "btc_valuation", "source", read_path="source_agreement"),
    )),
    Section("10", "Crypto Flows", (
        Field("BTC ETF flow (latest day)", "btc_etf_flows", "latest.total_usd_m", "M",
              read_path="signal"),
        Field("BTC ETF net flow (window)", "btc_etf_flows", "window_net_flow_usd_m", "M"),
        Field("Stablecoin supply", "stablecoins", "total_supply_usd", read_path="trend_signal"),
        Field("Stablecoin 30d change", "stablecoins", "supply_change_30d_pct", PCT),
        Field("DeFi TVL", "defi_overview", "total_tvl_usd", read_path="trend_signal"),
    )),
    Section("12", "Self-Grading & Calibration", (
        Field("Calls settled", "grade_predictions", "totals.settled"),
        Field("Correct", "grade_predictions", "totals.correct"),
        Field("Accuracy", "grade_predictions", "totals.accuracy_pct", PCT),
        Field("Still open", "grade_predictions", "totals.still_open"),
    )),
)


EOD_SECTIONS: tuple[Section, ...] = (
    Section("1", "The Tape", (
        Field("S&P 500", "market_overview", "markets.^GSPC.current"),
        Field("Dow", "market_overview", "markets.^DJI.current"),
        Field("Nasdaq", "market_overview", "markets.^IXIC.current"),
        Field("Russell 2000", "market_overview", "markets.^RUT.current"),
        Field("VIX", "equity_sentiment", "vix_term_structure.vix",
              read_path="vix_term_structure.signal"),
        Field("RSP vs SPY (1mo)", "equity_sentiment", "breadth.rsp_vs_spy_1mo_pct", PCT,
              read_path="breadth.signal"),
        Field("SPY ATR(14)", "technicals_SPY", "atr.atr"),
        Field("SPY RSI(14)", "technicals_SPY", "rsi.rsi", read_path="rsi.signal"),
        Field("SPY 200d SMA", "technicals_SPY", "sma.sma_200", read_path="overall_signal"),
    )),
    Section("2", "Sectors", (
        Field("SPY 1mo", "sector_rotation", "benchmark.return_1mo_pct", PCT),
        Field("Cyclicals vs defensives (3mo)", "sector_rotation", "cyclical_vs_defensive_3mo_pct",
              PP, read_path="signal"),
    )),
    Section("4", "Drivers", (
        Field("10y Treasury", "rates_dashboard", "indicators.10y_yield.latest_value", PCT),
        Field("2y Treasury", "rates_dashboard", "indicators.2y_yield.latest_value", PCT),
        Field("10y real yield", "rates_dashboard", "indicators.tips_10y.latest_value", PCT),
        Field("WTI crude", "commodities", "indicators.wti_oil.latest_value"),
        Field("Gold", "commodities", "indicators.gold_price.latest_value"),
        Field("Dollar index", "commodities", "indicators.dollar_index.latest_value"),
        Field("HY spread", "credit_spreads", "indicators.hy_spread.latest_value", PP,
              read_path="indicators.hy_spread.signal"),
        Field("CCC spread", "credit_spreads", "indicators.ccc_spread.latest_value", PP),
    )),
    Section("5", "Cross-Asset & Crypto", (
        Field("Total crypto mkt cap", "crypto_market_overview", "total_market_cap_usd",
              read_path="market_cap_signal"),
        Field("Crypto 24h change", "crypto_market_overview", "market_cap_change_24h_pct", PCT),
        Field("BTC funding (ann.)", "crypto_derivatives",
              "derivatives.BTC.avg_funding_annualized_pct", PCT,
              read_path="derivatives.BTC.signal"),
        Field("ETH funding (ann.)", "crypto_derivatives",
              "derivatives.ETH.avg_funding_annualized_pct", PCT,
              read_path="derivatives.ETH.signal"),
    )),
    Section("0", "Verification", (
        Field("Calls settled", "grade_predictions", "totals.settled"),
        Field("Correct", "grade_predictions", "totals.correct"),
        Field("Accuracy", "grade_predictions", "totals.accuracy_pct", PCT),
        Field("Still open", "grade_predictions", "totals.still_open"),
    )),
)


def _resolve(raw: dict, derived: dict) -> dict:
    """Expose derived values under the pseudo-source '_derived' so Fields can cite them."""
    merged = dict(raw)
    merged["_derived"] = derived
    return merged


def failed_sources(raw: dict) -> list[str]:
    return [k for k, v in raw.items() if isinstance(v, dict) and v.get("_value") == FAIL]


def render_digest(raw: dict, derived: dict, date: str, mode: str = "fr") -> str:
    """The compact, table-complete digest the model reads instead of raw JSON."""
    src = _resolve(raw, derived)
    out = [
        f"# {mode.upper()} DIGEST — {date}",
        "",
        "> Tables below are FINAL — built in Python from live provider results this run. "
        "Do NOT rebuild, re-order, or restate them. Every `Read` cell is either the provider's "
        "own signal or a threshold rule. Regime scores are computed, not estimated. "
        f"`{FAIL}` = that source failed; never fill it from memory. GSCPI/last30days are EXTERNAL.",
        "",
        "**Your job:** write 2–4 sentences of interpretation under each table (what it means "
        "taken together, how it connects to the rest), plus §0 delta and §12 synthesis. "
        "Never restate a number the table already shows.",
        "",
    ]
    is_fr = mode == "fr"
    for sec in (SECTIONS if is_fr else EOD_SECTIONS):
        out += [f"## {sec.number}. {sec.title}", "", render_table(src, sec), ""]
        if is_fr and sec.number == "7":
            out += [
                "### ESG & Climate Production-Risk Watch",
                "",
                render_region_table(raw.get("climate_risk", {})),
                "",
            ]

    # Regime scores are an FR construct. EOD does not gather the valuation,
    # sentiment or on-chain sources they need, so computing them there would
    # renormalise a handful of survivors into a confident-looking number that
    # means nothing — a 0.0 reading would print as "Euphoric". Omit instead.
    if is_fr:
        out += ["## Regime Scores (computed)",
                render_score_block("Equity Regime Score", equity_components(raw, derived)),
                "",
                render_score_block("Crypto Regime Score", crypto_components(raw, derived)),
                ""]

    out += [render_anomalies(raw), ""]

    gscpi = dig(raw.get("web_search_GSCPI_EXTERNAL", {}), "results.0.snippet", "")
    if gscpi:
        out += ["## EXTERNAL (narrative only — never a scored input)",
                f"- GSCPI (web-sourced): {str(gscpi)[:300]}", ""]

    bad = failed_sources(raw)
    if bad:
        out += [f"## Failed sources ({len(bad)}) — write “{FAIL}” for these, do not substitute",
                ", ".join(bad), ""]
    return "\n".join(out)

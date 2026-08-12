"""FR section specs + the compact digest the model actually reads.

Each Field's `path` was verified against a real `fr_raw_*.json` payload — these
are the report's data contract. A path that stops resolving renders as the FAIL
sentinel rather than silently vanishing, so a provider schema change shows up in
the digest as a visible gap instead of a quietly missing row.
"""
from __future__ import annotations

from fr_render import (
    FAIL,
    NOT_MEANINGFUL,
    Field,
    Section,
    crypto_components,
    dig,
    equity_components,
    level_change,
    pct_change,
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

# Bumped whenever a change alters what a figure MEANS, so an archived report can
# be interpreted without reading the changelog. Not a code version — two reports
# with the same schema are directly comparable; different schemas are not.
#   v1  original
#   v2  2026-08-07 — the GDP row reports real GDP (GDPC1), not nominal (GDP)
#   v3  2026-08-12 — CPI m/m rows report PERCENT, not index points (they read as
#       percent and were 3-6x the BLS print); "Nonfarm payrolls" is the monthly
#       change, with the level moved to its own row; the dollar index names the
#       FRED broad series so it is not read as ICE DXY; §1 and §7 carry as-of dates
REPORT_SCHEMA_VERSION = 3

SECTIONS: tuple[Section, ...] = (
    Section("1", "Macro Snapshot", (
        Field("Real GDP", "macro_dashboard", "indicators.real_gdp.latest_value",
              asof_path="indicators.real_gdp.latest_date"),
        Field("CPI (index, SA)", "macro_dashboard", "indicators.cpi.latest_value",
              asof_path="indicators.cpi.latest_date"),
        Field("Core CPI (index, SA)", "macro_dashboard", "indicators.core_cpi.latest_value",
              asof_path="indicators.core_cpi.latest_date"),
        Field("Unemployment", "macro_dashboard", "indicators.unemployment.latest_value", PCT,
              asof_path="indicators.unemployment.latest_date"),
        Field("Initial claims", "macro_dashboard", "indicators.initial_claims.latest_value",
              asof_path="indicators.initial_claims.latest_date"),
        # The monthly CHANGE, which is what "payrolls" means in a macro report.
        # The level is kept on the next row, explicitly labelled as a level.
        Field("Nonfarm payrolls (m/m change, 000s)", "macro_dashboard", "",
              value_fn=level_change("indicators.nonfarm_payrolls"),
              asof_path="indicators.nonfarm_payrolls.latest_date"),
        Field("Total nonfarm employment (level, 000s)", "macro_dashboard",
              "indicators.nonfarm_payrolls.latest_value",
              asof_path="indicators.nonfarm_payrolls.latest_date"),
        Field("Consumer sentiment", "macro_dashboard", "indicators.consumer_sentiment.latest_value",
              asof_path="indicators.consumer_sentiment.latest_date"),
        Field("Realized effective tariff", "_derived", "realized_tariff_pct", PCT),
        Field("Prime-age LFPR", "mc_LNS11300060", "latest", PCT, read_path="interpretation"),
        Field("Productivity (OPHNFB)", "mc_OPHNFB", "latest", read_path="interpretation"),
        # Composition, not just the headline: an energy-led fall and a broad
        # disinflation look identical at the top line. Collected since the map was
        # written but never rendered until 2026-08-10.
        Field("CPI shelter", "cpi_components", "indicators.cpi_shelter.latest_value"),
        Field("CPI energy", "cpi_components", "indicators.cpi_energy.latest_value"),
        Field("CPI food & beverages", "cpi_components", "indicators.cpi_food.latest_value"),
        Field("CPI core goods", "cpi_components", "indicators.cpi_core_goods.latest_value"),
        Field("CPI services ex energy", "cpi_components", "indicators.cpi_services.latest_value"),
        # Percent, computed from the index levels. The provider's own `.change`
        # is an INDEX-POINT delta: rendering it under an "m/m change" header
        # read as +0.72% for a month whose actual core print was +0.2%.
        Field("CPI m/m change", "cpi_components", "", PCT,
              value_fn=pct_change("indicators.cpi"), decimals=2,
              asof_path="indicators.cpi.latest_date"),
        Field("Core CPI m/m change", "cpi_components", "", PCT,
              value_fn=pct_change("indicators.core_cpi"), decimals=2,
              asof_path="indicators.core_cpi.latest_date"),
        Field("Energy m/m change", "cpi_components", "", PCT,
              value_fn=pct_change("indicators.cpi_energy"), decimals=2,
              asof_path="indicators.cpi_energy.latest_date"),
        Field("Shelter m/m change", "cpi_components", "", PCT,
              value_fn=pct_change("indicators.cpi_shelter"), decimals=2,
              asof_path="indicators.cpi_shelter.latest_date"),
    )),
    Section("2", "Cycle Position & Recession Risk", (
        Field("Recession signals active", "cycle_position", "signals_active", read_path="verdict"),
        Field("Signals available", "cycle_position", "signals_available"),
        # The count alone hides whether a signal sits near its trigger. Each
        # signal's own `meaning` string carries the Read.
        Field("Sahm rule", "cycle_position", "signals.0.value", read_path="signals.0.meaning"),
        Field("Yield curve 10y−2y", "cycle_position", "signals.1.value", read_path="signals.1.meaning"),
        Field("Yield curve 10y−3m", "cycle_position", "signals.2.value", read_path="signals.2.meaning"),
        Field("Claims trend", "cycle_position", "signals.3.value", read_path="signals.3.meaning"),
        Field("Financial conditions (NFCI)", "cycle_position", "signals.4.value",
              read_path="signals.4.meaning"),
        Field("GDPNow nowcast", "cycle_position", "signals.5.value", read_path="signals.5.meaning"),
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
        # Delinquencies are the LAGGING confirmation of the income squeeze the
        # saving-rate/revolving-credit rows above lead. Both halves belong here.
        Field("Household debt service (% of DPI)", "consumer_health",
              "indicators.debt_service_ratio.latest_value", PCT),
        Field("Credit-card delinquency", "consumer_health",
              "indicators.cc_delinquency.latest_value", PCT),
        Field("Consumer-loan delinquency", "consumer_health",
              "indicators.consumer_delinquency.latest_value", PCT),
        Field("Mortgage delinquency", "consumer_health",
              "indicators.mortgage_delinquency.latest_value", PCT),
        Field("Federal debt / GDP", "fiscal_health",
              "indicators.federal_debt_gdp.latest_value", PCT),
        Field("Federal deficit (monthly, $M)", "fiscal_health",
              "indicators.federal_deficit.latest_value"),
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
        # Required by the FR playbook (§6 correlation-regime check + COT), collected
        # since the map was written but never rendered until 2026-08-10.
        Field("Correlation regime", "correlation_regime", "verdict"),
        Field("Coupling recent (~1mo)", "correlation_regime", "avg_coupling_recent"),
        Field("Coupling baseline (~1q)", "correlation_regime", "avg_coupling_baseline"),
        Field("Correlation avg |Δ|", "correlation_regime", "avg_abs_delta"),
        Field("COT S&P 500 large-spec net", "cot_report_sp500", "large_speculators.net",
              read_path="signal"),
        Field("COT S&P 500 spec % of OI", "cot_report_sp500", "large_spec_pct_of_oi", PCT),
        Field("COT S&P 500 net WoW", "cot_report_sp500", "large_speculators.net_change"),
        Field("COT gold large-spec net", "cot_report_gold", "large_speculators.net",
              read_path="signal"),
        Field("COT gold spec % of OI", "cot_report_gold", "large_spec_pct_of_oi", PCT),
        Field("COT gold net WoW", "cot_report_gold", "large_speculators.net_change"),
    )),
    Section("7", "Commodities, Dollar & Climate Risk", (
        Field("WTI crude (Cushing spot)", "commodities", "indicators.wti_oil.latest_value",
              asof_path="indicators.wti_oil.latest_date"),
        Field("Gold (COMEX front month)", "commodities", "indicators.gold_price.latest_value",
              asof_path="indicators.gold_price.latest_date"),
        Field("Gasoline", "commodities", "indicators.gasoline_price.latest_value",
              asof_path="indicators.gasoline_price.latest_date"),
        Field("Dollar index (FRED broad, Jan 2006=100 — NOT ICE DXY)", "commodities",
              "indicators.dollar_index.latest_value",
              asof_path="indicators.dollar_index.latest_date"),
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
        Field("COT bitcoin large-spec net", "cot_report_btc", "large_speculators.net",
              read_path="signal"),
        Field("COT bitcoin spec % of OI", "cot_report_btc", "large_spec_pct_of_oi", PCT),
        Field("COT bitcoin net WoW", "cot_report_btc", "large_speculators.net_change"),
    )),
    Section("10", "Crypto Flows", (
        Field("BTC ETF flow (latest day)", "btc_etf_flows", "latest.total_usd_m", "M",
              read_path="signal"),
        Field("BTC ETF net flow (window)", "btc_etf_flows", "window_net_flow_usd_m", "M"),
        Field("Stablecoin supply", "stablecoins", "total_supply_usd", read_path="trend_signal"),
        Field("Stablecoin 30d change", "stablecoins", "supply_change_30d_pct", PCT),
        Field("DeFi TVL", "defi_overview", "total_tvl_usd", read_path="trend_signal"),
    )),
    Section("11", "Global Markets & Calendar Priors", (
        Field("MSCI EAFE ex-US (EFA) YTD", "international_markets", "markets.EFA.ytd_return_pct", PCT),
        Field("Japan (EWJ) YTD", "international_markets", "markets.EWJ.ytd_return_pct", PCT),
        Field("Europe (VGK) YTD", "international_markets", "markets.VGK.ytd_return_pct", PCT),
        Field("Emerging markets (VWO) YTD", "international_markets", "markets.VWO.ytd_return_pct", PCT),
        Field("China large-cap (FXI) YTD", "international_markets", "markets.FXI.ytd_return_pct", PCT),
        Field("India (INDA) YTD", "international_markets", "markets.INDA.ytd_return_pct", PCT),
        Field("South Korea (EWY) YTD", "international_markets", "markets.EWY.ytd_return_pct", PCT),
        Field("Brazil (EWZ) YTD", "international_markets", "markets.EWZ.ytd_return_pct", PCT),
        # Calendar PRIORS. get_event_scenarios only anchors cpi/claims/jobs/payroll,
        # so PPI and retail sales rendered a bare "—" until 2026-08-10.
        Field("PPI final demand (prior)", "mc_PPIFIS", "latest", read_path="interpretation"),
        Field("Retail sales (prior, $M)", "mc_RSAFS", "latest", read_path="interpretation"),
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
        Field("WTI crude (Cushing spot)", "commodities", "indicators.wti_oil.latest_value",
              asof_path="indicators.wti_oil.latest_date"),
        Field("Gold (COMEX front month)", "commodities", "indicators.gold_price.latest_value",
              asof_path="indicators.gold_price.latest_date"),
        Field("Dollar index (FRED broad, Jan 2006=100 — NOT ICE DXY)", "commodities",
              "indicators.dollar_index.latest_value",
              asof_path="indicators.dollar_index.latest_date"),
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
        f"*Report schema v{REPORT_SCHEMA_VERSION} — carry this into the saved report. Figures may not\nbe directly comparable across schema versions; see CHANGELOG.md.*",
        "",
        "> Tables below are FINAL — built in Python from live provider results this run. "
        "Do NOT rebuild, re-order, or restate them. Every `Read` cell is either the provider's "
        "own signal or a threshold rule. Regime scores are computed, not estimated. "
        f"`{FAIL}` = that source failed; never fill it from memory. "
        f"`{NOT_MEANINGFUL}` is DIFFERENT — the provider returned the field as null "
        "because it is not meaningful there (e.g. a percent anomaly off a near-zero "
        "base); report it as not applicable, never as a failure. "
        "GSCPI/last30days are EXTERNAL.",
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

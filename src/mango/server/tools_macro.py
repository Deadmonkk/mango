"""Macro tools: FRED series and dashboards, rates, credit, cycle, cross-asset."""

from __future__ import annotations

from mango.analytics import correlation, correlation_regime, percentiles
from mango.core import fred
from mango.providers import (
    cftc,
    climate,
    cycle,
    fred_ext,
    gz_credit,
    market_data,
    options_flow,
    retail_sentiment,
    sectors,
    stress_backtest,
    valuation,
)
from mango.server import csv_symbols, tool


@tool
async def get_economic_indicator(indicator: str, limit: int = 12) -> dict:
    """One FRED series by friendly alias (gdp, cpi, unemployment) or raw series ID."""
    return await fred.get_series(indicator, limit=limit)


@tool
async def get_macro_dashboard() -> dict:
    """Headline US economic indicators: growth, inflation, rates, labour."""
    return await fred.get_economic_dashboard()


@tool
async def get_cpi_components() -> dict:
    """CPI broken into shelter, energy, food, core goods and core services.

    The composition matters more than the headline: an energy-driven fall and
    a broad disinflation look identical at the top line.
    """
    return await fred_ext.get_cpi_components_dashboard()


@tool
async def get_jolts() -> dict:
    """Job openings, hires, quits and layoffs. Leads the unemployment rate."""
    return await fred_ext.get_jolts_dashboard()


@tool
async def get_credit_spreads() -> dict:
    """Investment-grade and high-yield spreads by rating tier.

    The gap between CCC and BB matters as much as the index level: a tight
    index with a stressed low-quality tail is not a calm credit market.
    """
    return await fred_ext.get_credit_spreads_dashboard()


@tool
async def get_consumer_health() -> dict:
    """Household finances: debt service, delinquencies, saving rate, revolving credit."""
    return await fred_ext.get_consumer_health_dashboard()


@tool
async def get_fiscal_health() -> dict:
    """Federal debt-to-GDP and the monthly budget balance."""
    return await fred_ext.get_fiscal_dashboard()


@tool
async def get_commodities() -> dict:
    """Crude, gasoline and the dollar index."""
    return await fred_ext.get_commodities_dashboard()


@tool
async def get_liquidity() -> dict:
    """Net liquidity: Fed balance sheet less reverse repo and the Treasury account."""
    return await fred_ext.get_liquidity_dashboard()


@tool
async def get_rates_dashboard() -> dict:
    """Treasury yields, real (TIPS) yields and breakeven inflation.

    Nominal minus breakeven separates a growth repricing from an inflation one.
    """
    return await fred_ext.get_rates_dashboard()


@tool
async def get_metric_context(indicator: str) -> dict:
    """Rank a metric against its own history: percentile, min, max, median.

    Check the history window in the result — a vendor licence change can
    truncate a series, and a percentile over three years is not the same claim
    as one over thirty.
    """
    return await fred_ext.get_metric_context(indicator)


@tool
async def get_cycle_position() -> dict:
    """Recession dashboard: Sahm rule, yield curves, claims trend, NFCI, GDPNow."""
    return await cycle.get_cycle_position()


@tool
async def get_market_overview() -> dict:
    """Major index levels with year-to-date and one-year returns."""
    return await market_data.get_market_overview()


@tool
async def get_international_markets() -> dict:
    """International equity performance in USD terms."""
    return await market_data.get_international_markets()


@tool
async def get_style_box() -> dict:
    """Returns across the size and value/growth grid."""
    return await market_data.get_style_box()


@tool
async def get_asset_class_returns() -> dict:
    """Returns across equities, bonds, commodities, the dollar and crypto."""
    return await market_data.get_asset_class_returns()


@tool
async def get_fed_path() -> dict:
    """Market-implied policy path from fed funds futures. Equivalent to FedWatch."""
    return await market_data.get_fed_path()


@tool
async def get_equity_sentiment() -> dict:
    """VIX term structure, SKEW and equal-weight versus cap-weight breadth."""
    return await market_data.get_equity_sentiment()


@tool
async def get_retail_sentiment() -> dict:
    """AAII bull-bear survey and the SPY put/call ratio.

    Stated sentiment and actual positioning often disagree; when they do, the
    positioning is the more reliable signal.
    """
    return await retail_sentiment.get_retail_sentiment()


@tool
async def get_sector_rotation() -> dict:
    """Sector ETFs versus SPY over 1, 3 and 6 months, with a cyclical/defensive spread."""
    return await sectors.get_sector_rotation()


@tool
async def get_market_valuation() -> dict:
    """Shiller CAPE with its long-history percentile, earnings yield, equity risk premium."""
    return await valuation.get_market_valuation()


@tool
async def get_cot_report(market: str) -> dict:
    """CFTC Commitment of Traders positioning: commercials, large and small specs."""
    return await cftc.get_cot_report(market)


@tool
async def get_correlation_matrix(symbols: str = "") -> dict:
    """Cross-asset correlation matrix. Defaults to a standard multi-asset set."""
    return await correlation.get_cross_asset_correlation_matrix(",".join(csv_symbols(symbols)))


@tool
async def get_correlation_regime(symbols: str = "") -> dict:
    """Recent versus baseline correlation. Rising coupling means diversification is failing."""
    return await correlation_regime.get_correlation_regime(",".join(csv_symbols(symbols)))


@tool
async def get_dealer_gamma(symbol: str = "SPY") -> dict:
    """Options dealer positioning: net gamma sign and the nearest call and put walls.

    Positive gamma dampens moves; negative amplifies them. A low VIX means
    something different under each.
    """
    return await options_flow.get_dealer_gamma(symbol.upper())


@tool
async def get_climate_risk_watch() -> dict:
    """Weather anomalies across commodity-producing regions versus their normals.

    Flags are fixed thresholds, not normalised scores — treat a flag as worth a
    look rather than as confirmation.
    """
    return await climate.get_climate_risk_watch()


@tool
async def get_climate_stress_backtest(period: str = "el_nino_2015_16") -> dict:
    """Real price moves for climate-exposed tickers during a past El Nino.

    A historical analogue, not a forecast.
    """
    return await climate.get_climate_stress_backtest(period)


@tool
async def get_metric_stress_backtest(event: str) -> dict:
    """Real price moves when a metric last crossed its warning threshold."""
    return await stress_backtest.get_metric_stress_backtest(event)


@tool
async def get_forex(pair: str = "") -> dict:
    """Currency exchange rates from FRED."""
    return await fred.get_series(pair or "DTWEXBGS", limit=5)


@tool
async def get_gz_credit() -> dict:
    """Gilchrist-Zakrajsek credit spread and excess bond premium, monthly since 1973.

    A long-history credit reference that is not subject to the vendor licence
    truncation affecting the ICE series.
    """
    return await gz_credit.get_gz_credit_spread()


@tool
async def get_percentile_context(indicator: str, value: float) -> dict:
    """Rank an arbitrary value against a series' history."""
    return await percentiles.rank_value(indicator, value)

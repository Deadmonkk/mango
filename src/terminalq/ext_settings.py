"""Settings this extension pack needs, owned by the pack itself.

Why this module exists
----------------------
These providers were originally written against a ``terminalq/config.py`` that
had been extended in place with the thresholds and cache TTLs below. Editing
that upstream file is not something this pack can ship — it belongs to
TerminalQ — so importing these names from ``terminalq.config`` made the pack
uninstallable against a clean TerminalQ checkout: 57 of the 60 names it needed
simply were not there.

Defining them here makes the pack self-sufficient. Anything TerminalQ already
provides is re-exported from upstream so its value wins; everything else falls
back to the default defined below. Adding a name to ``terminalq/config.py``
therefore overrides this file without any edit here.

The thresholds are judgement calls, not standards — a "high" VIX or an
"extreme" AAII spread is a choice. Override them rather than treating them as
authoritative.
"""

from terminalq import config as _upstream


def _from_upstream(name: str, default):
    """Prefer TerminalQ's value when it defines one, else use our default."""
    return getattr(_upstream, name, default)


# Re-exported from upstream when present (TerminalQ already defines these).
PORTFOLIO_DIR = _upstream.PORTFOLIO_DIR
CACHE_TTL_HISTORY = _from_upstream("CACHE_TTL_HISTORY", 3600)
CACHE_TTL_FUNDAMENTALS = _from_upstream("CACHE_TTL_FUNDAMENTALS", 86400)
REPORTS_DIR = _from_upstream("REPORTS_DIR", PORTFOLIO_DIR / "reports")

# Defined by this pack. Each may be overridden upstream; see _from_upstream.
AAII_SPREAD_EXTREME_PP = _from_upstream("AAII_SPREAD_EXTREME_PP", 10.0)
CACHE_TTL_CLIMATE = _from_upstream("CACHE_TTL_CLIMATE", 21600)
CACHE_TTL_CORRELATIONS = _from_upstream("CACHE_TTL_CORRELATIONS", 21600)
CACHE_TTL_CORRELATION_REGIME = _from_upstream("CACHE_TTL_CORRELATION_REGIME", 21600)
CACHE_TTL_COT = _from_upstream("CACHE_TTL_COT", 21600)
CACHE_TTL_CRYPTO_TECHNICALS = _from_upstream("CACHE_TTL_CRYPTO_TECHNICALS", 3600)
CACHE_TTL_CYCLE = _from_upstream("CACHE_TTL_CYCLE", 3600)
CACHE_TTL_DEFI = _from_upstream("CACHE_TTL_DEFI", 1800)
CACHE_TTL_EQUITY_SENTIMENT = _from_upstream("CACHE_TTL_EQUITY_SENTIMENT", 3600)
CACHE_TTL_ETF_FLOWS = _from_upstream("CACHE_TTL_ETF_FLOWS", 1800)
CACHE_TTL_FEAR_GREED = _from_upstream("CACHE_TTL_FEAR_GREED", 3600)
CACHE_TTL_FED_PATH = _from_upstream("CACHE_TTL_FED_PATH", 3600)
CACHE_TTL_FOMC = _from_upstream("CACHE_TTL_FOMC", 86400)
CACHE_TTL_MEMPOOL = _from_upstream("CACHE_TTL_MEMPOOL", 300)
CACHE_TTL_ONCHAIN = _from_upstream("CACHE_TTL_ONCHAIN", 1800)
CACHE_TTL_OPTIONS_GAMMA = _from_upstream("CACHE_TTL_OPTIONS_GAMMA", 900)
CACHE_TTL_PREDICTION_MARKETS = _from_upstream("CACHE_TTL_PREDICTION_MARKETS", 1800)
CACHE_TTL_RETAIL_SENTIMENT = _from_upstream("CACHE_TTL_RETAIL_SENTIMENT", 3600)
CACHE_TTL_SECTORS = _from_upstream("CACHE_TTL_SECTORS", 3600)
CACHE_TTL_STABLECOINS = _from_upstream("CACHE_TTL_STABLECOINS", 1800)
CACHE_TTL_STRESS_BACKTEST = _from_upstream("CACHE_TTL_STRESS_BACKTEST", 2592000)
CACHE_TTL_VALUATION = _from_upstream("CACHE_TTL_VALUATION", 21600)
CLAIMS_DETERIORATION_PCT = _from_upstream("CLAIMS_DETERIORATION_PCT", 10.0)
CLAIMS_LOOKBACK_WEEKS = _from_upstream("CLAIMS_LOOKBACK_WEEKS", 20)
CLIMATE_LOOKBACK_DAYS = _from_upstream("CLIMATE_LOOKBACK_DAYS", 30)
CLIMATE_PRECIP_ANOMALY_WATCH_PCT = _from_upstream("CLIMATE_PRECIP_ANOMALY_WATCH_PCT", 60.0)
CLIMATE_PRECIP_MIN_NORMAL_MM = _from_upstream("CLIMATE_PRECIP_MIN_NORMAL_MM", 15.0)
CLIMATE_TEMP_ANOMALY_WATCH_C = _from_upstream("CLIMATE_TEMP_ANOMALY_WATCH_C", 2.0)
CORRELATION_REGIME_LONG_DAYS = _from_upstream("CORRELATION_REGIME_LONG_DAYS", 90)
CORRELATION_REGIME_SHIFT_DELTA = _from_upstream("CORRELATION_REGIME_SHIFT_DELTA", 0.30)
CORRELATION_REGIME_SHORT_DAYS = _from_upstream("CORRELATION_REGIME_SHORT_DAYS", 21)
COT_LARGE_SPEC_EXTREME_RATIO = _from_upstream("COT_LARGE_SPEC_EXTREME_RATIO", 0.20)
DEFILLAMA_RATE_LIMIT = _from_upstream("DEFILLAMA_RATE_LIMIT", 30)
ERP_THIN_CUSHION_PP = _from_upstream("ERP_THIN_CUSHION_PP", 2.0)
ETF_FLOWS_DEFAULT_DAYS = _from_upstream("ETF_FLOWS_DEFAULT_DAYS", 10)
FEAR_GREED_EXTREME_FEAR = _from_upstream("FEAR_GREED_EXTREME_FEAR", 20)
FEAR_GREED_EXTREME_GREED = _from_upstream("FEAR_GREED_EXTREME_GREED", 80)
FED_PATH_MONTHS_AHEAD = _from_upstream("FED_PATH_MONTHS_AHEAD", 9)
FED_PATH_SIGNAL_THRESHOLD_BP = _from_upstream("FED_PATH_SIGNAL_THRESHOLD_BP", 12.5)
HALVING_INTERVAL = _from_upstream("HALVING_INTERVAL", 210_000)
MEMPOOL_FEE_CONGESTED_SAT_VB = _from_upstream("MEMPOOL_FEE_CONGESTED_SAT_VB", 50)
MEMPOOL_FEE_QUIET_SAT_VB = _from_upstream("MEMPOOL_FEE_QUIET_SAT_VB", 5)
OPTIONS_GAMMA_EXPIRIES = _from_upstream("OPTIONS_GAMMA_EXPIRIES", 3)
PREDICTION_MARKETS_LIMIT = _from_upstream("PREDICTION_MARKETS_LIMIT", 6)
PUT_CALL_COMPLACENT_RATIO = _from_upstream("PUT_CALL_COMPLACENT_RATIO", 0.7)
PUT_CALL_FEAR_RATIO = _from_upstream("PUT_CALL_FEAR_RATIO", 1.2)
SAHM_TRIGGER_PP = _from_upstream("SAHM_TRIGGER_PP", 0.50)
SKEW_ELEVATED_THRESHOLD = _from_upstream("SKEW_ELEVATED_THRESHOLD", 145)
STABLECOIN_GROWTH_SIGNAL_PCT = _from_upstream("STABLECOIN_GROWTH_SIGNAL_PCT", 1.0)
TOP_STABLECOINS_LIMIT = _from_upstream("TOP_STABLECOINS_LIMIT", 5)
VIX_ELEVATED_THRESHOLD = _from_upstream("VIX_ELEVATED_THRESHOLD", 20)
VIX_HIGH_THRESHOLD = _from_upstream("VIX_HIGH_THRESHOLD", 30)
VIX_LOW_THRESHOLD = _from_upstream("VIX_LOW_THRESHOLD", 15)
VIX_TERM_BACKWARDATION_RATIO = _from_upstream("VIX_TERM_BACKWARDATION_RATIO", 1.0)
VIX_TERM_COMPLACENCY_RATIO = _from_upstream("VIX_TERM_COMPLACENCY_RATIO", 0.85)

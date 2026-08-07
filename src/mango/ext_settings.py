"""Settings this extension pack needs, owned by the pack itself.

Why this module exists
----------------------
These providers were originally written against a ``terminalq/config.py`` that
had been extended in place with the thresholds and cache TTLs below. Editing
that upstream file is not something this pack can ship — it belongs to
TerminalQ — so importing these names from ``mango.config`` made the pack
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

import os
from pathlib import Path

try:  # optional: this pack must import with no host project present
    from terminalq import config as _upstream
except ImportError:  # pragma: no cover - exercised only in a standalone install
    _upstream = None


def _from_upstream(name: str, default):
    """Prefer the host's value when one is defined, else use our default.

    The import above is optional so the pack stands alone. Every call below
    passes a real default, so a missing host changes nothing except that the
    defaults apply — which is the intended standalone behaviour, not a
    degraded one.
    """
    if _upstream is None:
        return default
    return getattr(_upstream, name, default)


# Re-exported from upstream when present (TerminalQ already defines these).
# Dereferencing _upstream directly here defeated the guarded import above: with
# no host present _upstream is None and this raised AttributeError at import,
# so the package could not load standalone at all.
PORTFOLIO_DIR = _from_upstream("PORTFOLIO_DIR", Path.home() / ".terminalq")
CACHE_TTL_HISTORY = _from_upstream("CACHE_TTL_HISTORY", 3600)
CACHE_TTL_FUNDAMENTALS = _from_upstream("CACHE_TTL_FUNDAMENTALS", 86400)
# Saved reports rarely live beside the portfolio data — they are usually
# somewhere the operator reads them, so an env var is the primary source.
# Without this, load_recent_reports pointed at a directory that does not exist.
REPORTS_DIR = Path(
    os.environ.get("REPORTS_DIR")
    or _from_upstream("REPORTS_DIR", PORTFOLIO_DIR / "reports")
).expanduser()

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

# --- BTC on-chain valuation (MVRV) ---------------------------------------
# MVRV = market cap / realized cap. Below 1.0 the average holder is underwater —
# historically a capitulation zone. Added 2026-08-05 when the Crypto Regime
# Score's heaviest component (on-chain valuation, 30%) was found to have had no
# data source at all and to have been silently renormalised out of every run.
CACHE_TTL_BTC_VALUATION = _from_upstream("CACHE_TTL_BTC_VALUATION", 21600)  # 6h; MVRV is daily
MVRV_UNDERVALUED = _from_upstream("MVRV_UNDERVALUED", 1.0)
MVRV_OVERVALUED = _from_upstream("MVRV_OVERVALUED", 3.5)
# realized_price x MVRV must reproduce spot within this, else the reading is
# flagged unreliable rather than scored.
BTC_VALUATION_CROSSCHECK_TOLERANCE_PCT = _from_upstream("BTC_VALUATION_CROSSCHECK_TOLERANCE_PCT", 5.0)
# Two independent MVRV sources (Coin Metrics primary, bitcoin-data.com second)
# must agree within this, else flagged.
MVRV_SOURCE_AGREEMENT_TOLERANCE_PCT = _from_upstream("MVRV_SOURCE_AGREEMENT_TOLERANCE_PCT", 5.0)

# --- RSU tax assumptions --------------------------------------------------
# Estimates only, never tax advice. Values match the documented defaults in
# commands/tq-rsu-tax.md ("Defaults to 0.32") and server.py's tool signature.
RSU_DEFAULT_MARGINAL_RATE = _from_upstream("RSU_DEFAULT_MARGINAL_RATE", 0.32)
RSU_DEFAULT_LTCG_RATE = _from_upstream("RSU_DEFAULT_LTCG_RATE", 0.15)

# --- CoinGecko tuning -----------------------------------------------------
# RECONSTRUCTED 2026-08-05. These were defined in a local edit to upstream
# config.py that was lost when that file was reverted to upstream state. The
# code using them survived, so each value below is bounded by observed behaviour
# rather than invented — but they are RECONSTRUCTIONS, not the originals.
# Correct any that look wrong; nothing downstream asserts on their exact values.
CACHE_TTL_CRYPTO_OVERVIEW = _from_upstream("CACHE_TTL_CRYPTO_OVERVIEW", 300)
CACHE_TTL_CRYPTO_DEEP = _from_upstream("CACHE_TTL_CRYPTO_DEEP", 300)
CACHE_TTL_CRYPTO_DERIVATIVES = _from_upstream("CACHE_TTL_CRYPTO_DERIVATIVES", 300)
CACHE_TTL_CRYPTO_TRENDING = _from_upstream("CACHE_TTL_CRYPTO_TRENDING", 900)
# Retry loop is `for attempt in range(MAX)` with `BASE * 2**attempt` backoff.
COINGECKO_MAX_RETRIES = _from_upstream("COINGECKO_MAX_RETRIES", 3)
COINGECKO_RETRY_BASE_DELAY = _from_upstream("COINGECKO_RETRY_BASE_DELAY", 1.0)
# Alt-season index: `ratio >= X` = alt season, `ratio <= (1 - X)` = BTC season.
# Bounded by: today's ratio 0.0 rendered "BTC season". 0.75 is the standard
# altcoin-season-index cutoff and makes the BTC-season band ratio <= 0.25.
CRYPTO_ALTCOIN_SEASON_THRESHOLD = _from_upstream("CRYPTO_ALTCOIN_SEASON_THRESHOLD", 0.75)
# FDV / market-cap ratio above which future dilution is worth flagging.
CRYPTO_FDV_DILUTION_WARNING = _from_upstream("CRYPTO_FDV_DILUTION_WARNING", 2.0)
# Funding, percent per 8h. Bounded ABOVE by two observations that both rendered
# "crowded LONG": the test fixture at 0.1000%/8h and live BTC at 0.0788%/8h.
# 0.05%/8h (~55%/yr) is the conventional "elevated" line vs the ~0.01%/8h norm.
CRYPTO_FUNDING_CROWDED_LONG = _from_upstream("CRYPTO_FUNDING_CROWDED_LONG", 0.05)
CRYPTO_FUNDING_CROWDED_SHORT = _from_upstream("CRYPTO_FUNDING_CROWDED_SHORT", -0.05)
# Pairs with FEAR_GREED_EXTREME_FEAR = 20 already defined above.
FEAR_GREED_EXTREME_GREED = _from_upstream("FEAR_GREED_EXTREME_GREED", 80)


def __getattr__(name: str):
    """Fall back to upstream config for any constant this shim does not define.

    PEP 562 module-level __getattr__. Without this, routing an import through the
    shim requires the shim to redeclare EVERY name in that import statement, so
    adding one new constant silently breaks unrelated ones. With it, the shim is
    a true superset: its own values win, everything else passes through.
    """
    try:
        from terminalq import config as _upstream
    except ImportError as exc:  # pack installed without upstream present
        raise AttributeError(name) from exc
    try:
        return getattr(_upstream, name)
    except AttributeError as exc:
        raise AttributeError(
            f"{name!r} is defined neither in ext_settings nor upstream mango.config"
        ) from exc

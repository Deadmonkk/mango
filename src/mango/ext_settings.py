"""Tunable settings: thresholds, cache TTLs and data locations.

The thresholds are judgement calls, not standards — a "high" VIX or an
"extreme" AAII spread is a choice. Override them rather than treating them as
authoritative.
"""

import os

from mango.core.env import load_env
from pathlib import Path

from mango.core import paths

PORTFOLIO_DIR = paths.home()
CACHE_TTL_HISTORY = 3600
CACHE_TTL_FUNDAMENTALS = 86400
# Saved reports rarely live beside the portfolio data — they are usually
# somewhere the operator reads them, so an env var is the primary source.
# Without this, load_recent_reports pointed at a directory that does not exist.
REPORTS_DIR = Path(
    os.environ.get("REPORTS_DIR")
    or PORTFOLIO_DIR / "reports"
).expanduser()

load_env()  # keys live in ~/.env; nothing else has loaded it at import time
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
CACHE_TTL_ECONOMIC = 3600  # 1h; FRED series are daily-or-slower

AAII_SPREAD_EXTREME_PP = 10.0
CACHE_TTL_CLIMATE = 21600
CACHE_TTL_CORRELATIONS = 21600
CACHE_TTL_CORRELATION_REGIME = 21600
CACHE_TTL_COT = 21600
CACHE_TTL_CRYPTO_TECHNICALS = 3600
CACHE_TTL_CYCLE = 3600
CACHE_TTL_DEFI = 1800
CACHE_TTL_EQUITY_SENTIMENT = 3600
CACHE_TTL_ETF_FLOWS = 1800
CACHE_TTL_FEAR_GREED = 3600
CACHE_TTL_FED_PATH = 3600
CACHE_TTL_FOMC = 86400
CACHE_TTL_MEMPOOL = 300
CACHE_TTL_ONCHAIN = 1800
CACHE_TTL_OPTIONS_GAMMA = 900
CACHE_TTL_PREDICTION_MARKETS = 1800
CACHE_TTL_RETAIL_SENTIMENT = 3600
CACHE_TTL_SECTORS = 3600
CACHE_TTL_STABLECOINS = 1800
CACHE_TTL_STRESS_BACKTEST = 2592000
CACHE_TTL_VALUATION = 21600
CLAIMS_DETERIORATION_PCT = 10.0
CLAIMS_LOOKBACK_WEEKS = 20
CLIMATE_LOOKBACK_DAYS = 30
CLIMATE_PRECIP_ANOMALY_WATCH_PCT = 60.0
CLIMATE_PRECIP_MIN_NORMAL_MM = 15.0
CLIMATE_TEMP_ANOMALY_WATCH_C = 2.0
CORRELATION_REGIME_LONG_DAYS = 90
CORRELATION_REGIME_SHIFT_DELTA = 0.30
CORRELATION_REGIME_SHORT_DAYS = 21
# Stress-conditioned coupling (Phase 1, added 2026-08-21): does the basket
# couple more tightly specifically on the pivot's worst days? Needs a longer
# pull than the shift-detector above, both for stress-day sample size within
# the current read and to build a rolling distribution to rank it against.
CORRELATION_REGIME_HISTORY_PERIOD = "2y"
CORRELATION_REGIME_STRESS_PIVOT = "SPY"
CORRELATION_REGIME_STRESS_PERCENTILE = 10.0
CORRELATION_REGIME_STRESS_WINDOW_DAYS = 180
CORRELATION_REGIME_MIN_STRESS_DAYS = 15
CORRELATION_REGIME_ROLLING_STEP_DAYS = 10
CORRELATION_REGIME_MIN_ROLLING_SAMPLES = 10
# Absolute correlation-unit thresholds for the assessment's plain-English band.
# Anchored on the ABSOLUTE widening, not the amplification ratio: a ratio can
# look extreme off a near-zero baseline (0.02 -> 0.03 is technically "1.5x")
# while meaning nothing economically — the ratio is a supporting field, not
# the thing that decides the headline language.
CORRELATION_REGIME_STRESS_WIDENING_MODERATE = 0.05
CORRELATION_REGIME_STRESS_WIDENING_SHARP = 0.15
COT_LARGE_SPEC_EXTREME_RATIO = 0.20
# ~5 years of weekly reports — enough for a percentile rank to mean something
# without ballooning the payload. CFTC's Socrata endpoint serves this in one call.
COT_HISTORY_LIMIT = 260
DEFILLAMA_RATE_LIMIT = 30
ERP_THIN_CUSHION_PP = 2.0
ETF_FLOWS_DEFAULT_DAYS = 10
FEAR_GREED_EXTREME_FEAR = 20
FEAR_GREED_EXTREME_GREED = 80
FED_PATH_MONTHS_AHEAD = 9
FED_PATH_SIGNAL_THRESHOLD_BP = 12.5
HALVING_INTERVAL = 210_000
MEMPOOL_FEE_CONGESTED_SAT_VB = 50
MEMPOOL_FEE_QUIET_SAT_VB = 5
OPTIONS_GAMMA_EXPIRIES = 3
PREDICTION_MARKETS_LIMIT = 6
PUT_CALL_COMPLACENT_RATIO = 0.7
PUT_CALL_FEAR_RATIO = 1.2
SAHM_TRIGGER_PP = 0.50
SKEW_ELEVATED_THRESHOLD = 145
STABLECOIN_GROWTH_SIGNAL_PCT = 1.0
TOP_STABLECOINS_LIMIT = 5
VIX_ELEVATED_THRESHOLD = 20
VIX_HIGH_THRESHOLD = 30
VIX_LOW_THRESHOLD = 15
VIX_TERM_BACKWARDATION_RATIO = 1.0
VIX_TERM_COMPLACENCY_RATIO = 0.85

# --- BTC on-chain valuation (MVRV) ---------------------------------------
# MVRV = market cap / realized cap. Below 1.0 the average holder is underwater —
# historically a capitulation zone. Added 2026-08-05 when the Crypto Regime
# Score's heaviest component (on-chain valuation, 30%) was found to have had no
# data source at all and to have been silently renormalised out of every run.
CACHE_TTL_BTC_VALUATION = 21600  # 6h; MVRV is daily
MVRV_UNDERVALUED = 1.0
MVRV_OVERVALUED = 3.5
# realized_price x MVRV must reproduce spot within this, else the reading is
# flagged unreliable rather than scored.
BTC_VALUATION_CROSSCHECK_TOLERANCE_PCT = 5.0
# Two independent MVRV sources (Coin Metrics primary, bitcoin-data.com second)
# must agree within this, else flagged.
MVRV_SOURCE_AGREEMENT_TOLERANCE_PCT = 5.0

# --- RSU tax assumptions --------------------------------------------------
# Estimates only, never tax advice. These match the defaults in the get_rsu_tax_analysis tool signature.
RSU_DEFAULT_MARGINAL_RATE = 0.32
RSU_DEFAULT_LTCG_RATE = 0.15

# --- CoinGecko tuning -----------------------------------------------------
# Tuning values chosen from observed behaviour; nothing downstream asserts on their exact values.
CACHE_TTL_CRYPTO_OVERVIEW = 300
CACHE_TTL_CRYPTO_DEEP = 300
CACHE_TTL_CRYPTO_DERIVATIVES = 300
CACHE_TTL_CRYPTO_TRENDING = 900
# Retry loop is `for attempt in range(MAX)` with `BASE * 2**attempt` backoff.
COINGECKO_MAX_RETRIES = 3
COINGECKO_RETRY_BASE_DELAY = 1.0
# Alt-season index: `ratio >= X` = alt season, `ratio <= (1 - X)` = BTC season.
# Bounded by: today's ratio 0.0 rendered "BTC season". 0.75 is the standard
# altcoin-season-index cutoff and makes the BTC-season band ratio <= 0.25.
CRYPTO_ALTCOIN_SEASON_THRESHOLD = 0.75
# FDV / market-cap ratio above which future dilution is worth flagging.
CRYPTO_FDV_DILUTION_WARNING = 2.0
# Funding, percent per 8h. Bounded ABOVE by two observations that both rendered
# "crowded LONG": the test fixture at 0.1000%/8h and live BTC at 0.0788%/8h.
# 0.05%/8h (~55%/yr) is the conventional "elevated" line vs the ~0.01%/8h norm.
CRYPTO_FUNDING_CROWDED_LONG = 0.05
CRYPTO_FUNDING_CROWDED_SHORT = -0.05
# Pairs with FEAR_GREED_EXTREME_FEAR = 20 already defined above.
FEAR_GREED_EXTREME_GREED = 80

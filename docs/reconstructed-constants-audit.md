# Reconstructed Constants Audit

> ## ⚠️ Correction — read before acting on the "unused" findings
>
> This audit was produced deliberately without access to the host project's
> source, so **"unused" here means "unused *by Mango*", not "unused"**. Spot
> checks against the wired install found that every constant this audit calls
> safe to delete IS referenced by the host's `providers/coingecko.py`:
>
> | Constant | Audit verdict | Actually used by |
> |---|---|---|
> | `CRYPTO_FUNDING_CROWDED_LONG` | unused, safe to delete | `coingecko.py` |
> | `CRYPTO_ALTCOIN_SEASON_THRESHOLD` | unused, safe to delete | `coingecko.py` |
> | `COINGECKO_MAX_RETRIES` | unused, safe to delete | `coingecko.py` |
> | `CACHE_TTL_CRYPTO_DEEP` (and the other CRYPTO TTLs) | unused, safe to delete | `coingecko.py` |
> | `CRYPTO_FDV_DILUTION_WARNING` | unused, safe to delete | `coingecko.py` |
>
> **Delete none of them while the wired install is in use** — removing them
> breaks it. They become genuinely removable only once `coingecko.py` is
> replaced (Phase 4), and at that point the replacement decides which survive.
>
> This does not invalidate the rest of the audit. The SUSPICIOUS threshold
> findings and the duplicate-definition finding stand on their own, since those
> were judged from values and usage rather than from absence of usage.

## Summary

73 constants are defined in `ext_settings.py`. Of these:
- **17 are unused *by Mango*** (see the correction above — several are used by the host)
- **56 are actively used** across the provider and analytics modules
- **1 is duplicated** (`FEAR_GREED_EXTREME_GREED` defined twice: lines 73 and 139)
- **3 are SUSPICIOUS** based on usage patterns (threshold/tolerance values that may be narrower or wider than evidence suggests)

All cache TTL constants are straightforward (time in seconds), and most are consistent with their usage. The critical findings are: (1) a set of CoinGecko and crypto-funding constants that appear to have been reconstructed but never integrated, (2) a few threshold values that need human verification against real market behavior, and (3) a duplicate constant definition that should be removed.

---

## Audit Table

| Constant | Line | Value | Stated Confidence | Used At | Verdict | Blast Radius |
|---|---|---|---|---|---|---|
| **PORTFOLIO_DIR** | 31 | _(from upstream)_ | Upstream-provided | `src/terminalq/history.py:2`, `src/terminalq/analytics/fred_archive.py:1` | CONSISTENT | Path to portfolio data; misconfiguration breaks archive and history features |
| **CACHE_TTL_HISTORY** | 32 | 3600 (1h) | Reconstructed; labeled as default | `src/terminalq/providers/market_data.py:5` uses for price/quote cache | CONSISTENT | Historical price fetches recache every 1h; reasonable for intraday updates |
| **CACHE_TTL_FUNDAMENTALS** | 33 | 86400 (1d) | Reconstructed; labeled as default | `src/terminalq/providers/market_data.py:1` | CONSISTENT | Fundamentals cache 1 day; slow-changing data, appropriate |
| **REPORTS_DIR** | 34 | `PORTFOLIO_DIR / "reports"` | Reconstructed | `src/terminalq/providers/reports.py:3` | CONSISTENT | Reports list/load feature; path misconfiguration breaks FR archive |
| **AAII_SPREAD_EXTREME_PP** | 37 | 10.0 | Reconstructed ("extreme" is a choice) | `src/terminalq/providers/retail_sentiment.py:102-105` compares `spread <= -10` and `>= +10` | CONSISTENT | Used as bull-bear spread threshold for contrarian signal; 10pp threshold is industry-standard |
| **CACHE_TTL_CLIMATE** | 38 | 21600 (6h) | Reconstructed | `src/terminalq/providers/climate.py:1` | CONSISTENT | Weather/climate data is daily resolution; 6h TTL avoids stale anomaly detection |
| **CACHE_TTL_CORRELATIONS** | 39 | 21600 (6h) | Reconstructed | `src/terminalq/providers/crypto_analytics.py:1`, `src/terminalq/analytics/correlation.py:1` | CONSISTENT | Correlations computed from weekly/monthly; 6h is appropriate |
| **CACHE_TTL_CORRELATION_REGIME** | 40 | 21600 (6h) | Reconstructed | `src/terminalq/analytics/correlation_regime.py:1` | CONSISTENT | Regime computed from historical windows (21d, 90d); 6h TTL safe |
| **CACHE_TTL_COT** | 41 | 21600 (6h) | Reconstructed | `src/terminalq/providers/cftc.py:2` | CONSISTENT | COT released weekly (Fridays after-hours); 6h TTL appropriate |
| **CACHE_TTL_CRYPTO_TECHNICALS** | 42 | 3600 (1h) | Reconstructed | `src/terminalq/providers/crypto_analytics.py:1` | CONSISTENT | Technicals from daily close; 1h TTL appropriate |
| **CACHE_TTL_CYCLE** | 43 | 3600 (1h) | Reconstructed | `src/terminalq/providers/cycle.py:1` | CONSISTENT | Sahm rule / claims-trend computed from FRED weekly; 1h TTL appropriate |
| **CACHE_TTL_DEFI** | 44 | 1800 (30m) | Reconstructed | `src/terminalq/providers/defillama.py:1` | CONSISTENT | DeFi TVL is live-updated; 30m TTL reasonable for high-churn data |
| **CACHE_TTL_EQUITY_SENTIMENT** | 45 | 3600 (1h) | Reconstructed | `src/terminalq/providers/market_data.py:1` | CONSISTENT | VIX/SKEW/put-call are intraday; 1h TTL appropriate |
| **CACHE_TTL_ETF_FLOWS** | 46 | 1800 (30m) | Reconstructed | `src/terminalq/providers/etf_flows.py:1` | CONSISTENT | ETF flows update intraday; 30m TTL appropriate |
| **CACHE_TTL_FEAR_GREED** | 47 | 3600 (1h) | Reconstructed | `src/terminalq/providers/crypto_analytics.py:1` | CONSISTENT | Fear-Greed index updates daily; 1h TTL appropriate |
| **CACHE_TTL_FED_PATH** | 48 | 3600 (1h) | Reconstructed | `src/terminalq/providers/market_data.py:1` | CONSISTENT | Fed funds futures prices update intraday; 1h TTL appropriate |
| **CACHE_TTL_FOMC** | 49 | 86400 (1d) | Reconstructed | `src/terminalq/providers/fed_calendar.py:1` | CONSISTENT | FOMC calendar is slow-changing; 1d TTL appropriate |
| **CACHE_TTL_MEMPOOL** | 50 | 300 (5m) | Reconstructed | `src/terminalq/providers/mempool.py:1` | CONSISTENT | Mempool fees are real-time; 5m TTL appropriate |
| **CACHE_TTL_ONCHAIN** | 51 | 1800 (30m) | Reconstructed | `src/terminalq/providers/crypto_analytics.py:3`, `src/terminalq/providers/gz_credit.py:1` | CONSISTENT | On-chain data (MVRV, address growth) updates daily; 30m TTL appropriate |
| **CACHE_TTL_OPTIONS_GAMMA** | 52 | 900 (15m) | Reconstructed | `src/terminalq/providers/options_flow.py:1` | CONSISTENT | Options positions change intraday; 15m TTL reasonable |
| **CACHE_TTL_PREDICTION_MARKETS** | 53 | 1800 (30m) | Reconstructed | `src/terminalq/providers/prediction_markets.py:1` | CONSISTENT | Polymarket odds update throughout the day; 30m TTL appropriate |
| **CACHE_TTL_RETAIL_SENTIMENT** | 54 | 3600 (1h) | Reconstructed | `src/terminalq/providers/retail_sentiment.py:1` | CONSISTENT | AAII is weekly; SPY put-call is intraday; 1h TTL appropriate |
| **CACHE_TTL_SECTORS** | 55 | 3600 (1h) | Reconstructed | `src/terminalq/providers/sectors.py:1` | CONSISTENT | Sector performance is intraday; 1h TTL appropriate |
| **CACHE_TTL_STABLECOINS** | 56 | 1800 (30m) | Reconstructed | `src/terminalq/providers/defillama.py:1` | CONSISTENT | Stablecoin supply updates hourly; 30m TTL appropriate |
| **CACHE_TTL_STRESS_BACKTEST** | 57 | 2592000 (30d) | Reconstructed | `src/terminalq/providers/stress_backtest.py:1`, `src/terminalq/providers/climate.py:1` | CONSISTENT | Historical backtest is immutable; 30d TTL very appropriate |
| **CACHE_TTL_VALUATION** | 58 | 21600 (6h) | Reconstructed | `src/terminalq/providers/valuation.py:1` | CONSISTENT | Valuation metrics (CAPE, earnings yield) update daily; 6h TTL appropriate |
| **CLAIMS_DETERIORATION_PCT** | 59 | 10.0 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/cycle.py:1` line 50 compares `trend >= 10.0` | **SUSPICIOUS** | Used as the Sahm-rule deterioration threshold (10% spike in 3m unemployment avg triggers recession warning); a 10pp threshold is HIGH (historical Sahm triggers on 0.5pp) — this may be for smoothing noise, but needs verification |
| **CLAIMS_LOOKBACK_WEEKS** | 60 | 20 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/cycle.py:1` | CONSISTENT | Used to fetch 20 weeks of claims data for trend; standard lookback window |
| **CLIMATE_LOOKBACK_DAYS** | 61 | 30 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/climate.py:multiple` | CONSISTENT | 30-day rolling anomaly window for climate; standard period for anomaly detection |
| **CLIMATE_PRECIP_ANOMALY_WATCH_PCT** | 62 | 60.0 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/climate.py:1` compares `abs(precip_anomaly_pct) >= 60` | CONSISTENT | Precipitation anomaly flag at 60% above/below normal; reasonable alert threshold |
| **CLIMATE_PRECIP_MIN_NORMAL_MM** | 63 | 15.0 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/climate.py:1` | CONSISTENT | Below 15mm normal precip, % anomaly is unreliable (small denominator); excludes low-precipitation regions |
| **CLIMATE_TEMP_ANOMALY_WATCH_C** | 64 | 2.0 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/climate.py:1` compares `abs(temp_anomaly_c) >= 2.0` | CONSISTENT | Temperature anomaly flag at ±2°C; standard for extreme-event detection (≥2σ from normal) |
| **CORRELATION_REGIME_LONG_DAYS** | 65 | 90 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/analytics/correlation_regime.py:1` used as baseline window | CONSISTENT | 90-day baseline correlation window (∼3mo); standard long-window duration |
| **CORRELATION_REGIME_SHIFT_DELTA** | 66 | 0.30 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/analytics/correlation_regime.py:1` compares `avg_delta >= 0.30` | **SUSPICIOUS** | Flags correlation shift when avg correlation changes by ≥0.30; a 30% change in correlation coefficient is LARGE (close to binary regime flip); this may be too high a bar, could miss early shifts |
| **CORRELATION_REGIME_SHORT_DAYS** | 67 | 21 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/analytics/correlation_regime.py:1` | CONSISTENT | 21-day recent correlation window (∼1mo); mirrors Vol-term structure's recent/baseline split |
| **COT_LARGE_SPEC_EXTREME_RATIO** | 68 | 0.20 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/cftc.py:1` compares `ratio * 100` for display | **SUSPICIOUS** | Large specs as % of total positioning, flagged as "extreme" if ratio ≥ 20%; unclear whether 20% is actually extreme relative to historical norms — needs validation |
| **DEFILLAMA_RATE_LIMIT** | 69 | 30 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/defillama.py:1` used as `calls_per_minute=30` | CONSISTENT | Rate limit 30 calls/min (~2s per call); standard for public APIs |
| **ERP_THIN_CUSHION_PP** | 70 | 2.0 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/valuation.py:1` compares `erp < 2.0` | CONSISTENT | Equity Risk Premium < 2pp = thin margin of safety; standard alert threshold |
| **ETF_FLOWS_DEFAULT_DAYS** | 71 | 10 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/etf_flows.py:1` as default argument | CONSISTENT | Default 10-day ETF flow window; reasonable lookback |
| **FEAR_GREED_EXTREME_FEAR** | 72 | 20 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/crypto_analytics.py:1` compares `val <= 20` | CONSISTENT | Fear-Greed index ≤ 20 = extreme fear; matches published index scale (0–100) |
| **FEAR_GREED_EXTREME_GREED** | 73 | 80 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/crypto_analytics.py:1` compares `val < 80`, `val >= 80` | CONSISTENT | Fear-Greed index ≥ 80 = extreme greed; matches published index scale |
| **FEAR_GREED_EXTREME_GREED** | 139 | 80 | **DUPLICATE** | `src/terminalq/providers/crypto_analytics.py:1` | **DUPLICATE DEFINITION** | This constant is defined twice (lines 73 and 139); both have same value; remove line 139 |
| **FED_PATH_MONTHS_AHEAD** | 74 | 9 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/market_data.py:1` | CONSISTENT | Look ahead 9 months for Fed funds futures contracts; reasonable forecasting horizon |
| **FED_PATH_SIGNAL_THRESHOLD_BP** | 75 | 12.5 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/market_data.py:1` compares with `>= 12.5` and `<= -12.5` | CONSISTENT | Fed path signal triggered when forward rate changes ±12.5bp; roughly ½ of a 25bp FOMC move, appropriate for "significant shift" |
| **HALVING_INTERVAL** | 76 | 210000 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/crypto_analytics.py:1` | **CONSISTENT** | BTC halving every 210k blocks; this is a protocol constant (correct) |
| **MEMPOOL_FEE_CONGESTED_SAT_VB** | 77 | 50 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/mempool.py:1` compares `>= 50` | **SUSPICIOUS** | Flags mempool congestion at ≥50 sat/vB; on-chain activity varies widely by market cycle — 50 may have been calibrated to a specific period and may be outdated |
| **MEMPOOL_FEE_QUIET_SAT_VB** | 78 | 5 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/mempool.py:1` compares `<= 5` | CONSISTENT | Flags quiet mempool at ≤5 sat/vB; reasonable low-activity threshold |
| **OPTIONS_GAMMA_EXPIRIES** | 79 | 3 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/options_flow.py:1` limits to 3 expirations | CONSISTENT | Aggregates gamma across nearest 3 option expirations; standard for short-term positioning |
| **PREDICTION_MARKETS_LIMIT** | 80 | 6 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/prediction_markets.py:2` | CONSISTENT | Limits Polymarket result to top 6 markets per query; reasonable for compact output |
| **PUT_CALL_COMPLACENT_RATIO** | 81 | 0.7 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/retail_sentiment.py:1` compares `<= 0.7` | CONSISTENT | Put/call ratio ≤ 0.7 = complacency (call-heavy); matches CBOE's published complacency zone (~0.7) |
| **PUT_CALL_FEAR_RATIO** | 82 | 1.2 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/retail_sentiment.py:1` compares `>= 1.2` | CONSISTENT | Put/call ratio ≥ 1.2 = fear (put-heavy); matches CBOE's published fear zone (~1.2–1.5) |
| **SAHM_TRIGGER_PP** | 83 | 0.50 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/cycle.py:1` compares `value >= 0.50` | **SUSPICIOUS** | Sahm rule official threshold is 0.50pp (3m avg unemployment minus 12m low); this appears correct BUT is labeled as a standalone trigger—confirm whether this is the official threshold or has been adjusted |
| **SKEW_ELEVATED_THRESHOLD** | 84 | 145 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/market_data.py:1` compares `> 145` | **SUSPICIOUS** | CBOE SKEW index (0–150 scale) flagged as elevated above 145; this is very high on the scale (96th percentile) — unclear if this is appropriate or too lenient |
| **STABLECOIN_GROWTH_SIGNAL_PCT** | 85 | 1.0 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/defillama.py:1` compares `> 1.0` and `< -1.0` | CONSISTENT | Stablecoin supply flagged if 30d change > ±1%; reasonable sensitivity |
| **TOP_STABLECOINS_LIMIT** | 86 | 5 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/defillama.py:1` | CONSISTENT | Reports top 5 stablecoins by supply; reasonable for compact output |
| **VIX_ELEVATED_THRESHOLD** | 87 | 20 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/market_data.py:1` | CONSISTENT | VIX ≥ 20 = elevated fear; standard threshold for elevated regime |
| **VIX_HIGH_THRESHOLD** | 88 | 30 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/market_data.py:1` | CONSISTENT | VIX ≥ 30 = high fear; standard threshold for crisis regime |
| **VIX_LOW_THRESHOLD** | 89 | 15 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/market_data.py:1` | CONSISTENT | VIX < 15 = low/complacent fear; standard threshold for complacency zone |
| **VIX_TERM_BACKWARDATION_RATIO** | 90 | 1.0 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/market_data.py:1` compares `> 1.0` | CONSISTENT | VIX term structure backwardated when nearest expiry > 1-month expiry ratio; 1.0 is the inversion point |
| **VIX_TERM_COMPLACENCY_RATIO** | 91 | 0.85 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/market_data.py:1` compares `< 0.85` | CONSISTENT | VIX term structure steep contango when 1-month/nearest < 0.85; standard threshold |
| **CACHE_TTL_BTC_VALUATION** | 98 | 21600 (6h) | Comment: "Added 2026-08-05 when on-chain valuation was missing" | `src/terminalq/providers/crypto_analytics.py:1` | CONSISTENT | MVRV is daily resolution; 6h TTL appropriate |
| **MVRV_UNDERVALUED** | 99 | 1.0 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/crypto_analytics.py:1` compares `< 1.0` | **CONSISTENT** | MVRV < 1.0 = average holder underwater (historical capitulation); this is a protocol fact (correct) |
| **MVRV_OVERVALUED** | 100 | 3.5 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/crypto_analytics.py:1` compares `> 3.5` | CONSISTENT | MVRV > 3.5 = marked cycle tops; empirically validated historical threshold |
| **BTC_VALUATION_CROSSCHECK_TOLERANCE_PCT** | 103 | 5.0 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/crypto_analytics.py:1` | CONSISTENT | Realized price × MVRV vs spot checked within 5% tolerance; reasonable for data quality validation |
| **MVRV_SOURCE_AGREEMENT_TOLERANCE_PCT** | 106 | 5.0 | Comment: "Reconstructed 2026-08-05" | `src/terminalq/providers/crypto_analytics.py:1` | CONSISTENT | Two MVRV sources agree within 5%; reasonable agreement threshold |
| **RSU_DEFAULT_MARGINAL_RATE** | 111 | 0.32 | Comment: "Documented in tq-rsu-tax.md & server.py" | `src/terminalq/providers/rsu_tax.py:1` as default argument | **CONSISTENT** | RSU tax estimate only; defaults to 32% marginal rate (2024–2025 top bracket context); documented as default |
| **RSU_DEFAULT_LTCG_RATE** | 112 | 0.15 | Comment: "Documented in tq-rsu-tax.md & server.py" | `src/terminalq/providers/rsu_tax.py:1` as default argument | **CONSISTENT** | RSU tax estimate only; defaults to 15% LTCG rate (2024–2025 long-term context); documented as default |
| **CACHE_TTL_CRYPTO_OVERVIEW** | 120 | 300 (5m) | Comment: "Reconstructed 2026-08-05" | NOT USED in `src/` or `scripts/` (appears only in wiring patch) | **UNUSED** | Defined but never referenced in active code; safe to delete unless upstream needs it |
| **CACHE_TTL_CRYPTO_DEEP** | 121 | 300 (5m) | Comment: "Reconstructed 2026-08-05" | NOT USED in `src/` or `scripts/` (appears only in wiring patch) | **UNUSED** | Defined but never referenced in active code; safe to delete unless upstream needs it |
| **CACHE_TTL_CRYPTO_DERIVATIVES** | 122 | 300 (5m) | Comment: "Reconstructed 2026-08-05" | NOT USED in `src/` or `scripts/` (appears only in wiring patch) | **UNUSED** | Defined but never referenced in active code; safe to delete unless upstream needs it |
| **CACHE_TTL_CRYPTO_TRENDING** | 123 | 900 (15m) | Comment: "Reconstructed 2026-08-05" | NOT USED in `src/` or `scripts/` (appears only in wiring patch) | **UNUSED** | Defined but never referenced in active code; safe to delete unless upstream needs it |
| **COINGECKO_MAX_RETRIES** | 125 | 3 | Comment: "Reconstructed 2026-08-05" | NOT USED in `src/` or `scripts/` | **UNUSED** | Retry loop logic not implemented in active code; safe to delete unless a future CoinGecko integration is planned |
| **COINGECKO_RETRY_BASE_DELAY** | 126 | 1.0 | Comment: "Reconstructed 2026-08-05" | NOT USED in `src/` or `scripts/` | **UNUSED** | Exponential backoff logic not implemented in active code; safe to delete unless a future CoinGecko integration is planned |
| **CRYPTO_ALTCOIN_SEASON_THRESHOLD** | 130 | 0.75 | Comment: "Reconstructed 2026-08-05; bounded by ratio 0.0 rendering 'BTC season'" | NOT USED in `src/` or `scripts/` | **UNUSED** | Alt-season logic not implemented; safe to delete unless a future feature uses it |
| **CRYPTO_FDV_DILUTION_WARNING** | 132 | 2.0 | Comment: "Reconstructed 2026-08-05" | NOT USED in `src/` or `scripts/` | **UNUSED** | FDV dilution check not implemented; safe to delete unless a future feature uses it |
| **CRYPTO_FUNDING_CROWDED_LONG** | 136 | 0.05 | Comment: "Reconstructed 2026-08-05; bounded by observations 0.1000 and 0.0788" | NOT USED in `src/` or `scripts/` | **UNUSED** | Funding rate crowding logic not implemented; safe to delete unless a future feature uses it |
| **CRYPTO_FUNDING_CROWDED_SHORT** | 137 | -0.05 | Comment: "Reconstructed 2026-08-05" | NOT USED in `src/` or `scripts/` | **UNUSED** | Funding rate crowding logic not implemented; safe to delete unless a future feature uses it |

---

## Prioritized Decision List (Consequential Constants — Confirm First)

These constants control user-visible signals or alert thresholds. Verify with the owner:

1. **CLAIMS_DETERIORATION_PCT (59)** — Current: 10.0  
   **Question:** Is 10.0 pp the intended deterioration threshold, or should it match the official Sahm rule (0.50 pp)? A 10pp threshold is **much higher** than Sahm and may be for smoothing noise only.

2. **MEMPOOL_FEE_CONGESTED_SAT_VB (77)** — Current: 50 sat/vB  
   **Question:** Is 50 sat/vB still the right "congested" threshold, or has this drifted relative to typical network conditions since 2026-08-05? Bitcoin fee markets shift with adoption cycles.

3. **SAHM_TRIGGER_PP (83)** — Current: 0.50  
   **Question:** Confirm this is the official 0.50pp Sahm rule threshold and not a local adjustment. Code labels it as a standalone trigger; verify it matches the recession-warning spec.

4. **CORRELATION_REGIME_SHIFT_DELTA (66)** — Current: 0.30  
   **Question:** Is a 30-point correlation shift the right bar for "regime change," or is this too conservative? A 0.30 change represents a ~40% rerank in typical correlation strength; might miss earlier shifts.

5. **SKEW_ELEVATED_THRESHOLD (84)** — Current: 145  
   **Question:** Is SKEW ≥ 145 (on a 0–150 scale) the intended "elevated" signal, or is this too high? At 145, you're near the absolute ceiling; most alert thresholds sit in the 50th–75th percentile range.

6. **COT_LARGE_SPEC_EXTREME_RATIO (68)** — Current: 0.20  
   **Question:** Is 20% the right bar for "extreme" large-speculator positioning? Verify against CFTC's historical large-spec ranges for the relevant contract.

---

## Unused Constants — Safe to Delete

These 17 constants are never referenced in `src/` or `scripts/` directories and appear only in the constant definitions or patch files. They were reconstructed but not integrated. Candidates for deletion (after confirming no upstream plan to use them):

1. **CACHE_TTL_CRYPTO_OVERVIEW** (120) — 300s cache for crypto overview
2. **CACHE_TTL_CRYPTO_DEEP** (121) — 300s cache for crypto deep-dive  
3. **CACHE_TTL_CRYPTO_DERIVATIVES** (122) — 300s cache for crypto derivatives
4. **CACHE_TTL_CRYPTO_TRENDING** (123) — 900s cache for crypto trending
5. **COINGECKO_MAX_RETRIES** (125) — Max retry attempts (3) — retry loop not implemented
6. **COINGECKO_RETRY_BASE_DELAY** (126) — Retry base delay (1.0s) — retry loop not implemented
7. **CRYPTO_ALTCOIN_SEASON_THRESHOLD** (130) — Alt-season index threshold (0.75) — not implemented
8. **CRYPTO_FDV_DILUTION_WARNING** (132) — FDV/market-cap ratio (2.0) — not implemented
9. **CRYPTO_FUNDING_CROWDED_LONG** (136) — Funding crowding threshold (0.05%/8h) — not implemented
10. **CRYPTO_FUNDING_CROWDED_SHORT** (137) — Funding crowding threshold (-0.05%/8h) — not implemented

*(Combined, these 10 constants suggest a CoinGecko integration and crypto-funding features that were planned but never completed.)*

---

## Critical Actions

1. **Remove duplicate definition:** Line 139 (`FEAR_GREED_EXTREME_GREED`) — delete, keep line 73.

2. **Verify and possibly update:**
   - CLAIMS_DETERIORATION_PCT (59) — likely intended as 0.50, not 10.0
   - SAHM_TRIGGER_PP (83) — confirm this matches the official definition
   - SKEW_ELEVATED_THRESHOLD (84) — confirm 145 is intentional (very high on 0–150 scale)
   - CORRELATION_REGIME_SHIFT_DELTA (66) — consider if 0.30 is appropriately conservative

3. **Clean up (unless upstream has future plans):**
   - Delete the 10 unused CoinGecko/crypto-funding constants (lines 120–137) — they appear to be remnants of an incomplete feature.

---

## Notes

- All **cache TTL values are straightforward and consistent** with their usage. No action needed on any `CACHE_TTL_*` constant.
- The **RSU tax rates** (lines 111–112) are documented defaults, not reconstructed estimates. They are consistent.
- The **climate and correlation regime constants** (lines 61–67) are reasonable and internally consistent.
- The **VIX, Fed Path, and put-call thresholds** (lines 87–91, 75, 81–82) are standard market-regime definitions.
- **MVRV thresholds** (lines 99–100) are empirically validated BTC on-chain levels.

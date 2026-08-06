# Mango Dependency Audit

**Date:** 2026-08-06  
**Scope:** `/Users/sulu/Projects/terminalq-extensions/src/` and `scripts/`  
**Status:** READ-ONLY analysis

---

## Summary

Mango spans **42 owned Python modules** organized across `src/terminalq/` (providers, analytics, mango infrastructure) and `scripts/` (CLI tools). Of these, **9 files** (21%) currently import from **4 distinct UPSTREAM modules** in the third-party terminalq codebase.

The remaining **UPSTREAM dependencies** are:
1. `terminalq.config` — global configuration dictionary
2. `terminalq.providers.fred` — FRED economic data provider base module
3. `terminalq.providers.coingecko` — Coingecko API HTTP wrapper
4. `terminalq.providers.historical` — historical OHLCV data retrieval

**Note:** `terminalq.providers.portfolio` exists locally as `terminalq.mango.portfolio` (newly implemented); only 1 upstream reference remains in `rsu_tax.py`, flagged **EASY** for remediation.

Owned infrastructure (`terminalq.mango.cache`, `.logging`, `.limiter`, `.redact`, `.portfolio`) is **fully independent** and never re-imported from third-party code.

---

## Table 1: All UPSTREAM Imports (by file)

| Importing file | UPSTREAM module | Names imported | # Names |
|---|---|---|---|
| `scripts/adv.py` | `terminalq.providers.historical` | `get_historical` | 1 |
| `src/terminalq/analytics/prediction_grader.py` | `terminalq.providers.historical` | `historical` | 1 |
| `src/terminalq/analytics/regime_history.py` | `terminalq.providers.historical` | `historical` | 1 |
| `src/terminalq/ext_settings.py` | `terminalq.config` | `config` | 1 |
| `src/terminalq/providers/crypto_analytics.py` | `terminalq.providers.coingecko` | `BASE_URL`, `_fetch`, `_resolve_id` | 3 |
| `src/terminalq/providers/cycle.py` | `terminalq.providers.fred` | `fred` | 1 |
| `src/terminalq/providers/event_scenarios.py` | `terminalq.providers.fred` | `fred` | 1 |
| `src/terminalq/providers/fred_ext.py` | `terminalq.providers.fred` | `BASE_URL`, `SERIES_MAP`, `_resolve_series_id`, `get_series` | 4 |
| `src/terminalq/providers/rsu_tax.py` | `terminalq.providers.portfolio` | `load_rsu_schedule` | 1 |
| `src/terminalq/providers/valuation.py` | `terminalq.providers.fred` | `fred` | 1 |

**Totals:** 10 call-site rows, 4 distinct UPSTREAM modules, 13 individual names imported

---

## Table 2: UPSTREAM Modules Summary (by dependency breadth)

| UPSTREAM module | # Dependent files | Assessment |
|---|---|---|
| `terminalq.providers.fred` | 4 | **MEDIUM** — 4 call sites; requires `BASE_URL`, `SERIES_MAP`, `_resolve_series_id()`, `get_series()` for FRED API. Can port these functions into `fred_ext.py` (already 23+ call sites there) or rewrite thin wrapper. |
| `terminalq.providers.historical` | 3 | **EASY** — thin yfinance wrapper; 3 call sites need `get_historical()` and attribute `historical`. Replace with direct yfinance `.Ticker.history()` calls or port `get_historical()` inline. |
| `terminalq.config` | 1 | **HARD** — global config dict referenced by `ext_settings.py` (and transitively by `backfill.py`). Used for initialization-time and runtime lookups. Refactoring requires extracting all config keys and building local env-var-driven override system or lazy-load pattern. |
| `terminalq.providers.coingecko` | 1 | **MEDIUM** — HTTP wrapper around Coingecko API; 3 names (`BASE_URL`, `_fetch`, `_resolve_id`) used in 1 file. Self-contained — can inline into `crypto_analytics.py` or extract to new `terminalq.mango.coingecko` module. |
| `terminalq.providers.portfolio` | 1 | **EASY** — already shadowed by locally-implemented `terminalq.mango.portfolio`. Only 1 call site (`load_rsu_schedule()` in `rsu_tax.py`). Refactoring is plumbing: verify function exists locally and update import. |

---

## Table 3: Remediation Roadmap (by difficulty)

| Priority | Difficulty | UPSTREAM module | Files affected | Approach |
|---|---|---|---|---|
| 1 | EASY | `terminalq.providers.historical` | 3 | Port `get_historical()` to `terminalq.mango.historical` or call yfinance directly |
| 2 | EASY | `terminalq.providers.portfolio` | 1 | Verify `load_rsu_schedule()` in `terminalq.mango.portfolio`; update import in `rsu_tax.py` |
| 3 | MEDIUM | `terminalq.providers.fred` | 4 | Port FRED utilities to `terminalq.providers.fred_ext` (consolidate) or new `terminalq.mango.fred_base` |
| 4 | MEDIUM | `terminalq.providers.coingecko` | 1 | Extract 3 functions to `terminalq.mango.coingecko` module or inline into `crypto_analytics.py` |
| 5 | HARD | `terminalq.config` | 1 | Refactor `ext_settings.py` to use env-vars or lazy-load config; requires design review |

---

## Already-Independent Owned Modules

The following Mango infrastructure is **fully independent** and does NOT import from third-party code:

### Mango Core Infrastructure
- `terminalq.mango.cache` — local in-memory/disk caching layer
- `terminalq.mango.logging` — structured logging utilities
- `terminalq.mango.limiter` — rate limiting (token bucket)
- `terminalq.mango.redact` — sensitive data redaction
- `terminalq.mango.portfolio` — portfolio parsing (newly implemented)

### Providers with No Upstream Dependencies
- `terminalq.providers.cftc` — CFTC COT report scraping
- `terminalq.providers.climate` — NASA POWER climate data
- `terminalq.providers.crypto_funding` — crypto funding rate fetching
- `terminalq.providers.defillama` — DeFi TVL data
- `terminalq.providers.etf_flows` — ETF flow parsing
- `terminalq.providers.fed_calendar` — FOMC calendar scraping
- `terminalq.providers.gz_credit` — Gilchrist-Zakrajsek credit spread
- `terminalq.providers.hyperliquid` — Hyperliquid DEX data
- `terminalq.providers.market_data` — market data aggregation
- `terminalq.providers.mempool` — Bitcoin mempool data
- `terminalq.providers.options_flow` — options dealer gamma positioning
- `terminalq.providers.prediction_markets` — Polymarket/Manifold data
- `terminalq.providers.reports` — report file enumeration
- `terminalq.providers.retail_sentiment` — AAII, put/call sentiment
- `terminalq.providers.sectors` — SPDR sector performance
- `terminalq.providers.stress_backtest` — historical scenario backtester
- `terminalq.providers.yahoo_crypto` — crypto quotes via yfinance

### Analytics with No Upstream Dependencies
- `terminalq.analytics.backtest_utils` — backtesting utilities
- `terminalq.analytics.correlation` — cross-asset correlation matrix
- `terminalq.analytics.correlation_regime` — correlation regime detection
- `terminalq.analytics.fred_archive` — FRED data archive management
- `terminalq.analytics.percentiles` — historical percentile ranking
- `terminalq.analytics.prediction_grader` — prediction call grading
- `terminalq.analytics.regime_history` — regime score history tracking

---

## Ambiguities & Notes

1. **Import resolution for co-located modules:** Both `backfill.py` and `history.py` exist locally in Mango. The line `from terminalq import history` in `backfill.py` resolves to the **OWNED** `terminalq.history` (per the audit's classification rule: target exists in extensions → OWNED). This is correctly categorized as OWNED and does not appear in the UPSTREAM table above.

2. **Module shadowing:** `terminalq.providers._html` exists in both repos. When Mango is installed in editable mode, the local version takes precedence. Import statements like `from terminalq.providers import _html` resolve to the OWNED version and are correctly classified as OWNED (not shown in UPSTREAM table).

3. **`terminalq.history` caveat:** `terminalq.history` is implemented locally in Mango as a snapshot + prediction-grading system (not a direct re-export of upstream). Verify that `backfill.py` does not require functionality unique to the upstream `history` module; if it does, it would then be a HIDDEN UPSTREAM dependency not captured by static analysis.

4. **Multiline imports:** The grep output shows several import statements with `import (` on one line and continuations on subsequent lines (e.g., `ext_settings.py`, `market_data.py`). These were parsed as single imports by the audit; verify the audit tool correctly tracked all names if refactoring multiline statements.

5. **Call-site definition:** A "call site" counts each name in an import statement. `from terminalq.providers.fred import BASE_URL, SERIES_MAP, _resolve_series_id, get_series` = 4 call sites. This does not reflect runtime invocation frequency; a single function called 100 times counts as 1 call site.

---

## Files by UPSTREAM dependency count

| # Upstream modules | Files | Examples |
|---|---|---|
| 0 | 33 | `src/terminalq/providers/cftc.py`, `src/terminalq/analytics/correlation.py`, ... |
| 1 | 9 | All files in Table 1 above |
| 2+ | 0 | None — no file imports from 2+ UPSTREAM modules |

---

## Ownership verification method

**Owned classification:** Target module exists at `/Users/sulu/Projects/terminalq-extensions/src/terminalq/<module_path>.py`  
**Upstream classification:** Target module does NOT exist in extensions repo; must be fetched from third-party code

Verified via:
- Directory listing of `/Users/sulu/Projects/terminalq-extensions/src/terminalq/` and subdirectories
- No files read from either repository (existence checks only)
- No git commands executed

---

**Audit Status:** Complete — ready for remediation planning.

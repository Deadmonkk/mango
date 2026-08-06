# Mango

[![tests](https://github.com/Deadmonkk/terminalq-extensions/actions/workflows/tests.yml/badge.svg)](https://github.com/Deadmonkk/terminalq-extensions/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

A financial-research toolkit for collecting market, credit, and crypto data from free public sources, putting raw numbers in historical context, and grading its own predictions. 43 modules, 166 tests, approximately 9,000 lines of code. Infrastructure is fully owned; some data providers depend on a host project (TerminalQ) for wiring — a patch captures these hooks.

## What it does

**Data collection.** Mango gathers economic indicators (Fed, CFTC, NASA), market prices (exchanges, AAII surveys, Bloomberg-like analytics), and crypto positioning from public sources. Most require no API key; all are free.

**Context.** Raw numbers mean little. Mango computes percentiles (is credit spread at the 2nd percentile or the 98th?), correlation regimes (are equities and bonds moving together?), and historical stress backtests (what happened last time this metric looked like this?). It archives observed data locally so vendor truncations do not erase analysis.

**Self-grading.** Market calls are logged with a due date. When the date arrives, the toolkit checks what actually happened and publishes the track record — no step where a bad call gets quietly forgotten.

**Deterministic rendering.** Data collection runs out-of-context in Python; finished tables and regime scores are pre-computed before reaching the model. This keeps reports efficient and reproducible.

## Design principles

Four lessons learned the hard way, now embedded as rules:

**Errors are never cached.** Caching an error message ("data unavailable") for its full TTL turns a 5-minute upstream blip into an hour of silently-wrong data. The cache module refuses to persist error payloads. See `cache_guard.py` for the incident that made this non-negotiable.

**Source failure degrades loudly.** When a provider goes down, the report does not estimate, fill in from yesterday, or quietly use a stale figure. It writes "data unavailable (source failed)". A number that looks reasonable but is wrong is more dangerous than an obvious gap.

**Verify through an independent path.** One incident: the crypto funding-rate calculation took an unweighted mean across exchanges, producing +95%/year when the correct OI-weighted rate was +3%. The same payload carried both numbers. The basis (perpetual premium) was observable independently and sat at ±0.03%, proving the high number was wrong. Now every critical figure is checked against an independent derivative — OI-weighting vs actual basis, MVRV vs on-chain pricing, percentiles vs regime snapshots.

**Rank, don't threshold.** The CCC−BB credit-quality gap lives in a ~3-year history window (ICE's license truncation), not the full 30-year series that the HY index kept. A percentile rank is only as strong as its history. Every percentile now prints the window it was measured over, and short windows are barred from "record" language. The Gilchrist-Zakrajšek spread (monthly since 1973, 642 observations) provides the long-history anchor.

## Current status

**Owned:**
- All infrastructure modules (cache, logging, rate limiting, credential redaction) in `src/terminalq/mango/`
- All data providers and analytics modules
- The prediction ledger and regime scoring system
- Test suite (166 tests, all offline)

**Partial:** Some fallback data sources (e.g., crypto backups) live inside TerminalQ's own modules and activate only once the host project's code calls them. Those edits live in a reproducible patch; see `wiring/README.md` for details. Tests covering the wired features skip gracefully when the host project is not present, keeping the test suite usable standalone.

**Not owned:** The TerminalQ host project itself. Mango modules are being migrated to full independence; until complete, installation involves both copying the source tree and applying the wiring patch.

## Installing

```bash
git clone https://github.com/fakoli/terminalq.git
git clone https://github.com/Deadmonkk/terminalq-extensions.git

# Copy Mango's modules into TerminalQ
cp -r terminalq-extensions/src/terminalq/*  terminalq/src/terminalq/
cp -r terminalq-extensions/tests/*          terminalq/tests/
cp -r terminalq-extensions/scripts          terminalq/

cd terminalq && uv sync
```

To activate the fallback crypto sources and full integration testing, apply the wiring patch:

```bash
git apply /path/to/terminalq-extensions/wiring/upstream-wiring.patch
```

See `wiring/README.md` for details on what this patch does and why it exists.

## Running tests

```bash
cd terminalq && uv run pytest tests/
```

Expected: 156 pass, 10 skip (the skipped tests cover wiring-dependent features and will pass after applying the patch). All network calls are faked; tests run offline and consistently.

## Dependencies

`httpx`, `pandas`, `yfinance`, `pytest`. TerminalQ includes all except pandas.

## Example: Average Daily Volume

The `scripts/adv.py` utility calculates what a stock normally trades in dollar terms, using 3 months of raw price data rather than trusting a website's aggregated figure. This is useful for sizing flows against realistic liquidity:

```
$ python scripts/adv.py AAPL MSFT NVDA --flow 1.0e9

TKR       $ADV mean   $ADV MEDIAN     $ADV 20d   sh ADV med   spiky
-------------------------------------------------------------------
AAPL    $16,716.97M   $15,016.29M  $15,979.70M   48,535,150    5.4x
MSFT    $15,605.59M   $13,429.72M  $12,468.76M   33,034,250    5.6x
NVDA    $32,125.79M   $30,518.71M  $26,385.34M  148,628,350    1.9x

window: 3mo — 62 bars, 2026-04-29 to 2026-07-28

Sizing $1,000M of flow against MEDIAN $ADV (the conservative read):
  AAPL       0.1x ADV  =     0.1 days of volume
  MSFT       0.1x ADV  =     0.1 days of volume
  NVDA       0.0x ADV  =     0.0 days of volume

Prefer the MEDIAN for sizing — the mean is inflated by rebalance/earnings prints.
Spiky names (>3x) disrupt more than days-of-volume implies: the flow day is not this average day.
Yahoo consolidated tape (incl. off-exchange prints); order-of-magnitude, not a filing.
```

The tool reports the median alongside the mean (because a handful of rebalance days pull the average up), computes multiple windows (3-month median, 20-day, daily mean), and repeats its own caveats every run. No number is quoted later without the qualifications attached.

## Architecture

```
Free public sources (NASA, CFTC, Fed, exchanges, web tables)
                    ↓
Data providers (20 modules — keep working when a source breaks)
                    ↓
Analytics (context: percentiles, correlation, stress backtests)
                    ↓
Prediction ledger (log the call, grade it on the due date, report accuracy)
```

Each layer is independently testable. Providers return `{"error": ...}` payloads instead of raising, so a caller can propagate the failure clearly. Analytics modules verify their outputs against independent sources rather than trusting a single number.

## A few things I already know could be better

- The web scrapers use regex pattern matching instead of a proper HTML parser. It works, avoids adding a dependency, and is the kind of shortcut worth complaining about.
- One file, `src/terminalq/providers/_html.py`, has no tests of its own, and four scrapers depend on it. The weakest spot in test coverage.
- A couple of modules (`providers/reports.py`, `providers/backfill.py`) only make full sense in the context of the complete FR workflow. Read in isolation, they'll look sparse.

## How this was built

I built this with Claude Code. AI generated a large share of the code here. I decided what to build, reviewed what came back, and own every decision in it.

The most useful lesson came from the first time I deployed: the code worked perfectly on my machine. It had also never been run on a clean machine. 57 configuration values existed only on my computer, in a file I had never shared. The install instructions I had written with confidence could not have worked for anyone else.

Neither I nor the AI caught it. We were both looking at my environment, where everything was already in place.

The tests caught it. Which is why there are 166 of them, why they run offline on a clean machine every time, and why they run automatically on GitHub Actions on each commit.

## License

MIT. See [LICENSE](LICENSE).

# terminalq-extensions

An extension pack of market-data providers, analytics modules, and workflow commands
for [TerminalQ](https://github.com/fakoli/terminalq), a Bloomberg-style financial
terminal that runs as a Claude Code plugin.

**28 modules, 36 test files, ~9,400 lines.** Every data source added here is free and
most require no API key at all.

---

## What this adds

TerminalQ ships with quotes, fundamentals, and core macro data. This pack extends it in
four directions.

### 1. Free, keyless data providers

Institutional market data is expensive. Every provider here reaches a public source
directly — a government API, an exchange's public endpoint, or a scraped public table —
so the whole pack runs at zero marginal cost.

| Module | Source | What it gives you |
|---|---|---|
| `climate.py` | NASA POWER | Temperature and precipitation anomalies vs the 2001–2020 normal, across commodity-production regions |
| `cftc.py` | CFTC | Commitment of Traders futures positioning — commercial vs speculative |
| `defillama.py` | DefiLlama | DeFi total-value-locked and stablecoin supply |
| `etf_flows.py` | Farside | Daily spot Bitcoin ETF flows |
| `mempool.py` | mempool.space | Bitcoin fee and congestion microstructure |
| `hyperliquid.py` | Hyperliquid | Perpetual funding rates and open interest |
| `prediction_markets.py` | Polymarket | Live prediction-market odds |
| `fed_calendar.py` | federalreserve.gov | FOMC meeting schedule |
| `options_flow.py` | Yahoo option chains | Dealer gamma exposure and call/put walls |
| `retail_sentiment.py` | AAII + Yahoo | Weekly bull-bear survey and SPY put/call ratio |
| `sectors.py` | Yahoo | 11 SPDR sector ETFs measured against SPY |
| `valuation.py` | multiple | Shiller CAPE, earnings yield, equity risk premium |
| `cycle.py` | FRED | Six-signal recession dashboard (Sahm rule, yield curves, claims, NFCI) |

### 2. Analytics that add historical context

A raw number is close to meaningless without knowing where it sits in its own history.
These modules answer "is this normal?" rather than just "what is this?".

- **`percentiles.py`** — converts any metric into its percentile against its full history.
  A high-yield spread of 3.2% means nothing on its own; *3.2%, the 2nd percentile of the
  last 30 years* means credit markets are priced for near-perfection.
- **`correlation.py` / `correlation_regime.py`** — cross-asset correlation matrix, plus
  detection of when correlations are *tightening*. Assets moving together is a risk signal
  that individual asset prices hide, because it means diversification has stopped working.
- **`stress_backtest.py` / `backtest_utils.py`** — a registry for asking what actually
  happened to prices during comparable historical stress windows.
- **`fred_archive.py`** — a local archive of FRED series. Built after a data vendor
  retroactively truncated a series' history behind a license change; archiving locally
  means past analysis stays reproducible.

### 3. A self-grading prediction ledger

The part I'd point at first. Analysis that is never scored drifts toward being
unfalsifiable, so this makes it falsifiable by construction:

- **`history.py`** — append-only local store of dated regime snapshots and logged predictions.
- **`prediction_grader.py`** — settles each prediction automatically once its horizon
  elapses, by comparing against realized prices. No opportunity to quietly forget a bad call.
- **`regime_history.py`** — measures realized forward returns grouped by what the model
  scored at the time, which is what tells you whether the scoring weights actually predict
  anything or just feel plausible.
- **`backfill.py`** — reconstructs the snapshot store from previously written reports.

### 4. Resilience and tooling

- **Fallback providers** — `yahoo_crypto.py` and `hyperliquid.py` take over when the
  primary source is rate-limited or down, so a single upstream outage doesn't kill a report.
- **`_lazy_yfinance.py`** — defers a heavy import until first use, cutting cold-start time.
- **`scripts/fr_collect.py`** — runs data collection out-of-process and emits a compact
  brief, which substantially reduces the token cost of a full report.
- **`scripts/adv.py`** — computes average daily volume from raw OHLCV rather than trusting
  an aggregator's figure, and reports the median (the mean is skewed by rebalance days).
- **`voice.py`** — spoken briefings through the macOS `say` command.

---

## Installation

These modules import from TerminalQ's internals (`terminalq.cache`, `terminalq.config`,
`terminalq.logging_config`, `terminalq.rate_limiter`, and several of its base providers).
**This is an extension pack, not a standalone application** — it needs a TerminalQ
checkout to run against.

```bash
git clone https://github.com/fakoli/terminalq.git
git clone https://github.com/Deadmonkk/terminalq-extensions.git

# overlay the extension files onto the TerminalQ tree
cp -r terminalq-extensions/src/terminalq/*      terminalq/src/terminalq/
cp -r terminalq-extensions/commands/*           terminalq/commands/
cp -r terminalq-extensions/tests/*              terminalq/tests/
cp -r terminalq-extensions/scripts              terminalq/

cd terminalq && uv sync && uv run pytest
```

New providers still need registering as MCP tools in TerminalQ's `server.py`. Those
registrations are edits to upstream files and so are not included here.

### Dependencies

`httpx`, `pandas`, `yfinance`, and `pytest` for the test suite. All are already TerminalQ
dependencies except `pandas`.

---

## Testing

36 test files cover the modules above. Network calls are mocked, so the suite runs
offline and deterministically.

```bash
uv run pytest tests/
```

---

## Design notes

A few constraints shaped this code, and they are the parts worth reading:

**Free sources only.** Every provider is either a government API, a public exchange
endpoint, or a scraped public table. No paid data feeds and, for most of them, no API key.

**Assume sources break.** Scraped tables change layout and public APIs rate-limit. Providers
fail soft and hand off to a fallback rather than taking down an entire report.

**Never invent a number.** When a source fails, the report says the data is unavailable.
It does not interpolate, estimate, or fill the gap from memory — a plausible fabricated
figure is worse than an acknowledged hole.

**Percentiles over raw values.** A metric without historical context invites confident
misreadings.

**Predictions get graded.** See the ledger above.

---

## Credits

Built as an extension to [TerminalQ](https://github.com/fakoli/terminalq) by Sekou
Doumbouya. This repository contains only my own original modules; no upstream code is
redistributed here.

## License

MIT — see [LICENSE](LICENSE).

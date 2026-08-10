# Mango

[![tests](https://github.com/Deadmonkk/mango/actions/workflows/tests.yml/badge.svg)](https://github.com/Deadmonkk/mango/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

A financial-research toolkit for collecting market, credit, and crypto data from free public sources, putting raw numbers in historical context, and grading its own predictions. It runs as an MCP server exposing 88 tools to Claude Code. 66 modules, 575 tests, approximately 13,500 lines of code. Standalone — no host project, no patch to apply.

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

**State the convention, not just the number.** Most of these metrics have more than one defensible formula. Sortino alone differs by a third depending on whether the downside deviation divides by all observations or only the losing ones. Every such choice is documented at the definition site, because a mismatch against another tool is more often a convention difference than a bug.

## Installing

```bash
git clone https://github.com/Deadmonkk/mango.git
cd mango && uv sync
```

Then register it with Claude Code by adding this to `~/.claude.json` under `mcpServers`:

```json
"mango": {
  "type": "stdio",
  "command": "uv",
  "args": ["run", "--directory", "/path/to/mango", "python", "-m", "mango"],
  "env": {}
}
```

Use an absolute path for `command` if `uv` is not on the PATH that Claude Code launches with — a GUI-launched app does not inherit your shell profile, and the failure looks like the server simply never starting.

Restart Claude Code. The tools appear as `mcp__mango__get_quote`, `mcp__mango__get_risk_metrics`, and so on — the server prefix carries the name, so the tools themselves are unprefixed.

Verify it came up:

```bash
uv run python -m mango   # logs: "Mango MCP server starting with 88 tools"
```

The repo also ships 6 skills and 12 slash commands under `skills/` and `commands/`.

## Where your data lives

**Nothing you create is stored in this repository.** Clone it, pull it, delete
and re-clone it — your data is never involved. It lives in a separate directory
that Mango creates on first run:

```
~/.mango/                       # override with MANGO_HOME
├── portfolio-holdings.md       # your positions      (you write these)
├── watchlist.md                # symbols you track   (you write these)
├── rsu-schedule.md             # optional
├── accounts.md                 # optional
├── history/                    # predictions ledger, regime snapshots, FRED archive
├── cache/                      # provider responses (safe to delete)
├── audit/                      # a record of every tool call
└── usage/                      # daily call counts
```

Created with mode `700`, files `600` — it holds positions, an audit trail and
cached responses from keyed APIs, and a default umask would leave those
world-readable.

The four markdown files are yours to write; Mango only reads them. Everything
else it maintains. Deleting `cache/` is always safe. Deleting `history/` throws
away your prediction track record, which is the one thing here that cannot be
regenerated.

### Relocating it

| Variable | Moves |
|---|---|
| `MANGO_HOME` | Everything, in one setting |
| `MANGO_CACHE_DIR` | Just the cache — e.g. onto a faster disk |
| `MANGO_AUDIT_DIR`, `MANGO_USAGE_DIR`, `MANGO_HISTORY_DIR`, `MANGO_PORTFOLIO_DIR` | One directory each |
| `MANGO_REPORTS_DIR` | Generated reports (default `~/market-reports`) |
| `MANGO_ENV_FILE` | The credentials dotfile (default `~/.env`) |

A specific variable always beats `MANGO_HOME`, so you can move one directory
without disturbing the rest. `CACHE_DIR` and `PORTFOLIO_DIR` are still read for
backward compatibility but warn: unprefixed names like those belong to whichever
program reads them first, and pointing Mango's cache at another tool's directory
corrupts both.

### Upgrading

`git pull` and restart. Upgrades touch code only — there is no step where
updating Mango writes to, moves, or deletes anything under `MANGO_HOME`.

If you used this project before it was renamed, a `~/.terminalq` directory is
**copied** to the new location on first run. The original is left exactly where
it is, and if the new location already has data the migration refuses and tells
you, rather than guessing which prediction ledger is the current one.

## Secret scanning (required after cloning)

Two git hooks gate secrets before they can reach GitHub. They live in
`.githooks/` and are versioned, but git does not enable hooks automatically —
each clone must opt in once:

```bash
brew install gitleaks
git config core.hooksPath .githooks
```

- **pre-commit** scans the staged diff (fast, every commit).
- **pre-push** scans full history — it catches anything that entered via
  `--no-verify`, a rebase, or a commit predating the hooks.

Both **fail closed**: if gitleaks is missing, the operation is refused rather
than silently skipped. Findings print redacted, so the hook output never
becomes a second copy of the secret.

`.gitleaks.toml` extends the upstream ruleset with path rules for this
project's private data (holdings, watchlist, prediction ledger, FR/EOD
artifacts). Those files contain nothing a pattern scanner would recognise as a
secret, so they are blocked by filename — the failure mode that actually
happens is a copy landing where `.gitignore` does not reach.

Test placeholders are allowlisted by **value** (`test_key_[0-9]+`), never by
file: allowlisting a whole test file would stop a real key pasted into it from
being caught, and test files are where that happens.

If a secret ever does reach the remote, rotate it first — deleting the commit
does not un-publish it.

## Running tests

Test tooling lives in the `dev` extra, which `uv sync` does not install by default:

```bash
uv sync --extra dev
uv run pytest
```

Expected: **575 pass, 7 skip.** The 7 skips are integration tests for a host project that no longer exists; they skip by design rather than fail. All network calls are faked, so tests run offline and consistently.

Running `uv run pytest` without the extra does not fail cleanly — pytest resolves from outside the project environment and collection dies on a missing `mcp` import, which looks like a broken dependency rather than a missing dev tool.

## Dependencies

`mcp[cli]`, `httpx`, `pandas`, `yfinance`, `ddgs`. The `mcp` pin is `<2` and load-bearing: 2.x relocated `mcp.server.fastmcp`, which the server imports, so an unpinned resolve breaks startup.

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
Data providers (30 modules — keep working when a source breaks)
                    ↓
Analytics (9 modules — percentiles, correlation, stress backtests)
                    ↓
Prediction ledger (log the call, grade it on the due date, report accuracy)
                    ↓
MCP server (6 tool modules, 88 tools)
```

Each layer is independently testable. Providers return `{"error": ...}` payloads instead of raising, so a caller can propagate the failure clearly. Analytics modules verify their outputs against independent sources rather than trusting a single number. The server layer registers each tool by its function name and wraps every call in audit logging, so a failing tool returns an error payload rather than killing the session.

## A few things I already know could be better

- The web scrapers use regex pattern matching instead of a proper HTML parser. It works, avoids adding a dependency, and is the kind of shortcut worth complaining about.
- One file, `src/mango/providers/_html.py`, has no tests of its own, and four scrapers depend on it. The weakest spot in test coverage.
- A couple of modules (`providers/reports.py`, `backfill.py`) only make full sense in the context of the complete FR workflow. Read in isolation, they'll look sparse.
- The repo directory and the local data directory (`~/.terminalq/`) still carry the old project's name. Cosmetic, but they outlived the thing they were named after.
- `wiring/` is kept as a record of how Mango integrated with its former host. Nothing in the install path touches it.

## How this was built

I built this with Claude Code. AI generated a large share of the code here. I decided what to build, reviewed what came back, and own every decision in it.

The most useful lesson came from the first time I deployed: the code worked perfectly on my machine. It had also never been run on a clean machine. 57 configuration values existed only on my computer, in a file I had never shared. The install instructions I had written with confidence could not have worked for anyone else.

Neither I nor the AI caught it. We were both looking at my environment, where everything was already in place.

The tests caught it. Which is why there are 575 of them, why they run offline on a clean machine every time, and why they run automatically on GitHub Actions on each commit.

The same lesson recurred when Mango was cut loose from its host: the server was configured, the config was correct, and it still could not start — `mcp` had only ever been a development dependency, and nothing had run the exact configured command. Verifying the thing you actually ship, not a close relative of it, is the whole discipline.

## License

MIT. See [LICENSE](LICENSE).

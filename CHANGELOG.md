# Changelog

Entries that change what a number *means* are listed first in each release,
because those are the ones that make two reports incomparable.

## 2026-08-17

**No figure moves in this release.** Every change below is transport,
caching or labelling; no series was swapped, rescaled or re-derived, so
reports before and after remain directly comparable.

### Added

- **`core/http.py` — one outbound-HTTP policy for every provider.** FR fans
  out to ~70 sources; at even a 2% per-source failure rate the chance all 70
  succeed is `0.98**70` ≈ 24%, so most runs showed at least one
  `data unavailable (source failed)` row. Measured on 2026-08-17, the FRED
  release-calendar endpoint failed 1 call in 6 with a bare `ReadTimeout` and
  succeeded on the next attempt — a dropped packet, not an outage. Resilience
  was previously a property of each provider rather than of the platform: of
  19 provider modules only three retried anything, so one lost packet against
  any of the other 16 became a permanent gap in the report. `fetch_json()` /
  `fetch_text()` now own timeout, retry, exponential backoff with jitter, and
  `Retry-After`, and all 16 remaining providers were migrated onto them
  (16 migrated + 3 allowlisted = the 19 modules that make outbound calls).

  Retries are classified, never blind: `ConnectTimeout`, `ReadTimeout`,
  connection resets and `429/500/502/503/504` are retried; `400/401/403/404`
  and malformed JSON are not, because no number of attempts fixes them and
  hammering a premium-walled 403 burns quota that `core/usage_tracker.py`
  counts against a hard free-tier ceiling.

  The layer does **not** swallow errors. It re-raises after the final attempt,
  because providers already convert exceptions to `{"error": ...}` payloads
  and `scripts/fr_collect.py:safe()` already guarantees a failed source cannot
  abort a run. Degrading loudly stays the contract; this only stops TRANSIENT
  blips from becoming degradations.

- **`tests/test_http_convention.py` — the boundary is enforced, not documented.**
  Walks every provider's AST and fails if one issues a raw request or handles a
  raw response (`raise_for_status()` is the tell). Constructing an
  `httpx.AsyncClient` stays legal — pooling is good, and the client can be
  handed to `fetch_json(..., client=...)` — so the rule targets the
  architectural boundary rather than a particular call. `coingecko`,
  `crypto_funding` and `finnhub` are allowlisted with reasons; a second test
  asserts those modules still exist so a rename cannot silently exempt one.

- **`cache.get_stale()`** — reads an entry even when expired, returning its age,
  and deliberately does not delete it (`get()` does). Lets a source serve a
  known-old answer, labelled, instead of nothing.

### Changed — internal

- **The economic calendar can no longer report a quiet week as a failure.**
  §11 now renders four distinct states: a populated table; a genuine lull
  (`source OK, genuinely quiet`) naming the next release beyond the window; a
  cached copy (`served from cache (Nh old)`); and a true outage, now carrying
  its reason. On 2026-08-17 the window Aug 17–24 legitimately contained none of
  the seven tracked high-impact releases — the next was GDP + PCE on Aug 26 —
  and that was indistinguishable from a dead source.

- **The release calendar is cached for 6h with stale-serve on failure.** A
  release schedule is published weeks ahead and is effectively static intraday,
  so one fetch now covers a full day of FR re-runs.

- **EDGAR now retries 5xx.** A behaviour change, not just plumbing: an EDGAR
  outage takes ~3x longer to report failure than before. The 403 path is
  unchanged and still carries its User-Agent explanation, now derived from the
  raised `HTTPStatusError`.

### Fixed

- **Backoff could exceed its own documented ceiling.** Jitter was applied after
  the cap, letting a delay land 30% past `MAX_BACKOFF_SECONDS`. Capped after
  jitter.

- **Stale-serve would never have worked.** The first implementation called
  `cache.get()` before `cache.get_stale()`, and `get()` deletes expired entries
  as a side effect of reading — destroying the exact copy the fallback needed.
  The entry is now read once.

- **An EDGAR test mock hid a bug in itself.** Its fake response excluded 403
  from raising, so `raise_for_status()` was a no-op for precisely the status
  EDGAR cares most about. All 4xx/5xx now raise in the fake.

### Unchanged, deliberately

- **No circuit breaker.** Measured: the CoinGecko outage on 2026-08-17 raised
  `ConnectError`, which fails in milliseconds, not timeouts — the whole outage
  cost ~21s of a ~70s run, most of it sequential *fallback* attempts rather
  than retries against a dead host. Not worth the machinery yet.

- **No SLOs, metrics pipeline or health dashboard.** This is a single-user
  batch job run 10–20x/day, not a service; `core/audit.py` already answers
  "what did the tool return, and when".

- **The operational claim is NOT made.** The tests prove the retry mechanism is
  correct. They do not prove the visible failure count drops — a post-migration
  sample of 22 real FRED calls succeeded with no retry ever firing, which shows
  only that FRED was healthy. Treat the improvement as a hypothesis until a
  week of runs has accumulated.

## 2026-08-11

### Changed — affects report figures

- **A provider's explicit null is no longer reported as a source failure.** A
  path that does not resolve is a failure; a path that resolves to JSON null is
  the provider saying "not meaningful here". Folding both into
  `data unavailable (source failed)` published three climate regions on
  2026-08-10 as unknown when their data had in fact been returned — Mato Grosso
  read as cold-and-unknown when it was cold-and-WETTER-than-normal, which
  inverts the soy/corn interpretation. Nulls now render as
  `n/a (provider returned null — not a failure)`, and the climate row falls back
  to the absolute millimetres the provider did return.

  **This is a labelling fix, not a change of series.** No figure moves; cells
  that read as failures may now carry a value. Reports remain comparable and the
  schema stays at v2.

### Added

- **The collector writes the finished report, not just a digest.**
  `fr_collect.py --emit-report` emits `YYYY-MM-DD-{fr,eod}.md` with every
  deterministic block already populated and prose left as delimited, empty slots
  (`<!-- PROSE:key -->`). Previously every table was transported through the
  model so the model could copy it back into a file: ~15k tokens per run of pure
  transport, and "never rebuild the tables" was a matter of trust rather than
  structure. It is now structural — the only bytes the model writes are prose.
- **`fr_prose.py`** injects prose into those slots, failing loudly on an unknown
  or missing marker rather than appending. Idempotent, so a slot can be refilled
  on an intraday re-run. It routes to the FR or EOD slot set by inspecting the
  report's own markers.
- **The §0 delta is computed in code.** Each FR run writes a flat
  `fr_values_<date>.json` snapshot that the next run diffs against, instead of
  the model re-reading yesterday's report. Only metrics that moved take a row;
  the unchanged ones are counted and named so the denominator stays visible.
- **EOD report generation** (`eod_report.py` / `eod_render.py`), mirroring FR.
  The sector scoreboard, gainer/loser split and asset-class grid are ranked in
  code — "top gainers" is a sort, not a judgement — and §7's expected ranges are
  ATR-derived bands labelled, in the generated caption, as explicitly not a
  forecast. The two EOD integrity rules now live in the template rather than
  depending on the prose to restate them.
- **`climate_map.py`** regenerates the standing climate-map artifact's four
  sentinel-delimited data blocks and preserves everything else byte for byte. A
  missing or duplicated sentinel is a hard error: the map is published to a
  stable URL, so a partial update is worse than no update. It replaces ~20
  hand-made string replacements per run.
- **`fr_sidecar.py`** filters the EXTERNAL community pulse. The 2026-08-10 macro
  run returned six top-ranked clusters — broadcast-ownership rules, judicial
  confirmations, water allocation, a vandalism prosecution, federal land, and
  student loans — every one matched on the bare token "federal" and none bore on
  the Fed path. A cluster is now kept only on a multi-word market phrase or a
  domain+market token pair; Polymarket rows are always kept, since an odds quote
  with a volume is a market price.
- **Rows the spec required but no run ever rendered**: CPI components, the six
  cycle signals with their own meanings, consumer delinquencies and fiscal
  ratios, the correlation regime, COT for the S&P/gold/bitcoin, international
  markets, and PPI/retail-sales calendar priors (which rendered a bare "—"
  because `get_event_scenarios` anchors only cpi/claims/jobs/payroll).

### Fixed

- A failed mover universe falls back to the watchlist. The failure sentinel is a
  truthy dict, so the `or` fallback was unreachable and a single Finnhub blip
  emptied the movers table while usable quotes sat beside it.
- A malformed entry in the asset-class or market-overview payload is dropped, or
  fails its own row, instead of raising and aborting the whole report build.
- The gainer/loser split can no longer list the same name as both, which it did
  whenever the universe was smaller than twice the display limit.

### Unchanged, deliberately

- **Report schema stays v2.** Everything above is new sources, new rows, or bug
  fixes; no figure changes what it means, so reports across this release remain
  directly comparable.
- **`grade_predictions` still runs as part of collection.** It writes to the
  ledger, so report generation is not side-effect free — but it touches only
  open, past-due calls and settles each on the close at its *due* date, so the
  write is idempotent and independent of when the run happened. Splitting
  grading from collection is deferred rather than rushed.

## 2026-08-07

### Report schema v2

Reports now carry a schema version in their header. It is bumped only when a
change alters what a figure *means* — not for code changes, bug fixes, or new
sources. Two reports sharing a schema version are directly comparable; two with
different versions are not, and the difference is explained here.

| Schema | From | What changed |
|---|---|---|
| v1 | original | — |
| v2 | 2026-08-07 | the GDP row reports real GDP (`GDPC1`), not nominal (`GDP`) |

Added because today's GDP switch is the first such change and will not be the
last. Without it, a reader comparing an archived report to a later one has to
find and read this file to know whether a moved number reflects the economy or
a definition.


### Changed — affects report figures

- **The GDP row now reports REAL GDP instead of nominal.** The row was labelled
  "Real GDP" but fed by FRED's `GDP` series, which is nominal. It now uses
  `GDPC1` (real, chained 2017 dollars), matching the label and the intent — the
  report discusses growth over time, which requires inflation stripped out.

  **Expect a step change of roughly −34% in this figure.** On 2026-08-07 the two
  series read:

  | Series | Value | Units |
  |---|---|---|
  | `GDP` (old, nominal) | 32,475.2 | Billions of dollars |
  | `GDPC1` (new, real) | 24,270.6 | Billions of chained 2017 dollars |

  This is a change of *series*, not a change in the economy. Reports written
  before this date quote the nominal figure; do not read the gap as a
  contraction. Only one archived report was affected, which is why the change
  was made rather than preserving continuity with a mislabelled number.

- **Credit and yield spreads are stated in percentage points, not percent.**
  A spread is the difference between two rates, so 2.75 means 275 basis points,
  not 2.75%. An audit briefly changed these to `%`; that was reverted.

### Dependencies — deliberate pins

- **`mcp[cli]` is pinned `<2` deliberately, pending a migration to MCP 2.x.**
  Not accidental caution: 2.x moved `mcp.server.fastmcp`, which the host's
  `server.py` imports, so an unbounded resolve took 2.0.0 and the server would
  not start. The choice is to stay on 1.x until the migration is scheduled,
  rather than to test against 2.x continuously. Anyone removing this bound
  should expect to update the server's imports at the same time.

### Added

- Contract tests for the collector's source map. It fetches all 63 sources and
  had no tests; a namespace migration silently repointed six providers at a
  non-existent package, which would have broken every report while the full
  744-test suite still passed. Importing the module now resolves every provider
  as part of the suite.
- Dealer gamma and the climate production-risk watch are collected. Both were
  required by the report spec but absent from the source map, so those sections
  rendered empty on every run.

### Fixed

- Errors are never cached. A brief upstream timeout was previously stored for a
  full TTL, replaying a stale outage as though it were live.
- A regime-score component with an unverifiable input is dropped and the weights
  renormalised, rather than scored on whichever half is present. Previously a
  missing credit-quality input applied no penalty, so an outage rendered as the
  most bullish possible credit reading.
- Credentials are redacted before anything is written to disk. HTTP client
  errors quote the failing URL, which for a keyed API carries the key.
- Test isolation of live storage is unconditional. Opt-in isolation let a test
  run write fixture values into the operator's real cache.

### Changed — internal

- Package renamed `terminalq` → `mango`; infrastructure at `mango.core.*`.
  The project no longer depends on, or occupies the namespace of, the
  third-party project it began as an extension to.
- Distribution renamed `terminalq-extensions` → `mango`.
- CI installs and tests the package standalone instead of checking out a host
  project, which demonstrates independence rather than asserting it.

### Unchanged, deliberately

- The initial-claims deterioration threshold stays at 10%. It was reconstructed
  by inference after a data-loss incident and looked implausibly high next to
  the Sahm rule, but a backtest over 1,983 weeks (~38 years) showed it firing in
  142 of them (7.2%) — frequent enough to be a live signal, rare enough not to
  be noise. It is now an evidence-backed parameter rather than a guess.
- The regime scoring weights are frozen until 2026-09-05. The first
  "Bottom-forming" crypto reading was recorded on 2026-08-06; it is the first
  score outside the single band every prior snapshot fell into, and therefore
  the first genuine test of whether the bands predict anything. Changing the
  model before it settles would destroy that experiment.

# Changelog

Entries that change what a number *means* are listed first in each release,
because those are the ones that make two reports incomparable.

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

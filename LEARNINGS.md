# Learnings

Three failures found while building this, what caused them, and what changed as a
result. Each was expensive enough to be worth writing down, and each produced a
rule that now lives in the code.

---

## 1. The 17.6M-token run

**What happened.** A scheduled job ran the Full Report headless every morning. On
2026-08-04 it hit `API Error: Connection closed mid-response`, and the wrapper
script retried — with no timeout and no check on *why* it failed. The retry re-ran
the entire report from scratch.

```
17,624,669 tokens   120 API turns   08:04 -> 14:18 (6h14m)
```

That was ~76% of the day's usage, and it was still running when I sat down to work,
so my own session was competing with it for the same quota. A second job fired at
15:35, hit the exhausted limit, and retried into the same wall twice more.

**The obvious cause was the retry. The real cause was the architecture.**

Those 120 API turns existed because the model was calling ~56 data tools one at a
time, then formatting the results into tables and computing weighted averages —
all inside its context window, re-sent every turn. Almost none of that is judgment:

| Work | Belongs to |
|---|---|
| Fetching 56 sources | deterministic — Python |
| Building markdown tables | deterministic — Python |
| Turning a percentile into "rich vs cheap" | deterministic — Python |
| Averaging weighted score components | deterministic — Python |
| *What it means, and what to watch* | the model |

**What changed.**

`fr_collect.py` gathers every source out-of-context in ~40 seconds for zero model
tokens. `fr_render.py` and `fr_sections.py` then build finished tables and compute
both regime scores in Python. The model receives a ~1,900-token digest and writes
only interpretation.

```
                    before      after
model receives      13,583      ~1,900
model writes        17,832      ~1,500
API turns              120         1-2
per run          17,600,000      ~5,000
```

Guards were added too — a wall-clock timeout, never retry into a usage limit, and
a lock so two runs can't overlap. But those only stop the bleeding. The 3,500×
reduction came from moving deterministic work out of the model.

> **Rule: if an output is a pure function of its inputs, it belongs in code.**
> Not mainly for cost — computed things cannot hallucinate and can be unit-tested.
> The tell that you are on the wrong side: *the model recomputes this identically
> every run.* That is a function wearing a costume.

---

## 2. The funding rate that was 38x too high

**What happened.** The Crypto Regime Score read BTC perpetual funding at **+95.76%
annualized** and had been reporting "crowded long — short squeeze risk" for weeks.
Funding feeds 35% of that score, and the liquidation component was pinned at 0/100.

Checking the venues directly told a different story:

```
Deribit                      +3.83%/yr
OKX                          +1.79%/yr
Hyperliquid                  +4.85%/yr
Coinglass OI-weighted        +2.52%/yr
```

**Why the number was wrong.** The source returned ~195 BTC perpetual contracts and
the code took an **unweighted mean**. Open interest is enormously concentrated, so
the average was dominated by dust:

```
Ostium              +9.5192%/8h   open interest    $412,616
Gemini Derivatives  +1.1339%/8h   open interest  $1,152,362
14 venues >$1B OI   -0.0006%/8h   open interest $43.6 billion
```

One venue holding $412K — roughly 25× more extreme than the worst *altcoin* perp on
any major exchange — was moving a number that drove a third of the score.

**The correct answer was inside the same payload the whole time:**

```
median of 195 contracts      0.0%/yr
OI-weighted (>$1B venues)   -0.6%/yr
unweighted mean            +65 to +96%/yr   <- what was being used
```

**How it was settled, rather than argued.** Funding is not a free-floating number:
it is the mechanism that tethers a perpetual to spot. A 96%/yr rate requires the
perp to trade at a sustained premium. The observed premium across three venues was
**−0.058% to +0.028%** — essentially zero, straddling it. The economics could not
produce the reported number.

**What changed.** `crypto_funding.py` — OI-weighted across venues above a minimum
open-interest threshold, out-of-band quotes rejected, and a perp-vs-spot basis
cross-check that flags rather than scores when funding and basis disagree. Score
bands were recalibrated from ±(100/−20) to ±(30/−10), since +100%/yr was an
artifact that made the liquidation component unreachable.

Effect: Crypto Regime Score **50.0 → 68.1**, and a "crowded long" watch-item that
had never been real disappeared from the reports.

> **Rule: rank, don't threshold — and weight by where the money is.**
> An unweighted mean across venues is not a market rate. When comparing values with
> different units or volatilities, score by percentile against the series' own
> history rather than fixed hand-picked bounds.
>
> **Rule: verify through an independent path.** Re-running the same code proves
> nothing. Derive the number from first principles (here: the basis), or check it
> against a source you did not build.

---

## 3. Historical data that disappeared

Two separate incidents, same lesson.

### Vendor retraction

In April 2026 ICE Data Indices restricted every `BAML*` credit-spread series on
FRED to a **rolling 3-year window**. Not a failure — a silent truncation. Every
series still returned data; it just no longer reached back.

The damage was to *interpretation*, not availability. A percentile is only as
strong as the history behind it:

```
HY index      7,727 obs since 1996-12-31   "4.9th percentile"  = tighter than 95% of 30 years
CCC             806 obs since 2023-08-07   "97.8th percentile" = widest in ~3 years
```

Both printed identically as "percentile of history". The second is not a historical
extreme, and the report was treating it as one — in the exact signal used to detect
hidden credit stress.

**What changed.**

- `fred_archive.py` permanently banks every fetched observation locally, so a
  future retraction cannot erase history already seen. It is why the HY index still
  has its full 1996 history when FRED itself no longer serves it.
- Every percentile now prints the window it was measured over, and short windows
  are explicitly barred from "record" language.
- `gz_credit.py` was added as a long-history alternative: the Gilchrist-Zakrajšek
  credit spread and excess bond premium — Federal Reserve Board, monthly since
  1973, keyless, not ICE-licensed. 642 observations across four recessions, so
  credit stress can be ranked against five decades instead of three years.

Recovery attempts that failed, recorded so nobody repeats them: ALFRED does not
vintage these series, the FRED API confirms `observation_start: 2023-08-07`, and the
Wayback Machine has no pre-restriction CSV captures.

### Self-inflicted

A `git checkout --` intended to restore an upstream file also discarded 659 lines of
local work living in the same file, plus 12 constants in another. Bytecode cache,
git stash, editor local history and Time Machine all came up empty.

It was rebuildable only because the *specification* survived: a test suite that
described the required behaviour, and saved raw payloads containing the exact output
shape of every lost function — field names, nesting, units, source strings. Rebuilt
against those, all 561 tests pass.

> **Rule: check what a destructive command will actually destroy, before running it.**
> `git checkout --` is as irreversible as `rm` for uncommitted work.
>
> **Rule: keep the outputs.** The saved payloads turned an unrecoverable loss into a
> rewrite. Archiving raw responses is cheap insurance against both vendor retraction
> and your own mistakes.

---

## 4. Cached errors turned a blip into an outage

**What happened.** A FRED API timeout during a collector run was cached as-is. One
hour later, the next scheduled run replayed 24 "data unavailable" cells in 0.0
seconds — indistinguishable from a live outage, but only because the cache was
inspectable plain JSON files. The silent damage: the Equity Regime Score reads a
CCC−BB credit gap. When the gap was missing, the scoring component applied no
penalty. An API blip rendered as the most bullish possible credit reading.

**Why it happened.** Providers return errors as dicts rather than raising exceptions,
and the cache stored those error dicts alongside real payloads without distinction.

**What changed.**
- Cache now never persists a payload containing an error, partial failures included.
- A scoring component that cannot be verified is dropped and the weights
  renormalised, rather than scored as if the half that happens to be present is
  sufficient.

> **Rule: never cache an error.** When errors are cached with TTL they degrade
> silently and repeatedly — indistinguishable from a live outage to anything that
> does not read the cache file directly.
>
> **Rule: missing inputs do not score as neutral.** A score built on half the data
> is worse than no score: it is a lie that looks authoritative. Drop incomplete
> components and renormalize the remaining weights.

---

## 5. Isolation and recovery that broke

Two separate incidents, same lesson.

### Opt-in test isolation let fixtures reach live storage

Tests that wanted to avoid polluting live storage could request a fixture that
redirected the cache directory. When owned code moved to a new cache module, tests
that did not request the fixture wrote into the operator's real cache — 67 files
over the course of the test run, including a fabricated CAPE of 35.0, which a
later report run would have served as real data.

**Why it happened.** Storage isolation was opt-in, not autouse. Tests had to
remember to request it.

### A destructive git command in a borrowed checkout

A working directory was a clone of a third-party repository carrying thousands of
lines of uncommitted local modification. An automated helper ran `git stash` while
debugging something unrelated, reverting 17 files at once. Recovery came from a
patch file captured an hour earlier. Two files (`pyproject.toml`, `CLAUDE.md`) sat
outside that patch's path filter and survived only by luck.

**Why it happened.** The helper was testing git operations and did not check which
repository it was in before running a destructive command. The backup's path filter
was narrower than the work it protected.

**What changed.**
- Storage isolation is now autouse; tests do not have to remember to opt in.
  Resolve cache directories once at import into a module constant, so they can be
  patched by fixtures — per-call environment lookups silently bypass the fixture.
- No git commands run in a borrowed checkout.
- Backups are verified to cover the entire working set before trusting them.

> **Rule: isolation is not automatic when it is optional.** Tests that forget to
> request a fixture pollute live storage. Make the fixture autouse if something
> must be isolated to be safe.
>
> **Rule: don't run git in a borrowed checkout.** A `git stash` is as irreversible
> as `rm` for uncommitted work. A borrowed repo carrying local modifications is not
> a place to test git commands.
>
> **Rule: a backup must cover everything at risk.** A patch covering 15 of 17
> destroyed files is not a backup — it is a trap.

---

## 6. An API key was written into audit artifacts

**What happened.** The HTTP client errors quote the failing URL. For a keyed API,
the URL carries the key. The collector stored error strings verbatim into audit
files in a user-facing folder — eleven occurrences. The published site never read
that folder, so exposure stayed local, but three keys were visible to anyone with
filesystem access.

**Why it happened.** Error-handling logged the full response without redaction.
Audit artifacts were assumed to be internal only.

**What changed.**
- All secrets held by the process are redacted by value wherever they appear,
  before persisting to any file.
- Redaction by value (whatever the process knows is a secret) rather than by
  pattern shape, because a pattern-based rule only catches formats someone
  anticipated.

> **Rule: redact by value, before persisting.** An error string that quotes a
> keyed URL will expose the key. Redact everything the process knows is a secret,
> not patterns you think might be secret.

---

## 7. A label asserted something the data did not support

**What happened.** A report row labelled "Real GDP" was fed by an alias resolving
to FRED's `GDP` series, which is nominal; real GDP is `GDPC1`. Every report printed
nominal GDP under a real-GDP heading for weeks.

Separately, an audit proposed "fixing" spread rows from percentage points to percent
for "consistency". That would have misstated 275 basis points as 2.75%. A spread is
a difference between two rates, so percentage points is correct — the proposed fix
was wrong.

**Why it happened.** A friendly alias was not verified against what it actually
resolved to. A plausible-sounding consistency fix was not checked against the domain
meaning of the data.

**What changed.**
- Every alias is resolved and checked against its actual FRED identifier before
  data is served.
- "Consistency fixes" on data require domain verification before they are applied.

> **Rule: a label is a claim about the data.** It needs verifying like any other
> fact. The identifier an alias resolves to, the units a series carries, and the
> meaning of a "fix" all belong in verification, not convenience.

---

## The through-line

All seven incidents were the same shape: **a number or artifact that looked
authoritative but wasn't**, surviving because nothing checked it against an
independent path.

The funding rate came from a real API and was 38× wrong. A cached error replayed
as real data. An API key leaked into audit logs. Test isolation was optional. A
label meant one thing and the data was another. Each was invisible until it
corrupted a downstream decision.

What catches these is not more caution — it is separating concerns so failures are
loud and localized. Every fix here has that shape: errors fail fast and are never
cached, isolation is mandatory not optional, secrets are redacted before storage,
labels are verified against their data, scores are computed from tested code, and
backups cover everything at risk.

## 8. A subagent's report is a claim, not a result

Nine subagents were run across the 2026-08-06/07 independence work. They
produced six working modules and three useful audits. They also, in three
separate instances, asserted things that were not true:

- one ran `git stash` in a borrowed checkout while debugging something
  unrelated, reverting 17 files and ~4,100 lines of uncommitted work;
- one reported that it handled a specific edge case (a `>` inside a quoted HTML
  attribute); a live check showed it did not;
- one rewrote a failing test to use well-formed input while keeping the name
  `test_unclosed_cell_tags`, converting a defect into a documented "limitation"
  and turning the suite green without fixing anything.

Every one of those was caught by verifying against live data or by reading the
code. None was caught by the test suite — in the third case the test suite was
the thing being subverted.

**The rule.** Delegate implementation, refactoring and exploration freely. Never
accept a behavioural claim on the strength of the report or a green suite.
Either a test genuinely exercises the behaviour, or it is checked by hand
against real data, before the work is merged. Treat the output as a junior
contributor's pull request: valuable, and not the source of truth.

**The corollary, which cost more time than the subagents did.** Three of the
*coordinator's* own scripts were also wrong in the same session — one reported
zero duplicate files when 37 existed, one missed an entire directory, one
rewrote `as`-aliased imports into invalid syntax. The failure mode is not
"subagents are unreliable." It is that any automated check is itself unverified
until something independent confirms it. The checking is where the risk sits.

## 9. Test a threshold before distrusting it

Twelve configuration constants were reconstructed by inference after a data-loss
incident. One — a recession warning that fires when initial jobless claims rise
10% over roughly three months — looked implausible next to the Sahm rule, which
triggers on a 0.5 percentage-point move. The suspicion was reasonable and it was
wrong: backtested over 1,983 weeks (~38 years) the threshold fires in 142 of
them, 7.2%, with a maximum reading of +2,296% in April 2020.

**The rule.** A parameter that looks wrong beside a differently-scaled reference
is a hypothesis, not a finding. Backtest it against its own history before
changing it. The cost of checking was one query; the cost of "fixing" a
correctly-calibrated recession signal would have been silent and long-lived.

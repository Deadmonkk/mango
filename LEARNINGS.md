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

## The through-line

All three were the same shape: **a number that looked authoritative but wasn't**,
surviving because nothing checked it against an independent path.

The funding rate came from a real API and was 38× wrong. The credit percentile was
arithmetically correct and economically misleading. The token bill came from code
that worked exactly as written.

What catches these is not more caution — it is deriving the answer a second way and
comparing. Every fix here has that shape: OI-weighting checked against the observed
basis, MVRV cross-checked between two providers, percentiles carrying the window
they were measured over, tables computed in tested code rather than re-derived
from scratch each run.

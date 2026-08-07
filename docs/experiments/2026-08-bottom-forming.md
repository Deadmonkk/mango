# Experiment: does the "Bottom-forming" crypto band predict anything?

**Status:** open — pre-registered 2026-08-07, before any observation matured.

This file records what was decided *before* the data arrived. It is separate
from `CHANGELOG.md` on purpose: a changelog records what changed in the
software, this records what we intended to test and how we agreed to read the
result. Conflating them lets an outcome quietly rewrite the criteria.

## Background

The toolkit computes a 0–100 Crypto Regime Score and maps it to a band. Higher
is meant to mean cheaper and more washed-out, and therefore historically
stronger forward returns.

Until mid-2026 every recorded snapshot landed in one band, so the scores had
been shown to be **stable, not correct** — there was nothing to compare against.
The prediction ledger separately stands at roughly a coin flip across 168
settled calls, so the scoring has no demonstrated edge either.

## Hypothesis

> A snapshot in the **Bottom-forming** band (score 65–80) is followed by a
> higher 30-day BTC return than one in the **Neutral / transitional** band
> (45–65).

## Baseline

Measured 2026-08-07 from 45 recorded snapshots, 24 matured per asset:

| Band | Matured samples | Mean 30-day forward BTC return |
|---|---|---|
| Neutral / transitional | 24 | **+3.14%** |
| Bottom-forming | 0 | — |

For reference, the equity score's only populated band (Mid-cycle, n=24) averages
+0.66% forward on the S&P 500.

## Observations pending

**Correction to an earlier claim.** The 2026-08-06 snapshot was described as the
first Bottom-forming reading on record. It is the second. A snapshot on
2026-07-14 scored exactly 65.0, and the band boundary is inclusive at the lower
edge, so it also sits in Bottom-forming. It matures first.

| Snapshot | Score | BTC baseline | Matures |
|---|---|---|---|
| 2026-07-14 | 65.0 | $64,779.00 | **2026-08-13** |
| 2026-08-06 | 67.0 | $64,554.06 | **2026-09-05** |

The 2026-07-14 sample sits on the exact band boundary, which is worth noting when
reading it: a score of 65.0 is the weakest possible Bottom-forming reading and
should not carry the same weight as one in the middle of the band.

## Decision rule

**The primary gate is sample size, not return.** This is deliberate: it makes it
structurally impossible for one spectacular result — in either direction — to
drive a model change.

### Fewer than 3 matured Bottom-forming observations

- **Do not change the scoring weights.** Not for a good result, not for a bad one.
- Record the outcome in this file.
- Update the ledger only.

### Three or more matured Bottom-forming observations

Evaluate the aggregate against the Neutral baseline of +3.14%, then decide:

| Aggregate result | Reading |
|---|---|
| Materially above Neutral | Evidence the band carries signal. Keep collecting; consider acting on it. |
| Indistinguishable from Neutral | The band is not earning its place. Investigate which components drive the score. |
| Materially below Neutral | Evidence against the weighting. Review methodology before relying on the score. |

"Materially" is left unquantified on purpose — with n≥3 and this much variance,
a fixed threshold would be false precision. The commitment is to state the
comparison plainly and record the conclusion here, not to hit a number.

## What would falsify the hypothesis

If Bottom-forming samples do not beat Neutral ones once there are at least three,
the band is not doing the work it claims to. That is the outcome to state
plainly rather than explain away.

## Outcome

_To be completed after 2026-09-05, or after the third sample matures, whichever
is later._

| Snapshot | Score | Baseline | Settled | 30d return | Notes |
|---|---|---|---|---|---|
| 2026-07-14 | 65.0 | $64,779.00 | | | |
| 2026-08-06 | 67.0 | $64,554.06 | | | |

---
name: tq-corr-regime
description: Correlation-regime monitor — is cross-asset diversification breaking?
arguments:
  - name: symbols
    description: "Optional comma-separated tickers. Defaults to the cross-asset universe."
    required: false
---

Call `get_correlation_regime` with the symbols ("$ARGUMENTS", default universe).

Present: average coupling now vs baseline, the average absolute delta, and the **biggest-moving pairs** as a table (Pair | Baseline | Recent | Δ).

Then deliver the verdict in plain English: rising coupling (everything correlating toward 1) means diversification is weakening — a classic risk-off tell that has preceded drawdowns; loosening means old hedges may not behave as expected; stable means relationships are holding. Connect it to the broader regime read (`get_cycle_position`, VIX) — a correlation spike alongside a deteriorating cycle is more worrying than one in isolation.

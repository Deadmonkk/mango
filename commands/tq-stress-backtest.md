---
name: tq-stress-backtest
description: Generalized metric stress backtest — real historical price moves when VIX/credit/CPI crossed a warning threshold
arguments:
  - name: event
    description: "vix_2008_gfc | vix_2020_covid | credit_2008_gfc | credit_2020_covid | cpi_2021_22_surge"
    required: true
---

Call `terminalq_get_metric_stress_backtest` with the event ("$ARGUMENTS").

Present the window and the metric's peak/threshold context first (from `peak_value` and `threshold_note`), then a table per ticker group: Group | Ticker | % Change over window. Interpret each group in plain English tied to the metric — e.g. for a VIX event, explain why financials/high-beta cyclicals fell harder than defensives; for the credit event, explain why regional banks and HY-heavy sectors (energy) underperformed the HY bond ETFs themselves; for the CPI event, explain the counterintuitive result if TIP actually lost money (real yields rising faster than breakevens erodes TIPS price even though they're "inflation protected").

Always surface `fact_source` and note explicitly that this is Phase 1 (VIX, credit, CPI) of a registry designed to extend to more FR metrics (Sahm rule, yield-curve inversion, PSAVERT, CCC-BB credit-quality gap, Fed path) — this is a one-off on-demand lookup, not part of the automatic FR run.

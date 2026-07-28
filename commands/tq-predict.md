---
name: tq-predict
description: Prediction-market odds (Polymarket) as a cross-check on model reads
arguments:
  - name: topic
    description: "Free-text topic (e.g. 'Fed rate', 'recession 2026', 'CPI'). Defaults to 'Fed rate'."
    required: false
---

Call `terminalq_get_prediction_markets` with the topic ("$ARGUMENTS", default "Fed rate").

Present the top markets as a table: **Question | Implied Yes % | Volume**.

Then, per the plain-language rule, for each notable market explain in one line what the probability *means* and whether it agrees or disagrees with the model-implied read (cross-check the Fed-rate markets against `terminalq_get_fed_path`; recession markets against `terminalq_get_cycle_position`). Call out any large divergence explicitly — that gap is the signal. Note that higher-volume markets are more trustworthy, and that these are real-money bets, not forecasts.

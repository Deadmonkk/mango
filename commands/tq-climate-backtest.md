---
name: tq-climate-backtest
description: Historical stress-period backtest — how the climate-watch tickers actually performed in a real past super El Nino
arguments:
  - name: period
    description: "el_nino_2015_16 (default) or el_nino_1997_98"
    required: false
---

Call `get_climate_stress_backtest` with the period ("$ARGUMENTS", default "el_nino_2015_16").

Present as a table per region: Region | Commodity Proxy (ticker: % change) | Linked Equities (ticker: % change). Note any ticker that errored (e.g. delisted or not listed in that form back then) rather than dropping it silently — that's a real data-availability limit, not a system failure.

Then connect it back to `get_climate_risk_watch`'s live flags: for any region currently FLAGGED, name what happened to the same tickers during this historical stress window as the empirical check on whether the region->commodity->equity link is real or coincidental. State plainly that pct_change is start-to-end of the window, not the peak move, so a name that spiked mid-window and gave it back will understate its volatility here.

---
name: market-overview
description: Summarize where markets stand today across equities, rates, commodities, and crypto — breadth, sentiment, and cross-asset positioning — for someone who wants a snapshot of the current tape.
---

# Market Overview

Use this skill when the user asks "how are markets doing," wants a cross-asset snapshot, or needs a same-day read on equities, rates, commodities, dollar, and crypto together. Distinct from `economic-outlook` (the macro/cycle picture) and `company-research` (a single name) — this is the tape, today.

## Tool sequence

1. `terminalq_get_market_overview()` — major index levels, YTD and 1-year returns. The frame for everything else.
2. `terminalq_get_equity_sentiment()` — VIX term structure, SKEW, equal-weight vs. cap-weight breadth. Establishes the vol and breadth backdrop before anything else is read against it.
3. `terminalq_get_sector_rotation()` — sector ETFs vs. SPY, 1/3/6-month, cyclical vs. defensive spread. Does leadership confirm or contradict the index-level read.
4. `terminalq_get_retail_sentiment()` — AAII bull-bear spread and SPY put/call. The crowd read, useful as a contrarian check at extremes.
5. `terminalq_get_dealer_gamma(symbol="SPY")` — net dealer gamma sign and the nearest call/put walls. Changes what the VIX level from step 2 actually means.
6. `terminalq_get_rates_dashboard()` — yields, real yields, breakeven inflation.
7. `terminalq_get_commodities()` — crude, gasoline, dollar index.
8. `terminalq_get_crypto_market_overview()` and `terminalq_get_fear_greed(limit=7)` — total crypto market cap and sentiment, so the overview covers the full asset-class set, not equities alone.
9. `terminalq_get_correlation_regime()` — recent vs. baseline cross-asset correlation. Tightening correlation across asset classes is a risk-off tell even when individual assets look calm.
10. `terminalq_get_international_markets()` — optional, when the question is global rather than US-only.
11. `terminalq_get_cot_report(market)` — optional, when positioning in a specific futures market (S&P, gold, etc.) is relevant to the question.

## Interpreting the output

No section is a bare number dump — every index return, VIX level, breadth ratio, and correlation reading gets a plain-English call on whether it's constructive, concerning, or neutral, and the overview closes with a synthesis connecting equities, rates, commodities, and crypto into one read on the day or week.

- **VIX and dealer gamma together**: a low VIX sitting on positive/long gamma is dealers dampening moves — a genuinely calm setup. The same low VIX sitting on negative/short gamma means dealers amplify moves — calm that can break sharply. State which regime applies before calling low volatility "calm."
- **Breadth**: equal-weight underperforming cap-weight over a stretch means the rally is narrow — a different risk profile than one confirmed by broad participation, even at the same index level.
- **Correlation regime**: rising cross-asset correlation (equities, bonds, commodities, crypto all moving together) means diversification is failing in real time — flag this plainly, don't just show the matrix.
- **Retail sentiment**: read AAII and put/call together, not separately — when the stated survey and the actual positioning disagree, positioning is generally the more reliable of the two.
- **COT data**: shows positioning as of the prior week's close, not today — always a lagged read on who is offside.

## Data freshness

- **Live/intraday**: index levels, VIX, dealer gamma, quotes.
- **Weekly**: AAII sentiment survey, CFTC COT report (as of the prior Tuesday, published the following Friday).
- **Effectively real-time but exchange-lagged by design**: crypto fear/greed and market-cap figures update frequently but are still a snapshot at call time.

Label anything weekly or older explicitly as such rather than presenting it alongside live quotes without distinction.

## Disclaimer

This is research and information, not investment advice. A snapshot of the current tape says nothing certain about tomorrow's; sentiment, breadth, and gamma readings shift quickly and none of them are a reliable timing signal on their own.

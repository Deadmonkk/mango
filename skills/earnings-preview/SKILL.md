---
name: earnings-preview
description: Preview an upcoming earnings report for one company — what's expected, how the market is positioned into it, and what a beat or miss would mean — for someone deciding how to read or trade the print.
---

# Earnings Preview

Use this skill when the user asks what to expect from an upcoming earnings report, how a stock is positioned into earnings, or wants a pre-print briefing on a specific company. Distinct from `company-research` (a general deep dive) and `trade-research` (a specific trade thesis) — this skill is anchored to one dated catalyst.

## Tool sequence

1. `get_earnings(symbol)` — reported vs. expected EPS history and the forward estimate. This is the anchor for the whole preview: what does consensus expect, and how has this company tended to land relative to estimates in the past.
2. `get_quote(symbol)` — where the stock trades right now, the base the reaction will be measured from.
3. `get_technicals(symbol)` — RSI, moving averages, ATR. ATR in particular gives an expected-move sanity check: how much does this stock normally move in a day, so an earnings gap can be read as ordinary or extraordinary.
4. `get_analyst_ratings(symbol)` — consensus rating and price target, and whether targets have been moving up or down into the print (a proxy for sell-side sentiment shift).
5. `get_financials(symbol, statement="income", periods=4)` — the trailing quarters' revenue and margin trend, so the estimate can be judged against the company's own recent trajectory rather than in isolation.
6. `get_insider_transactions(symbol, limit=10)` — insider activity in the weeks before the print. Note the standard caveat below; this is a weak, noisy signal, not a strong one, and most pre-earnings insider activity is blocked by quiet-period policy anyway.
7. `get_news(symbol, days=14)` — recent catalysts, guidance updates, or preannouncements that would shift the setup independent of the print itself.
8. `get_sector_rotation()` — optional, when sector-wide positioning (e.g. other names in the group already reported and moved) is relevant context for how this print might be read.

## Interpreting the output

Every number gets a plain-English reading: what does an EPS beat/miss of this size typically do to the stock, is the ATR-implied move big or small relative to what options or historical reactions would price, is the analyst-target trend supportive or not. End with a synthesis: given the setup, what would confirm the bullish case, what would confirm the bearish case, and where does the risk sit if the print goes the "wrong" way relative to positioning.

- **Estimates are a consensus, not a forecast this tool makes** — state it as "consensus expects X," never as a prediction of the actual result.
- **Insider transactions**: distinguish routine, pre-scheduled selling (10b5-1 plans, standard grants) from unscheduled open-market buying or selling, and note that companies typically restrict trading in the weeks before earnings, so an absence of insider activity here is expected, not informative.
- **ATR-based expected move**: this is a volatility-derived range, not a directional call — label it as such, never as a point forecast of where the stock lands after the print.
- Do not fabricate an options-implied move or a specific price target beyond what the tools return; if implied volatility or options data isn't available from these tools, say so rather than estimating it.

## Data freshness

- **Live**: quote, technicals.
- **Periodic, lagged**: financial statements (prior quarters, up to ~90 days old); earnings estimates typically update in the days/weeks before a print but are still a snapshot at call time, not real-time consensus.
- **Event-driven**: insider transactions and news — reported as they occur, but sparse or silent in the pre-earnings quiet period by design.

State the as-of nature of the estimate and the financial trend explicitly; never imply the consensus number is current to the minute.

## Disclaimer

This is research and information, not investment advice. Earnings reactions are volatile and can move against even a well-reasoned setup; this preview does not account for the user's position sizing, risk tolerance, or tax situation.

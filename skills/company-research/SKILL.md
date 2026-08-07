---
name: company-research
description: Evaluate a single public company end to end — profile, financials, valuation, insider and institutional activity, technicals, and news — for someone deciding whether to understand, watch, or act on the name.
---

# Company Research

Use this skill when the user wants a rounded picture of one company: "what does X actually do," "is X a good business," "tell me about X," or any single-ticker deep dive that isn't specifically about an upcoming earnings print (use `earnings-preview` for that) or a specific trade thesis (use `trade-research` for that).

## Tool sequence

Call in this order — cheap, structural calls first so later numbers have context; expensive or narrow calls only once the shape of the company is known.

1. `terminalq_get_company_profile(symbol)` — name, industry, exchange, market cap. Establishes what kind of company this is before any number gets judged against a peer set.
2. `terminalq_get_quote(symbol)` — live price and day range, so the rest of the report is anchored to where the stock trades right now.
3. `terminalq_get_financials(symbol, statement="income", periods=8)`, then `statement="balance"` and `statement="cash"` — margins, debt load, and cash generation trend. Pull multiple periods so a single good or bad quarter doesn't read as a trend.
4. `terminalq_get_technicals(symbol)` — SMA/EMA, RSI, MACD, Bollinger, ATR. Cheap and fast; establishes where price sits technically before layering fundamentals on top.
5. `terminalq_get_analyst_ratings(symbol)` — consensus and price targets, as a sanity check against your own read, not a substitute for it.
6. `terminalq_get_insider_transactions(symbol, limit=10)` — Form 4 filings.
7. `terminalq_get_13f_holdings(institution)` — only if a specific notable holder is relevant to the question (e.g. the user names an institution, or the company is a known concentrated-holder situation). Skip by default; it's a narrow, expensive-to-interpret call.
8. `terminalq_get_dividends(symbol, years=5)` — only if the company pays a dividend or the user asks about income/yield.
9. `terminalq_get_filings(symbol, filing_type="10-K", limit=3)` — only if the financials raise a question the summary numbers can't answer (a debt covenant, a going-concern flag, a segment breakout).
10. `terminalq_get_news(symbol, days=14)` — last, so headlines are read against the fundamentals already established rather than driving the narrative.

Optional context, pulled only if the question calls for it: `terminalq_get_sector_rotation()` (is the company's sector in or out of favor right now) and `terminalq_get_market_valuation()` (is the broad market rich or cheap, as a backdrop for the company's own multiple).

## Interpreting the output

Never hand back raw numbers. Every figure gets a plain-English reading and a good/bad/neutral call: a margin, a leverage ratio, an RSI level, an insider transaction all mean nothing to the reader without "and here's whether that's good." Close with a synthesis paragraph that ties the pieces together — does the balance sheet support the growth story, does insider activity confirm or contradict the technical picture, is the valuation asking you to believe something the financials don't yet show.

- **Insider transactions**: distinguish routine scheduled grants and 10b5-1 sales from open-market conviction buys or sells. A CFO selling on a pre-set schedule is not a signal; an unscheduled cluster of open-market buying is. Dollar values are sometimes absent from the filing — say so rather than omitting the line.
- **13F holdings**: up to 45 days stale by the time it's filed. Treat it as "what this institution owned last quarter," never as their current position.
- **Financial statement trend**: look at the multi-period series, not the latest quarter alone — one print can be a one-off (a write-down, a buyback timing effect), a trend is the real signal.
- **Technicals**: RSI and moving averages describe positioning and momentum, not whether the company is a good business. Keep the technical read and the fundamental read separate, then note where they agree or disagree.

## Data freshness

- **Live**: quote, technicals (intraday-derived), news.
- **Lagged by a quarter (up to ~90 days after period end, sooner for large filers)**: financial statements, from SEC filings.
- **Lagged by up to 45 days**: 13F institutional holdings — always a snapshot of the past quarter.
- **Event-driven, not periodic**: insider Form 4 filings — reported within days of the transaction, but only exist when a transaction occurs, so silence isn't necessarily a signal either way.

State staleness explicitly next to any lagged figure rather than presenting it as current.

## Disclaimer

This is research and information, not investment advice. It does not account for the user's specific financial situation, tax position, or risk tolerance, and past performance or current positioning says nothing certain about future results.

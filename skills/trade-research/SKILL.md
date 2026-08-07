---
name: trade-research
description: Evaluate a specific trade idea — the thesis, what would confirm or refute it, current positioning, and the risk — for someone deciding whether a proposed trade holds up.
---

# Trade Research

Use this skill when the user brings a specific trade idea to test — "should I buy X because Y," "is this thesis still valid," "what's the risk on this trade." Distinct from `company-research` (a general company view, no thesis to test) and `market-overview` (no single position). The job here is to stress-test a stated thesis against the evidence, not to originate a new idea from scratch.

## Tool sequence

Start by pricing the instrument and establishing the thesis's own claim, then gather evidence that would confirm or break it, then check positioning and risk last.

1. `terminalq_get_quote(symbol)` — where it trades now; the reference point the thesis is implicitly measured against.
2. `terminalq_get_technicals(symbol)` — RSI, moving averages, MACD, Bollinger, ATR. Establishes the technical setup and gives an ATR-based sense of normal daily movement, useful for sizing the risk later.
3. Pull whatever tools test the specific mechanism the thesis depends on — this is the part that varies by trade and cannot be a fixed checklist:
   - A fundamental/valuation thesis → `terminalq_get_financials(symbol, ...)`, `terminalq_get_analyst_ratings(symbol)`, `terminalq_get_earnings(symbol)`.
   - A macro-driven thesis (rates, credit, inflation) → the relevant `terminalq_get_rates_dashboard()`, `terminalq_get_credit_spreads()`, `terminalq_get_cycle_position()`, `terminalq_get_fed_path()`.
   - A positioning/flow thesis → `terminalq_get_cot_report(market)`, `terminalq_get_dealer_gamma(symbol)`, `terminalq_get_insider_transactions(symbol)`.
   - A cross-asset or correlation thesis → `terminalq_get_correlation_matrix(symbols)`, `terminalq_get_correlation_regime(symbols)`.
   - A crypto thesis → `terminalq_get_crypto_deep(symbol)`, `terminalq_get_btc_valuation()`, `terminalq_get_crypto_derivatives()`, `terminalq_get_crypto_funding(symbol)`.
4. `terminalq_get_news(symbol, days=14)` — recent catalysts or news that could already have priced in, or undercut, the thesis.
5. `terminalq_get_sector_rotation()` or `terminalq_get_market_valuation()` — the broader backdrop the trade sits inside; a good single-name thesis in a hostile macro/sector regime is a different risk than the same thesis with the wind behind it.

## Interpreting the output

State the thesis explicitly at the top before evaluating it, then work through the evidence with a plain-English reading of each figure and whether it supports, contradicts, or is neutral to the claim. Close with an explicit confirm/refute synthesis: what would need to be true for this trade to work, what's already true, what isn't yet, and what the risk looks like if the thesis is wrong — not just whether it's right.

- **Positioning data is a crowd read, not a prediction**: COT and dealer gamma describe who is positioned how, not what happens next. A crowded trade can still work; the point is knowing whether you'd be early, aligned, or contrarian to the current positioning.
- **Dealer gamma changes what volatility means for the trade**: positive gamma dampens moves toward the walls, negative amplifies them — this matters directly for how a stop or target should be set, not just as color.
- **Insider and 13F data are lagged and noisy** (see the standard caveats: 13F up to 45 days old, Form 4 mixes routine and conviction transactions) — useful as corroboration, never as the sole basis for the thesis.
- **A credit- or macro-driven thesis needs the CCC−BB and percentile-window caveats** applied the same way `economic-outlook` applies them — don't let a calm headline spread or a short-window percentile stand in for the full picture.
- **External/social signals** (prediction markets, community sentiment, if pulled via web search) are narrative color only — label them EXTERNAL and never let them function as a scored input to the confirm/refute call.

## Data freshness

- **Live**: quote, technicals, dealer gamma.
- **Weekly**: COT positioning (as of the prior Tuesday).
- **Lagged, potentially by a quarter or more**: 13F holdings, financial statements.
- **Event-driven, sparse**: insider transactions, news.

Every piece of evidence used to confirm or refute the thesis should carry its own freshness alongside it, so the reader can weigh a live technical signal differently from a 45-day-old institutional filing.

## Disclaimer

This is research and information, not investment advice or a recommendation to enter or exit any position. Testing a thesis against available evidence does not eliminate the risk of being wrong; the user is responsible for their own sizing, risk tolerance, and decision.

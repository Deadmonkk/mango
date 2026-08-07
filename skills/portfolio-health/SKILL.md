---
name: portfolio-health
description: Review the user's actual holdings — allocation, concentration, risk metrics, and live profit/loss — for someone who wants to know how their real portfolio is doing right now.
---

# Portfolio Health

Use this skill when the user asks about their own holdings, allocation, concentration, or portfolio-level risk — "how's my portfolio doing," "am I too concentrated," "what's my risk exposure." This is about the user's actual recorded positions, not a hypothetical or a general market view.

## Tool sequence

1. `terminalq_get_portfolio()` — recorded holdings by account, cost basis, and unrealized P/L as of the last recorded snapshot. The static baseline before anything live is layered on.
2. `terminalq_get_portfolio_live()` — the same holdings repriced at the current market, with the day's move per position. Read against step 1 to separate "how it's positioned" from "what today did to it."
3. `terminalq_get_allocation()` — breakdown by asset class, region, and sub-class, with concentration. The structural question: is this portfolio actually diversified or does it just look like it is.
4. `terminalq_get_risk_metrics(period="1y")` — Sharpe, Sortino, max drawdown, VaR(95), beta vs. SPY. The quantitative risk read, computed from the trailing period given.
5. `terminalq_get_watchlist()` — if the user tracks names outside the portfolio, useful context on where new capital might go or what's being monitored.
6. `terminalq_get_sector_rotation()` — to judge whether the portfolio's sector tilts (from step 3) are currently in or out of favor.
7. `terminalq_get_correlation_matrix(symbols)` — built from the portfolio's actual holdings, when concentration in step 3 suggests the positions might be more correlated with each other than the allocation breakdown alone shows.
8. `terminalq_get_rsu_schedule()` and `terminalq_get_rsu_tax_analysis()` — only if the user holds unvested RSUs and asks about vesting exposure or the sell-vs-hold tax tradeoff.

## Interpreting the output

Every figure gets a plain-English reading tied to what it means for this specific portfolio, not a textbook definition — a beta of 1.3 means "moves more than the market, in both directions," a max drawdown of -28% means "this portfolio has fallen that much peak-to-trough in the sample window." Close with a synthesis: is the portfolio well-positioned for what it's trying to achieve, where is the concentration risk, and what would meaningfully change the risk profile.

- **Cost basis vs. live P/L**: `get_portfolio` reflects the last recorded snapshot; `get_portfolio_live` reprices it now. If the two diverge meaningfully, say so — it usually means the recorded snapshot is stale, not that something is wrong.
- **Risk metrics are backward-looking**: Sharpe, Sortino, drawdown, and VaR are statistics of realized returns over the chosen period. They describe what has happened, not what will — a low historical VaR says nothing about a risk that hasn't shown up in the sample yet. State this rather than presenting the numbers as a guarantee.
- **Concentration**: a single position or sector at an outsized share of the portfolio is a risk statement independent of whether that position has performed well — flag it even when the concentrated name has been the best performer.
- **Correlation between holdings**: two positions in different sectors can still move together; check the actual correlation rather than assuming allocation-by-label equals diversification.

## Data freshness

- **Live**: prices in `get_portfolio_live`, day's move per position, current quotes.
- **As-of a recorded snapshot, potentially stale**: cost basis and the static `get_portfolio` view — state the `as_of` date returned by the tool rather than implying it's current.
- **Trailing-window statistics, not live**: risk metrics are computed over the period requested (default one year) and update only when recalculated, not tick-by-tick.

Always surface the `as_of` date for the static portfolio view and the period used for risk metrics, so the reader knows exactly what window they're looking at.

## Disclaimer

This is research and information about the user's own recorded holdings, not investment advice. It does not account for the user's full financial picture, tax situation, or goals beyond what is reflected in the data pulled, and historical risk statistics do not guarantee future portfolio behavior.

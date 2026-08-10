---
name: economic-outlook
description: Assess the macro picture — growth, inflation, labour, rates, credit, and cycle position — for someone who wants to know where the economy stands and where the risks sit.
---

# Economic Outlook

Use this skill when the user asks about the state of the economy, recession risk, the Fed's likely path, inflation, or the macro backdrop generally, independent of any single company or trade.

## Tool sequence

Start broad and cheap, then go deep only where the headline numbers raise a question.

1. `get_macro_dashboard()` — headline growth, inflation, rates, and labour in one call. Sets the frame for everything after it.
2. `get_cpi_components()` — break inflation into shelter, energy, food, and core, since the composition of a CPI print matters as much as the headline.
3. `get_jolts()` — job openings, hires, quits, layoffs. Leads the unemployment rate, so it's the labour-market read that matters most for where things are headed, not just where they are.
4. `get_cycle_position()` — the recession dashboard: Sahm rule, yield curves, claims trend, NFCI, GDPNow. This is the single call that answers "where are we in the cycle."
5. `get_rates_dashboard()` — nominal yields, real (TIPS) yields, breakeven inflation. Needed to say whether a yield move is a growth story or an inflation story.
6. `get_credit_spreads()` — IG/HY spreads by rating tier, including the CCC vs. BB gap.
7. `get_gz_credit()` — the Gilchrist-Zakrajsek spread and excess bond premium, monthly since 1973. Use this for any historical-percentile credit claim; it isn't subject to the ICE licence truncation that shortened the CCC/BB series.
8. `get_consumer_health()` — debt service, delinquencies, saving rate, revolving credit. The leading edge of consumer stress, ahead of where it shows up in credit spreads or spending data.
9. `get_liquidity()` — net liquidity (Fed balance sheet less reverse repo and the Treasury account) and its trend, for the monetary backdrop.
10. `get_fiscal_health()` — debt-to-GDP and the monthly budget balance, if the fiscal/issuance angle is relevant to the question.
11. `get_fed_path()` — market-implied policy path from fed funds futures, to close the loop on what the market itself expects the Fed to do.
12. `get_economic_calendar(days=7)` — upcoming releases worth watching, so the outlook ends with "here's what could move this view next."

## Interpreting the output

Every indicator needs a plain-English reading and a good/bad/neutral call, not a bare figure — a Sahm rule reading, a yield-curve inversion, a JOLTS ratio all mean something specific and the reader should not have to know the threshold themselves. Close with a synthesis: where does the weight of evidence point on the cycle, what is the biggest risk, and what single upcoming data point would most change the picture.

- **Credit-quality divergence**: a tight headline HY spread is not an all-clear on its own. Check the CCC−BB gap — if BB sits near a historically tight level while CCC sits at or above its own norm, the lowest-quality credit is stressed even though the index looks calm. Say so explicitly rather than letting a tight index spread stand alone as bullish.
- **History-window caveat**: a 2023 vendor licence change truncated the CCC and BB spread series to roughly three years of history. A percentile computed over that window is a rank within three years, not a historical extreme — never call it "record-tight" or "record-wide" without naming the window. The Gilchrist-Zakrajsek series is the correct anchor for any claim about decades of history.
- **Term structure**: decompose a rate move into the expected-policy-path component and the term-premium component where possible — a rise driven by term premium (fiscal/issuance concern) has a different implication than one driven by the expected-rate path (growth/inflation repricing). Same yield, different story.
- **Cycle position**: markets and the economy don't bottom on the same clock — a cycle dashboard that is "bad but stabilizing" is a different signal than "bad and still deteriorating," and that distinction matters more than the raw level of any one indicator.

## Data freshness

- **Live/near-real-time**: market-implied Fed path, yields, credit spreads.
- **Monthly, lagged by weeks**: CPI, JOLTS, consumer-health series — a print released today describes a month or more in the past.
- **Quarterly, lagged further**: GDP, debt-to-GDP.
- **Nowcast, not a report**: GDPNow is a running model estimate, not an official release — label it as such.

State the reference month or quarter for every macro figure; never present a lagged monthly series as describing the current moment.

## Disclaimer

This is research and information, not investment advice or an economic forecast to be relied on for financial decisions. Macro indicators are noisy, subject to revision, and no single dashboard reliably calls a turn in advance.

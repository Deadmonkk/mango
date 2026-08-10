---
name: tq-jp-style
description: Full institutional-style macro and market intelligence brief — runs all 11 analyses in parallel
---

Run ALL of the following tools **simultaneously in parallel** (do not wait for one before starting the next):

1. `get_macro_dashboard` — core economic indicators
2. `get_cpi_components` — inflation breakdown
3. `get_jolts` — labor market depth
4. `get_credit_spreads` — credit market stress signal
5. `get_consumer_health` — debt service and delinquencies
6. `get_fiscal_health` — federal debt and deficit
7. `get_commodities` — oil, gold, gasoline, dollar
8. `get_market_overview` — equities, VIX, dollar, gold, oil
9. `get_asset_class_returns` — cross-asset performance
10. `get_international_markets` — global equity performance
11. `get_economic_calendar` — upcoming market-moving events

---

Once all data is returned, present the report in this exact structure:

---

# JP Morgan-Style Market Intelligence Brief
**Date:** [today's date]

---

## 1. MACRO SNAPSHOT
Present GDP, CPI, Core CPI, unemployment, fed funds, 10y yield, 2y yield, yield spread, initial claims, consumer sentiment.
Show latest value + change. Flag anything with ⚠️ if trending wrong direction.
End with a 2-sentence macro read.

---

## 2. INFLATION DEEP DIVE
Break down CPI into shelter, energy, food, core goods, services.
Identify which components are accelerating vs decelerating.
State the YoY rate and whether the Fed's 2% target is getting closer or further.

---

## 3. LABOR MARKET
Show JOLTS openings, hires, layoffs, quits, and wage growth.
Interpret the quits rate (confidence signal) and layoffs trend.
Cross-reference with initial claims from the macro dashboard.
Give a 1-sentence labor market verdict: tightening, stable, or softening.

---

## 4. CREDIT & STRESS SIGNALS
Show IG spread, HY spread, BB, B, CCC with the risk signal.
Compare current levels to historical averages (IG ~145bps, HY ~500bps).
State whether credit markets are complacent, normal, or stressed.

---

## 5. CONSUMER & FISCAL HEALTH
Show household debt service ratio and all three delinquency rates.
Show federal debt as % of GDP and latest monthly deficit.
Flag any deteriorating trends.

---

## 6. COMMODITIES & DOLLAR
Show WTI, gold, gasoline, and dollar index with change.
Note any inflationary or deflationary signals from commodity moves.

---

## 7. EQUITY MARKETS
Present S&P 500, Dow, Nasdaq, Russell 2000, VIX with YTD and 1yr returns.
Show style box: which size/style is leading (growth vs value, large vs small).
Show asset class returns table across 1mo, 3mo, YTD, 1yr — sort by YTD performance.

---

## 8. GLOBAL MARKETS
Show international market YTD returns in USD.
Note which regions are outperforming or underperforming US equities.
Flag any notable divergences.

---

## 9. THIS WEEK'S CALENDAR
List only HIGH impact events with date, time, estimate vs previous.
Flag the single most important release of the week.

---

## 10. SYNTHESIS — THE BIG PICTURE
Write 4-6 sentences that connect all the above into a coherent market narrative.
Cover: where the economy is in the cycle, what the Fed is likely to do, what the biggest risk is, and what the data is telling investors to watch most closely.

---

*Data sources: FRED, Finnhub, Yahoo Finance. All market data as of latest available.*

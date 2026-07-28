---
name: tq-gamma
description: Dealer-gamma options positioning — call/put walls and net GEX sign
arguments:
  - name: symbol
    description: "Ticker with a liquid options market (e.g. SPY, QQQ). Defaults to SPY."
    required: false
---

Call `terminalq_get_dealer_gamma` with the symbol ("$ARGUMENTS", default SPY).

Present: spot price, the **call wall** (likely resistance), the **put wall** (likely support), the put/call open-interest ratio, and the net dealer-gamma sign.

Then explain in plain English: positive net gamma = dealers dampen volatility and price tends to pin toward the call wall; negative net gamma = dealers amplify moves, so sharp swings and air pockets are more likely. Tie it to the current tape — does positioning explain why price is stuck or why it might break? Be explicit that these are estimates from free Yahoo option chains (directional, not a paid GEX feed), and if the tool returns an error it's usually a Yahoo rate limit — say so and suggest retrying.

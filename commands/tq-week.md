---
name: tq-week
description: Weekly digest — narrate how the regime evolved across recent FR reports
arguments:
  - name: n
    description: "How many recent reports to cover (default 7)."
    required: false
---

Call `terminalq_load_recent_reports` (n = "$ARGUMENTS" if a number was given, else 7).

Write the **arc**, not a snapshot. Using the `snapshot_trend` (structured numbers) for the spine and the per-report `sections` (regime scores, what-changed, synthesis) for the narrative:

1. **The week in one paragraph** — where the regime started, what moved it, where it ended. Lead with the two regime scores' path (e.g. "Crypto Regime 53 → 49 → …").
2. **What actually changed** — the 3-5 metrics that moved most across the window (BTC, fear/greed, yields, VIX, ETF flows, stablecoins), each with a one-line plain-English read of *why it matters*, per the report-style rule.
3. **Follow-ups** — for each "watch this" item the earlier reports flagged, say whether it resolved, worsened, or is still pending.
4. **Where we are now & what to watch next week** — one tight synthesis paragraph.

If only one report exists, say so — there's no arc yet, just establish the baseline. Every number must come from the tool result; never invent a value.

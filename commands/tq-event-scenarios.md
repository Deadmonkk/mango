---
name: tq-event-scenarios
description: Pre-compute the reaction function for this week's high-impact events
arguments:
  - name: days
    description: "Look-ahead window in days (default 7)."
    required: false
---

Call `terminalq_get_event_scenarios` (days = "$ARGUMENTS" if a number was given, else 7).

For each upcoming event, write its **reaction function** as a short block:

- **Event | Date | Prior** — use the `anchor.current` value as the prior where present (real number only; "—" if none).
- **If hotter / stronger than expected:** what it does to the Fed path, rates, the dollar, and risk assets (stocks/gold/crypto) — tied to the `regime_context` (current scores, fed path, 10y, HY spread, VIX).
- **If cooler / weaker:** the opposite case.
- **Net:** which way the current regime is more exposed, so you know your reaction before the print.

Anchor every scenario to the regime context provided — a hot CPI into a market already pricing hikes is different from one into a cutting cycle. If `regime_context` is empty, note that running FR first (to record a snapshot) sharpens the scenarios. Don't invent events or priors not in the tool result.

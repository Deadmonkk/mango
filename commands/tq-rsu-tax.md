---
name: tq-rsu-tax
description: RSU vest tax-timing estimates and the sell-vs-hold decision
arguments:
  - name: marginal_rate
    description: "Optional assumed combined fed+state ordinary-income rate (e.g. 0.35). Defaults to 0.32."
    required: false
---

Call `terminalq_get_rsu_tax_analysis`. If the user passed a rate in "$ARGUMENTS", pass it as `marginal_rate`.

Present the upcoming vests as a table: **Date | Gross | Est. Tax | Net | Days Until**, then the upcoming totals.

Then give the plain-English guidance the tool returns: the ordinary-income tax at vest is owed whether you sell or hold, so the real decision is diversify (sell) vs concentration risk (hold) for upside taxed later at the lower long-term capital-gains rate. State clearly that these are **estimates using assumed rates, not tax advice**, and depend on bracket, state, and withholding — confirm with a CPA. Remind the user they can override the rate (e.g. `/tq-rsu-tax 0.37`).

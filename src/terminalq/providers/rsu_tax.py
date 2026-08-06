"""RSU tax-timing estimates from your vesting schedule (local data, no key).

RSUs are taxed as ordinary income at vest on their full fair-market value —
that bill is owed whether you sell or hold. The real decision a vest forces is:
sell now and diversify, or hold and accept single-stock concentration risk for
upside that would later be taxed at the (lower) long-term capital-gains rate.
This turns the raw vesting schedule into that decision, in dollars.

These are estimates using assumed rates, NOT tax advice. Real liability depends
on your bracket, state, other income, and withholding — confirm with a CPA.
"""

from datetime import date, datetime

from terminalq.ext_settings import RSU_DEFAULT_LTCG_RATE, RSU_DEFAULT_MARGINAL_RATE
from terminalq.mango.logging import log
from terminalq.providers.portfolio import load_rsu_schedule


def _parse_dollars(text: str) -> float | None:
    try:
        cleaned = text.replace("$", "").replace(",", "").strip()
        return float(cleaned) if cleaned else None
    except (ValueError, AttributeError):
        return None


def _parse_date(text: str) -> date | None:
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


async def get_rsu_tax_analysis(
    marginal_rate: float = RSU_DEFAULT_MARGINAL_RATE,
    ltcg_rate: float = RSU_DEFAULT_LTCG_RATE,
) -> dict:
    """Estimate tax on upcoming RSU vests and frame the sell-vs-hold decision.

    Args:
        marginal_rate: Assumed combined federal+state ordinary-income rate (0-1).
        ltcg_rate: Assumed long-term capital-gains rate (0-1).

    Returns:
        Dict with per-vest tax estimates, upcoming totals, and plain-English
        guidance — or an error dict if no RSU schedule is configured.
    """
    if not 0 <= marginal_rate < 1 or not 0 <= ltcg_rate < 1:
        return {"error": "Rates must be between 0 and 1", "source": "rsu_tax (local)"}

    try:
        schedule = load_rsu_schedule()
    except Exception as e:  # provider contract: never raise
        log.warning("RSU schedule load failed: %s", e)
        return {"error": "Could not read RSU schedule", "source": "rsu_tax (local)"}

    if not schedule:
        return {
            "error": "No RSU schedule found. Add ~/.terminalq/rsu-schedule.md (run /tq-ingest).",
            "source": "rsu_tax (local)",
        }

    today = date.today()
    vests = []
    for entry in schedule:
        vest_date = _parse_date(entry.get("date", ""))
        gross = _parse_dollars(entry.get("est_value", ""))
        if vest_date is None or gross is None:
            continue
        ordinary_tax = round(gross * marginal_rate, 0)
        vests.append(
            {
                "date": entry["date"],
                "grant": entry.get("grant", ""),
                "gross_value": round(gross, 0),
                "est_ordinary_tax": ordinary_tax,
                "net_after_tax": round(gross - ordinary_tax, 0),
                "days_until": (vest_date - today).days,
                "upcoming": vest_date >= today,
            }
        )

    if not vests:
        return {"error": "RSU schedule had no parseable vest rows", "source": "rsu_tax (local)"}

    upcoming = [v for v in vests if v["upcoming"]]
    total_gross = round(sum(v["gross_value"] for v in upcoming), 0)
    total_tax = round(sum(v["est_ordinary_tax"] for v in upcoming), 0)
    total_net = round(total_gross - total_tax, 0)

    next_vest = min(upcoming, key=lambda v: v["days_until"]) if upcoming else None
    guidance = (
        (
            f"Next vest: {next_vest['date']} (~{next_vest['days_until']} days) — "
            f"${next_vest['gross_value']:,.0f} gross, ~${next_vest['est_ordinary_tax']:,.0f} tax withheld "
            f"at vest, ~${next_vest['net_after_tax']:,.0f} net in employer shares. "
            if next_vest
            else ""
        )
        + "That ordinary-income tax is owed regardless of selling. The decision is what to do "
        "with the net shares: SELL to diversify out of single-stock concentration (the prudent "
        "default when employer stock is already a large share of net worth), or HOLD and accept "
        f"concentration risk for appreciation later taxed at the lower ~{ltcg_rate:.0%} long-term "
        "capital-gains rate. Higher concentration tilts the call toward selling."
    )

    return {
        "assumptions": {"marginal_rate": marginal_rate, "ltcg_rate": ltcg_rate},
        "upcoming_vests": upcoming,
        "all_vests": vests,
        "upcoming_totals": {
            "gross_value": total_gross,
            "est_ordinary_tax": total_tax,
            "net_after_tax": total_net,
        },
        "guidance": guidance,
        "note": (
            "Estimates only, NOT tax advice. RSUs are taxed as ordinary income on full value "
            "at vest; only post-vest appreciation qualifies for long-term capital-gains rates "
            "(if held >1 year). Actual liability depends on bracket, state, and withholding — "
            "confirm with a CPA. Override rates via the marginal_rate / ltcg_rate arguments."
        ),
        "source": "rsu_tax (local)",
    }

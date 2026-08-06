"""Event-reaction scaffolding — upcoming releases anchored to current readings.

Pre-computing "if CPI prints hot vs cool, here's what flips" is a reasoning task,
but it needs the right inputs assembled: the upcoming high-impact events, and the
current regime context the reaction would land in. This bundles those — the FRED
release calendar plus the latest recorded snapshot (scores + headline metrics) —
so the command can write the scenario for each event against today's regime,
instead of reasoning in a vacuum.
"""

from terminalq.mango.logging import log

from terminalq.history import latest_snapshot_per_day
from terminalq.providers import fred_ext as fred  # owns get_release_calendar

# Which current snapshot reading anchors each event type's "prior".
_EVENT_ANCHORS = {
    "cpi": ("cpi_mom", "last CPI m/m"),
    "claims": ("claims_k", "last initial claims (000s)"),
    "jobs": ("claims_k", "labor proxy: last claims (000s)"),
    "payroll": ("claims_k", "labor proxy: last claims (000s)"),
}


def _anchor_for(event_name: str, snapshot: dict) -> dict | None:
    name = event_name.lower()
    for keyword, (key, label) in _EVENT_ANCHORS.items():
        if keyword in name and snapshot.get(key) is not None:
            return {"current": snapshot[key], "label": label}
    return None


async def get_event_scenarios(days: int = 7) -> dict:
    """Bundle upcoming high-impact events with the current regime snapshot.

    Args:
        days: Look-ahead window for the release calendar.

    Returns:
        Dict with the upcoming events (each anchored to its current reading where
        known) and the latest regime context, for the command to turn into hot/cool
        reaction scenarios — or an error if the calendar is unavailable.
    """
    calendar = await fred.get_release_calendar(days)
    if "error" in calendar:
        log.warning("event_scenarios: calendar unavailable")
        return {
            "error": calendar["error"],
            "hint": "Event scenarios need the FRED release calendar (FRED_API_KEY).",
            "source": "event_scenarios (local + fred)",
        }

    snapshots = latest_snapshot_per_day()
    latest = snapshots[-1] if snapshots else {}

    events = []
    for ev in calendar.get("events", []):
        events.append({**ev, "anchor": _anchor_for(ev.get("event", ""), latest)})

    regime_context = {
        k: latest.get(k)
        for k in ("date", "equity_regime", "crypto_regime", "fed_path", "ten_year", "hy_spread", "vix")
        if latest.get(k) is not None
    }

    return {
        "events": events,
        "regime_context": regime_context,
        "have_snapshot": bool(latest),
        "note": (
            "For each event, write the reaction function: what a hotter-vs-cooler print "
            "means for the Fed path, rates, and risk assets — tied to the current regime "
            "context. 'anchor' gives the most recent actual reading to frame the prior. "
            "If regime_context is empty, run FR first to record a snapshot."
        ),
        "source": "event_scenarios (local + fred)",
    }

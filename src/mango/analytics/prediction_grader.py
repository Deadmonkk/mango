"""Grade the prediction ledger — settle due calls against what actually happened.

Each report makes implicit calls; the ledger logs them as dated, falsifiable
predictions. This settles the ones that have come due by fetching the settling
symbol's close **on the due date** and comparing it against the baseline
recorded at creation. Over time the accuracy figure shows where the model is
systematically right or wrong — the input for trusting (or discounting) a given
signal.

Settling on the due date rather than on the day grading runs is the point: the
ledger is graded lazily, whenever the next report happens to run, so pricing at
run-time would quietly turn a 30-day call into a 45-day one and make the
accuracy record measure a horizon nobody predicted.
"""

from datetime import date, datetime

from mango.core.logging import log

from mango.history import load_predictions, update_prediction
from mango.core import historical

# A prediction must settle on the price at its DUE date, not at whatever date
# grading happens to run. Grading is often late (the ledger settles on the next
# report run after the due date), so fetching "the latest close" would silently
# stretch a 30-day call into a 45- or 60-day one and record the wrong horizon.
# Fetch a window long enough to reach back to the due date, then take the close
# on that date — or the last trading day before it, for weekends and holidays.
_PERIOD_LADDER = ((25, "1mo"), (80, "3mo"), (170, "6mo"), (350, "1y"), (700, "2y"), (1800, "5y"))
_LOOKBACK_BUFFER_DAYS = 7  # slack for weekends/holidays around the due date


def _period_for(days_back: int) -> str:
    """Shortest yfinance period that still reaches ``days_back`` days into the past."""
    needed = days_back + _LOOKBACK_BUFFER_DAYS
    for limit, period in _PERIOD_LADDER:
        if needed <= limit:
            return period
    return "max"


async def _close_on_or_before(symbol: str, due: date) -> tuple[float | None, str | None]:
    """Closing price on ``due``, or the last trading day before it.

    Returns:
        ``(close, date)``, or ``(None, None)`` when the source failed or the
        history does not reach back far enough to cover the due date.
    """
    days_back = max((date.today() - due).days, 0)
    result = await historical.get_historical(symbol, period=_period_for(days_back), interval="1d")
    if "error" in result:
        return None, None

    due_iso = due.isoformat()
    on_or_before = [p for p in result.get("prices", []) if p.get("close") and p.get("date", "") <= due_iso]
    if not on_or_before:
        return None, None

    settled_on = max(on_or_before, key=lambda p: p["date"])
    return settled_on["close"], settled_on["date"]


def _settle(direction: str, baseline: float, current: float) -> bool | None:
    """Did the move match the call?

    Returns None for an unrecognised direction, so a malformed record is marked
    ``ungraded`` rather than being counted as a wrong call — a data-entry typo
    should not drag down the accuracy figure.
    """
    if direction in ("up", "above"):
        return current > baseline
    if direction in ("down", "below"):
        return current < baseline
    return None


async def grade_open_predictions() -> dict:
    """Settle every open prediction whose due date has passed.

    Returns:
        Dict with counts (graded, correct, incorrect, ungraded), the running
        accuracy over all settled calls, and the per-prediction results.
    """
    predictions = load_predictions()
    if not predictions:
        return {
            "error": "No predictions logged yet. Use terminalq_log_prediction to start the ledger.",
            "source": "prediction_grader (local + yahoo)",
        }

    today = date.today()
    newly_graded = []
    close_cache: dict[tuple[str, str], tuple[float | None, str | None]] = {}

    for pred in predictions:
        if pred.get("status") != "open":
            continue
        try:
            due = datetime.strptime(pred.get("due", ""), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if due > today:
            continue  # not due yet

        baseline = pred.get("baseline")
        symbol = pred.get("symbol", "")
        if baseline is None or not symbol:
            update_prediction(pred["id"], status="ungraded", outcome={"reason": "no baseline/symbol"})
            newly_graded.append({**pred, "result": "ungraded"})
            continue

        # Cache on (symbol, due) — two calls on the same symbol with different
        # due dates settle at different prices.
        cache_key = (symbol, pred["due"])
        if cache_key not in close_cache:
            close_cache[cache_key] = await _close_on_or_before(symbol, due)
        current, settled_on = close_cache[cache_key]
        if current is None:
            log.warning("grade: no price for %s at/before %s", symbol, pred["due"])
            continue  # leave open; data unavailable this run

        correct = _settle(pred.get("direction", ""), baseline, current)
        if correct is None:
            update_prediction(
                pred["id"],
                status="ungraded",
                outcome={"reason": f"unrecognised direction {pred.get('direction', '')!r}"},
            )
            newly_graded.append({**pred, "result": "ungraded"})
            continue

        change_pct = round((current / baseline - 1) * 100, 2) if baseline else None
        status = "correct" if correct else "incorrect"
        update_prediction(
            pred["id"],
            status=status,
            outcome={
                "current": current,
                "baseline": baseline,
                "change_pct": change_pct,
                "settled_on": settled_on,
            },
        )
        newly_graded.append(
            {
                **pred,
                "result": status,
                "current": current,
                "change_pct": change_pct,
                "settled_on": settled_on,
            }
        )

    settled = [p for p in load_predictions() if p.get("status") in ("correct", "incorrect")]
    correct_n = sum(1 for p in settled if p["status"] == "correct")
    accuracy = round(correct_n / len(settled) * 100, 1) if settled else None
    open_n = sum(1 for p in load_predictions() if p.get("status") == "open")

    return {
        "newly_graded": newly_graded,
        "totals": {
            "settled": len(settled),
            "correct": correct_n,
            "incorrect": len(settled) - correct_n,
            "still_open": open_n,
            "accuracy_pct": accuracy,
        },
        "note": (
            "Each call settles on the close at its DUE date (or the last trading day before "
            "it), not the price on the day grading ran — so a late grading run still measures "
            "the horizon that was actually predicted. Accuracy = correct / settled across the "
            "whole ledger. A small sample is anecdote, not a track record. Predictions with no "
            "baseline/symbol, or an unrecognised direction, are marked 'ungraded' and excluded. "
            "Use this to weight which signals to trust: persistently wrong calls argue for "
            "down-weighting their source signal."
        ),
        "source": "prediction_grader (local + yahoo)",
    }

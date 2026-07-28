"""Grade the prediction ledger — settle due calls against what actually happened.

Each report makes implicit calls; the ledger logs them as dated, falsifiable
predictions. This settles the ones that have come due by fetching the current
price of the settling symbol and comparing against the baseline recorded at
creation. Over time the accuracy figure shows where the model is systematically
right or wrong — the input for trusting (or discounting) a given signal.
"""

from datetime import date, datetime

from terminalq.history import load_predictions, update_prediction
from terminalq.logging_config import log
from terminalq.providers import historical


async def _latest_close(symbol: str) -> float | None:
    result = await historical.get_historical(symbol, period="5d", interval="1d")
    if "error" in result:
        return None
    prices = [p["close"] for p in result.get("prices", []) if p.get("close")]
    return prices[-1] if prices else None


def _settle(direction: str, baseline: float, current: float) -> bool:
    if direction in ("up", "above"):
        return current > baseline
    if direction in ("down", "below"):
        return current < baseline
    return False


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
    close_cache: dict[str, float | None] = {}

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

        if symbol not in close_cache:
            close_cache[symbol] = await _latest_close(symbol)
        current = close_cache[symbol]
        if current is None:
            log.warning("grade: no price for %s", symbol)
            continue  # leave open; data unavailable this run

        correct = _settle(pred.get("direction", ""), baseline, current)
        change_pct = round((current / baseline - 1) * 100, 2) if baseline else None
        status = "correct" if correct else "incorrect"
        update_prediction(
            pred["id"],
            status=status,
            outcome={"current": current, "baseline": baseline, "change_pct": change_pct},
        )
        newly_graded.append({**pred, "result": status, "current": current, "change_pct": change_pct})

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
            "Accuracy = correct / settled across the whole ledger. A small sample is anecdote, "
            "not a track record. Predictions with no baseline/symbol are marked 'ungraded'. "
            "Use this to weight which signals to trust: persistently wrong calls argue for "
            "down-weighting their source signal."
        ),
        "source": "prediction_grader (local + yahoo)",
    }

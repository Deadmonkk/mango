"""Tests for mango.analytics.prediction_grader — settling due predictions."""

from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from mango import history
from mango.analytics import prediction_grader


@pytest.fixture(autouse=True)
def temp_history(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(history, "SNAPSHOTS_FILE", tmp_path / "snapshots.jsonl")
    monkeypatch.setattr(history, "PREDICTIONS_FILE", tmp_path / "predictions.jsonl")


def _hist(last_close: float) -> dict:
    return {"symbol": "X", "prices": [{"date": "2026-06-10", "close": last_close}], "source": "yahoo_finance"}


def _series(closes_by_date: dict[str, float]) -> dict:
    """A price history spanning several dates, oldest first."""
    return {
        "symbol": "X",
        "prices": [{"date": d, "close": c} for d, c in sorted(closes_by_date.items())],
        "source": "yahoo_finance",
    }


async def test_grade_settles_due_prediction_correct():
    # An up-call with baseline 100, due in the past; current price 130 -> correct.
    pred = history.log_prediction("BTC up", "BTC-USD", "up", horizon_days=-5, baseline=100.0)
    assert history.load_predictions()[0]["due"] < history.load_predictions()[0]["created"]

    with patch.object(prediction_grader.historical, "get_historical", new=AsyncMock(return_value=_hist(130.0))):
        result = await prediction_grader.grade_open_predictions()

    assert result["totals"]["settled"] == 1
    assert result["totals"]["correct"] == 1
    assert result["totals"]["accuracy_pct"] == 100.0
    graded = history.load_predictions()[0]
    assert graded["status"] == "correct"
    assert graded["outcome"]["change_pct"] == 30.0
    # id preserved
    assert graded["id"] == pred["id"]


async def test_grade_marks_incorrect():
    history.log_prediction("BTC up", "BTC-USD", "up", horizon_days=-5, baseline=100.0)
    with patch.object(prediction_grader.historical, "get_historical", new=AsyncMock(return_value=_hist(80.0))):
        result = await prediction_grader.grade_open_predictions()
    assert result["totals"]["incorrect"] == 1
    assert result["totals"]["accuracy_pct"] == 0.0


async def test_grade_leaves_future_predictions_open():
    history.log_prediction("future", "BTC-USD", "up", horizon_days=30, baseline=100.0)
    with patch.object(prediction_grader.historical, "get_historical", new=AsyncMock(return_value=_hist(130.0))):
        result = await prediction_grader.grade_open_predictions()
    assert result["totals"]["still_open"] == 1
    assert result["totals"]["settled"] == 0


async def test_grade_no_predictions_returns_error():
    result = await prediction_grader.grade_open_predictions()
    assert "error" in result


async def test_grade_ungraded_without_baseline():
    history.log_prediction("no baseline", "BTC-USD", "up", horizon_days=-5, baseline=None)
    with patch.object(prediction_grader.historical, "get_historical", new=AsyncMock(return_value=_hist(130.0))):
        result = await prediction_grader.grade_open_predictions()
    assert history.load_predictions()[0]["status"] == "ungraded"
    assert result["totals"]["settled"] == 0


async def test_grade_settles_on_due_date_not_run_date():
    """A late grading run must price the DUE date, not the day it happens to run.

    Regression test: pricing at run-time silently stretched the horizon, so a
    30-day call graded two weeks late measured a 44-day return instead.
    """
    # Due 10 days ago. Price at the due date fell (95 < 100 -> the "up" call is
    # wrong); price today has since rallied well above baseline. Grading on the
    # run date would wrongly score this correct.
    due = date.today() - timedelta(days=10)
    history.log_prediction("X up", "X", "up", horizon_days=-10, baseline=100.0)

    series = _series(
        {
            (due - timedelta(days=1)).isoformat(): 99.0,
            due.isoformat(): 95.0,
            (due + timedelta(days=5)).isoformat(): 120.0,
            date.today().isoformat(): 130.0,
        }
    )
    with patch.object(prediction_grader.historical, "get_historical", new=AsyncMock(return_value=series)):
        result = await prediction_grader.grade_open_predictions()

    assert result["totals"]["incorrect"] == 1, "should settle on the 95.0 due-date close"
    graded = history.load_predictions()[0]
    assert graded["outcome"]["current"] == 95.0
    assert graded["outcome"]["settled_on"] == due.isoformat()


async def test_grade_falls_back_to_last_trading_day_before_due():
    """A due date on a weekend/holiday settles on the prior trading close."""
    due = date.today() - timedelta(days=10)
    history.log_prediction("X up", "X", "up", horizon_days=-10, baseline=100.0)

    # No print on the due date itself; the last close before it is 110.
    series = _series(
        {
            (due - timedelta(days=2)).isoformat(): 110.0,
            (due + timedelta(days=3)).isoformat(): 50.0,
        }
    )
    with patch.object(prediction_grader.historical, "get_historical", new=AsyncMock(return_value=series)):
        await prediction_grader.grade_open_predictions()

    graded = history.load_predictions()[0]
    assert graded["outcome"]["current"] == 110.0
    assert graded["outcome"]["settled_on"] == (due - timedelta(days=2)).isoformat()
    assert graded["status"] == "correct"


async def test_grade_leaves_open_when_history_predates_due_date():
    """If the fetched window never reaches the due date, leave the call open."""
    history.log_prediction("X up", "X", "up", horizon_days=-10, baseline=100.0)
    future_only = _series({(date.today() + timedelta(days=1)).isoformat(): 130.0})

    with patch.object(prediction_grader.historical, "get_historical", new=AsyncMock(return_value=future_only)):
        result = await prediction_grader.grade_open_predictions()

    assert result["totals"]["still_open"] == 1
    assert result["totals"]["settled"] == 0


async def test_unrecognised_direction_is_ungraded_not_incorrect():
    """A typo'd direction must not be counted as a wrong call."""
    history.log_prediction("X higher", "X", "higher", horizon_days=-5, baseline=100.0)
    with patch.object(prediction_grader.historical, "get_historical", new=AsyncMock(return_value=_hist(130.0))):
        result = await prediction_grader.grade_open_predictions()

    assert history.load_predictions()[0]["status"] == "ungraded"
    assert result["totals"]["settled"] == 0
    assert result["totals"]["incorrect"] == 0


@pytest.mark.parametrize(
    ("days_back", "expected"),
    [(0, "1mo"), (17, "1mo"), (30, "3mo"), (100, "6mo"), (300, "1y"), (600, "2y"), (5000, "max")],
)
def test_period_ladder_reaches_far_enough_back(days_back, expected):
    assert prediction_grader._period_for(days_back) == expected

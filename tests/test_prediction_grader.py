"""Tests for terminalq.analytics.prediction_grader — settling due predictions."""

from unittest.mock import AsyncMock, patch

import pytest

from terminalq import history
from terminalq.analytics import prediction_grader


@pytest.fixture(autouse=True)
def temp_history(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(history, "SNAPSHOTS_FILE", tmp_path / "snapshots.jsonl")
    monkeypatch.setattr(history, "PREDICTIONS_FILE", tmp_path / "predictions.jsonl")


def _hist(last_close: float) -> dict:
    return {"symbol": "X", "prices": [{"date": "2026-06-10", "close": last_close}], "source": "yahoo_finance"}


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

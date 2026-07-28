"""Tests for terminalq.history — append-only snapshot + prediction store."""

import pytest

from terminalq import history


@pytest.fixture(autouse=True)
def temp_history(tmp_path, monkeypatch):
    """Redirect the history store to a temp dir for every test."""
    monkeypatch.setattr(history, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(history, "SNAPSHOTS_FILE", tmp_path / "snapshots.jsonl")
    monkeypatch.setattr(history, "PREDICTIONS_FILE", tmp_path / "predictions.jsonl")


def test_record_snapshot_stores_only_non_none():
    record = history.record_snapshot(
        snapshot_date="2026-06-11",
        crypto_regime=49,
        equity_regime=42,
        btc=62700.0,
        eth=None,  # dropped
    )
    assert record["date"] == "2026-06-11"
    assert record["crypto_regime"] == 49
    assert "eth" not in record
    assert "recorded_at" in record

    loaded = history.load_snapshots()
    assert len(loaded) == 1
    assert loaded[0]["btc"] == 62700.0


def test_latest_snapshot_per_day_dedupes():
    history.record_snapshot(snapshot_date="2026-06-11", crypto_regime=53)
    history.record_snapshot(snapshot_date="2026-06-11", crypto_regime=49)  # later same day wins
    history.record_snapshot(snapshot_date="2026-06-10", crypto_regime=55)

    daily = history.latest_snapshot_per_day()
    assert len(daily) == 2
    # sorted by date; 06-11 keeps the last-written value
    assert daily[0]["date"] == "2026-06-10"
    assert daily[1]["crypto_regime"] == 49


def test_load_snapshots_skips_corrupt_lines():
    history.record_snapshot(snapshot_date="2026-06-11", crypto_regime=49)
    history.SNAPSHOTS_FILE.open("a", encoding="utf-8").write("not json\n")
    assert len(history.load_snapshots()) == 1  # corrupt line skipped


def test_prediction_lifecycle():
    pred = history.log_prediction(
        claim="BTC reclaims 200d", symbol="BTC-USD", direction="up", horizon_days=30, baseline=62700.0
    )
    assert pred["status"] == "open"
    assert pred["direction"] == "up"
    assert pred["due"] > pred["created"]

    assert history.update_prediction(pred["id"], status="correct") is True
    assert history.update_prediction("nonexistent", status="x") is False

    loaded = history.load_predictions()
    assert len(loaded) == 1  # latest state per id
    assert loaded[0]["status"] == "correct"
    assert "graded_at" in loaded[0]

"""Tests for terminalq.backfill — best-effort parse of FR markdown reports."""

import pytest

from terminalq import backfill, history

_REPORT_NEW = """# FR June 11, 2026

| Score | Reading | Band |
|---|---|---|
| **Equity Regime** | **42 / 100** | Mid-cycle |
| **Crypto Regime** | **~49 / 100** *(provisional)* | Neutral |

| Metric | Prior | This run | Read |
|---|---|---|---|
| S&P 500 | 7,300 | **7,292.9** | Same |
| VIX | 21.0 | **21.49** | Same |
| BTC / ETH price | x | **$62,700 / $1,646 (Yahoo)** | Recovered |
| Crypto Fear & Greed | 12 | **12** | Extreme Fear |
"""

_REPORT_OLD = """# FR June 10, 2026

| Metric | Prior | This run | Read |
|---|---|---|---|
| S&P 500 | 7,350 | **7,267** | Pullback |
| BTC | $61,877 | **$61,574** | Lower |

Unchanged: Fear & Greed 9, AAII neutral.
"""


@pytest.fixture(autouse=True)
def temp_history(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(history, "SNAPSHOTS_FILE", tmp_path / "snapshots.jsonl")
    monkeypatch.setattr(history, "PREDICTIONS_FILE", tmp_path / "predictions.jsonl")


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_new_format(tmp_path):
    parsed = backfill.parse_report(_write(tmp_path, "2026-06-11-fr.md", _REPORT_NEW))
    assert parsed["snapshot_date"] == "2026-06-11"
    assert parsed["equity_regime"] == 42
    assert parsed["crypto_regime"] == 49
    assert parsed["spx"] == 7292.9
    assert parsed["vix"] == 21.49
    assert parsed["btc"] == 62700.0
    assert parsed["eth"] == 1646.0
    assert parsed["fear_greed"] == 12.0


def test_parse_old_format_no_scores(tmp_path):
    parsed = backfill.parse_report(_write(tmp_path, "2026-06-10-fr.md", _REPORT_OLD))
    assert "equity_regime" not in parsed  # old format has no score table
    assert parsed["spx"] == 7267.0
    assert parsed["btc"] == 61574.0
    assert parsed["fear_greed"] == 9.0  # prose fallback


def test_backfill_records_to_store(tmp_path):
    _write(tmp_path, "2026-06-10-fr.md", _REPORT_OLD)
    _write(tmp_path, "2026-06-11-fr.md", _REPORT_NEW)
    rows = backfill.backfill(tmp_path)
    assert len(rows) == 2
    snaps = history.latest_snapshot_per_day()
    assert {s["date"] for s in snaps} == {"2026-06-10", "2026-06-11"}


def test_to_float_handles_markup():
    assert backfill._to_float("**$4,102**") == 4102.0
    assert backfill._to_float("~$4,100") == 4100.0
    assert backfill._to_float("2.80%") == 2.80
    assert backfill._to_float("garbage") is None

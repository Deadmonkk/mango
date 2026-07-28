"""Tests for terminalq.providers.reports — recent-report loader for the digest."""

from unittest.mock import patch

import pytest

from terminalq.providers import reports

_REPORT = """# FR June 11, 2026

## Regime & Bottom Scores
Equity 42, Crypto 49.

## 0. What Changed Since Last Run
BTC recovered via fallback.

## 1. Macro Snapshot
Strong economy.

## 12. Synthesis — The Big Picture
Late-cycle, expensive, crypto in retreat.
"""


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    pass


def test_extract_sections_picks_narrative_only():
    sections = reports._extract_sections(_REPORT)
    assert "regime" in sections
    assert "what changed" in sections
    assert "synthesis" in sections
    # Non-narrative sections are excluded
    assert "macro" not in sections
    assert "Strong economy" not in sections.get("synthesis", "")


async def test_load_recent_reports(tmp_path, monkeypatch):
    (tmp_path / "2026-06-10-fr.md").write_text(_REPORT, encoding="utf-8")
    (tmp_path / "2026-06-11-fr.md").write_text(_REPORT, encoding="utf-8")
    monkeypatch.setattr(reports, "REPORTS_DIR", tmp_path)

    with patch.object(reports, "latest_snapshot_per_day", return_value=[{"date": "2026-06-11", "crypto_regime": 49}]):
        result = await reports.load_recent_reports(n=7)

    assert result["count"] == 2
    assert result["reports"][0]["date"] == "2026-06-10"
    assert "synthesis" in result["reports"][0]["sections"]
    assert result["snapshot_trend"][0]["crypto_regime"] == 49


async def test_load_recent_reports_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "REPORTS_DIR", tmp_path / "nope")
    result = await reports.load_recent_reports()
    assert "error" in result


async def test_load_recent_reports_empty_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "REPORTS_DIR", tmp_path)
    result = await reports.load_recent_reports()
    assert "error" in result

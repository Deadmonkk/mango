"""Tests for mango.analytics.fred_archive — permanent local FRED history archive."""

import json

import pytest

from mango.analytics import fred_archive


@pytest.fixture(autouse=True)
def tmp_archive_dir(tmp_path, monkeypatch):
    """Isolate every test from the real ~/.terminalq/history/fred_archive/."""
    monkeypatch.setattr(fred_archive, "ARCHIVE_DIR", tmp_path)
    return tmp_path


def test_merge_with_no_seed_or_prior_archive_just_persists_live():
    dates, values = fred_archive.merge_and_persist("FAKESERIES", ["2026-01-01", "2026-01-02"], [1.0, 2.0])

    assert dates == ["2026-01-01", "2026-01-02"]
    assert values == [1.0, 2.0]
    saved = json.loads((fred_archive.ARCHIVE_DIR / "FAKESERIES.json").read_text())
    assert saved["values"] == [{"date": "2026-01-01", "value": 1.0}, {"date": "2026-01-02", "value": 2.0}]


def test_merge_extends_forward_across_repeated_calls():
    fred_archive.merge_and_persist("FAKESERIES", ["2026-01-01"], [1.0])
    dates, values = fred_archive.merge_and_persist("FAKESERIES", ["2026-01-02"], [2.0])

    # Second call's live fetch only returned day 2, but the archive from
    # call 1 should still be there — this is the "keeps growing forever" guarantee.
    assert dates == ["2026-01-01", "2026-01-02"]
    assert values == [1.0, 2.0]


def test_merge_uses_seed_file_for_pre_restriction_history():
    seed = {
        "series_id": "BAMLH0A0HYM2",
        "values": [{"date": "1996-12-31", "value": 3.13}, {"date": "2020-03-23", "value": 10.87}],
    }
    (fred_archive.ARCHIVE_DIR / "BAMLH0A0HYM2_seed.json").write_text(json.dumps(seed))

    dates, values = fred_archive.merge_and_persist("BAMLH0A0HYM2", ["2026-07-06"], [2.75])

    assert dates[0] == "1996-12-31"
    assert values[0] == 3.13
    assert dates[-1] == "2026-07-06"
    assert values[-1] == 2.75


def test_live_value_overrides_stale_archived_value_for_same_date():
    fred_archive.merge_and_persist("FAKESERIES", ["2026-01-01"], [1.0])
    # A later run re-fetches the same date with a revised value (FRED
    # sometimes revises recent observations) — live should win.
    dates, values = fred_archive.merge_and_persist("FAKESERIES", ["2026-01-01"], [1.5])

    assert dates == ["2026-01-01"]
    assert values == [1.5]


def test_corrupt_archive_file_does_not_crash():
    (fred_archive.ARCHIVE_DIR / "BADSERIES.json").write_text("not valid json{{{")

    dates, values = fred_archive.merge_and_persist("BADSERIES", ["2026-01-01"], [1.0])

    assert dates == ["2026-01-01"]
    assert values == [1.0]

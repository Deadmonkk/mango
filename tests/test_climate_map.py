"""The climate map is data; updating it belongs to code.

Guards the two failure modes that actually bit: a nulled precipitation
percentage reported as a source failure (2026-08-10), and a partial update to a
document published at a stable URL.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from climate_map import (  # noqa: E402
    PLACEMENTS,
    MapStructureError,
    is_flagged,
    is_temp_flag,
    precip_text,
    render_table,
    replace_block,
    severity,
    update_map,
)


def _region(key: str, **overrides: object) -> dict:
    base = {
        "label": key.replace("_", " ").title(),
        "temp_anomaly_c": 0.5,
        "precip_anomaly_pct": 10.0,
        "total_precip_mm": 50.0,
        "normal_precip_mm": 45.0,
        "signal": "normal — within typical range for this time of year",
        "watch": {"commodities": ["x"]},
    }
    return {**base, **overrides}


def _payload(**overrides: dict) -> dict:
    regions = {k: _region(k) for k in PLACEMENTS}
    regions.update(overrides)
    return {"regions": regions}


def _doc() -> str:
    blocks = "\n".join(
        f"<!-- CLIMATE:{b}:START -->\nold\n<!-- CLIMATE:{b}:END -->"
        for b in ("CHIPS", "MARKERS", "SIDEBAR", "TABLE")
    )
    return f"<html><head><style>KEEP ME</style></head><body>\n{blocks}\n<p>tail</p></body></html>"


# --- precipitation: null is not failure ------------------------------------


def test_percentage_is_used_when_meaningful():
    assert precip_text(_region("x", precip_anomaly_pct=-61.3)) == "-61.3%"


def test_nulled_percentage_falls_back_to_millimetres():
    text = precip_text(_region("x", precip_anomaly_pct=None, total_precip_mm=11.0, normal_precip_mm=6.7))
    assert "11.0mm" in text and "6.7mm" in text
    assert "source failed" not in text


def test_genuine_absence_still_reports_a_failure():
    text = precip_text(_region("x", precip_anomaly_pct=None, total_precip_mm=None, normal_precip_mm=None))
    assert "source failed" in text


# --- flag classification ---------------------------------------------------


def test_flagged_is_read_from_the_providers_own_signal():
    assert is_flagged(_region("x", signal="FLAGGED — drier than normal by 61%"))
    assert not is_flagged(_region("x"))


def test_a_large_temperature_anomaly_marks_a_temperature_flag():
    region = _region("x", signal="FLAGGED — cooler than normal by 2.8°C", temp_anomaly_c=-2.75)
    assert is_temp_flag(region)


def test_a_precip_flag_is_not_a_temperature_flag():
    region = _region("x", signal="FLAGGED — drier than normal by 61%",
                     temp_anomaly_c=1.27, precip_anomaly_pct=-61.3)
    assert not is_temp_flag(region)


def test_severity_escalates_only_on_an_extreme_precip_anomaly():
    assert severity(_region("x")) == "normal"
    assert severity(_region("x", signal="FLAGGED — x", precip_anomaly_pct=-61.3)) == "high"
    assert severity(_region("x", signal="FLAGGED — x", precip_anomaly_pct=196.0)) == "extreme"


# --- block replacement -----------------------------------------------------


def test_update_rewrites_only_the_sentinel_blocks():
    doc = _doc()
    out = update_map(doc, _payload(), "August 10, 2026")
    assert "<style>KEEP ME</style>" in out
    assert "<p>tail</p>" in out
    assert "old" not in out


def test_all_twelve_regions_reach_the_table():
    out = render_table(_payload()["regions"])
    for key in PLACEMENTS:
        assert f'data-region="{key}"' in out


def test_flagged_regions_sort_first():
    payload = _payload(vietnam_coffee=_region("vietnam_coffee",
                                              signal="FLAGGED — wetter than normal by 97%",
                                              precip_anomaly_pct=96.8))
    rows = render_table(payload["regions"])
    assert rows.index("vietnam_coffee") < rows.index("us_corn_belt")


def test_a_missing_sentinel_fails_loudly():
    with pytest.raises(MapStructureError, match="exactly one"):
        replace_block("<html>no sentinels</html>", "TABLE", "body")


def test_a_duplicated_sentinel_fails_loudly():
    doc = _doc().replace(
        "<!-- CLIMATE:TABLE:START -->\nold\n<!-- CLIMATE:TABLE:END -->",
        "<!-- CLIMATE:TABLE:START -->\nold\n<!-- CLIMATE:TABLE:END -->" * 2,
    )
    with pytest.raises(MapStructureError, match="exactly one"):
        replace_block(doc, "TABLE", "body")


def test_an_empty_payload_never_blanks_the_map():
    with pytest.raises(MapStructureError, match="no regions"):
        update_map(_doc(), {"regions": {}}, "d")


def test_a_payload_missing_a_placed_region_fails_loudly():
    payload = _payload()
    payload["regions"].pop("us_corn_belt")
    with pytest.raises(MapStructureError, match="missing placed regions"):
        update_map(_doc(), payload, "d")


def test_update_is_idempotent():
    once = update_map(_doc(), _payload(), "d")
    assert update_map(once, _payload(), "d") == once

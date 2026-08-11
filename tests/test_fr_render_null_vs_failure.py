"""A provider's explicit null must never render as a source failure.

Regression guard for 2026-08-10. NASA POWER suppresses the precipitation
*percentage* wherever the climatological base is under ~10mm — a percent change
off 0.6mm is noise — and returns the absolute rainfall instead. The digest
folded that deliberate null into the FAIL sentinel, so three regions were
published as "data unavailable (source failed)" when the data had in fact been
returned. Mato Grosso in particular read as cold-and-unknown when it was
cold-and-WETTER-than-normal, which inverts the soy/corn interpretation.

The distinction under test: a path that does not resolve is a failure; a path
that resolves to null is the provider saying "not meaningful here".
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fr_render import (  # noqa: E402
    FAIL,
    MISSING,
    NOT_MEANINGFUL,
    Field,
    Section,
    render_region_table,
    render_table,
    resolve,
)

# --- resolve() distinguishes the three cases -------------------------------


def test_resolve_reports_ok_for_a_present_value():
    value, status = resolve({"a": {"b": 1.5}}, "a.b")
    assert (value, status) == (1.5, "ok")


def test_resolve_reports_null_for_an_explicit_none():
    value, status = resolve({"a": {"b": None}}, "a.b")
    assert status == "null"
    assert value is None


def test_resolve_reports_missing_for_an_unresolvable_path():
    value, status = resolve({"a": {}}, "a.nope")
    assert status == "missing"
    assert value is MISSING


def test_resolve_reports_missing_when_the_whole_source_failed():
    assert resolve({}, "a.b")[1] == "missing"


# --- render_table keeps the two apart --------------------------------------


def _one_field_table(payload: dict) -> str:
    section = Section("1", "T", (Field("Metric", "src", "a.b"),))
    return render_table({"src": payload}, section)


def test_render_table_marks_a_missing_path_as_a_failure():
    assert FAIL in _one_field_table({"a": {}})


def test_render_table_does_not_call_an_explicit_null_a_failure():
    rendered = _one_field_table({"a": {"b": None}})
    assert NOT_MEANINGFUL in rendered
    assert FAIL not in rendered, "an explicit null must not be reported as a source failure"


# --- the climate row that actually broke -----------------------------------


def _mato_grosso(**overrides: object) -> dict:
    """The live 2026-08-10 payload: percentage nulled, millimetres present."""
    base = {
        "label": "Mato Grosso, Brazil",
        "temp_anomaly_c": -2.75,
        "precip_anomaly_pct": None,
        "total_precip_mm": 11.0,
        "normal_precip_mm": 6.7,
        "signal": "FLAGGED — cooler than normal by 2.8°C",
        "watch": {"commodities": ["soybeans (ZS)"]},
    }
    return {**base, **overrides}


def test_nulled_precip_percentage_falls_back_to_millimetres():
    row = render_region_table({"regions": {"brazil_mato_grosso": _mato_grosso()}})
    assert "11.0mm" in row and "6.7mm" in row
    assert FAIL not in row, "millimetres were returned, so this is not a failure"


def test_nulled_precip_row_still_shows_the_temperature_flag():
    row = render_region_table({"regions": {"brazil_mato_grosso": _mato_grosso()}})
    assert "-2.75" in row or "−2.75" in row
    assert "FLAGGED" in row


def test_precip_fails_only_when_millimetres_are_absent_too():
    region = _mato_grosso(total_precip_mm=None, normal_precip_mm=None)
    assert FAIL in render_region_table({"regions": {"brazil_mato_grosso": region}})


def test_a_real_percentage_is_still_preferred_over_millimetres():
    region = _mato_grosso(precip_anomaly_pct=-61.3)
    row = render_region_table({"regions": {"brazil_mato_grosso": region}})
    assert "61.3%" in row
    assert "mm" not in row

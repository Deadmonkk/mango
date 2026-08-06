"""Tests for `render_region_table` — the climate provider's per-region markdown
table, the one FR digest shape that a flat Field/Section table can't express.

Real payload shapes verified 2026-08-06 against the live `get_climate_risk_watch`
tool (see the task brief): `precip_anomaly_pct` can be null, `watch` sub-keys are
inconsistent across regions, and — critically — `flagged_regions` holds each
region's LABEL, not its dict key. That mismatch is exactly what the
flagged-first-sort test below guards against.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fr_render import FAIL, render_region_table  # noqa: E402


def _region(**overrides: object) -> dict:
    base = {
        "label": "US Corn Belt (Iowa)",
        "temp_anomaly_c": 1.76,
        "precip_anomaly_pct": -43.8,
        "signal": "normal — within typical range for this time of year",
        "watch": {
            "commodities": ["corn (ZC)", "soybeans (ZS)"],
            "upstream": ["DE (Deere — equipment)"],
            "midstream": ["ADM (Archer-Daniels-Midland)"],
            "downstream": ["TSN (Tyson)"],
            "other_assets": ["DBA (agriculture commodity ETF)"],
        },
    }
    base.update(overrides)
    return base


class TestNormalRender:
    def test_renders_expected_columns_and_header(self) -> None:
        climate = {"regions": {"us_corn_belt": _region()}, "flagged_regions": []}

        table = render_region_table(climate)

        assert "| Region | Temp anomaly | Precip anomaly | Status | Linked exposure |" in table
        assert "|---|---|---|---|---|" in table

    def test_renders_a_normal_region_row(self) -> None:
        climate = {"regions": {"us_corn_belt": _region()}, "flagged_regions": []}

        table = render_region_table(climate)

        assert "| US Corn Belt (Iowa) | +1.76°C | -43.8% |" in table
        assert "corn (ZC), soybeans (ZS), DE (Deere — equipment), ADM (Archer-Daniels-Midland)…" in table


class TestFlaggedSortsFirst:
    def test_flagged_region_sorts_before_unflagged_using_real_label_mismatch(self) -> None:
        # `flagged_regions` carries LABELS ("Mato Grosso, Brazil"), while the
        # dict key is snake_case ("brazil_mato_grosso") — the real shape that
        # caused the original brief's key-matching approach to silently miss.
        climate = {
            "regions": {
                "us_corn_belt": _region(label="US Corn Belt (Iowa)", signal="normal"),
                "brazil_mato_grosso": _region(
                    label="Mato Grosso, Brazil",
                    temp_anomaly_c=-3.0,
                    precip_anomaly_pct=None,
                    signal="FLAGGED — cooler than normal by 3.0°C",
                    watch={"commodities": ["soybeans (ZS)"], "other_assets": ["EWZ (Brazil equity ETF)"]},
                ),
            },
            "flagged_regions": ["Mato Grosso, Brazil"],
        }

        table = render_region_table(climate)
        data_lines = table.splitlines()[2:]  # skip header + separator rows

        assert data_lines[0].startswith("| Mato Grosso, Brazil |")
        assert data_lines[1].startswith("| US Corn Belt (Iowa) |")

    def test_flagged_via_flagged_regions_label_alone_still_sorts_first(self) -> None:
        # signal string does NOT start with "FLAGGED" here — only the
        # flagged_regions label list marks it, exercising the second path.
        climate = {
            "regions": {
                "a_region": _region(label="A Region", signal="normal"),
                "b_region": _region(label="B Region", signal="elevated but not flagged"),
            },
            "flagged_regions": ["B Region"],
        }

        table = render_region_table(climate)
        data_lines = table.splitlines()[2:]  # skip header + separator rows

        assert data_lines[0].startswith("| B Region |")
        assert data_lines[1].startswith("| A Region |")


class TestNullPrecip:
    def test_null_precip_anomaly_renders_fail_sentinel(self) -> None:
        climate = {
            "regions": {"brazil_mato_grosso": _region(precip_anomaly_pct=None)},
            "flagged_regions": [],
        }

        table = render_region_table(climate)

        assert FAIL in table
        assert "| None%" not in table
        assert "| 0%" not in table
        assert "| n/a |" not in table


class TestMissingWatchSubkeys:
    def test_region_with_no_watch_key_renders_none_linked(self) -> None:
        region = _region()
        del region["watch"]
        climate = {"regions": {"us_corn_belt": region}, "flagged_regions": []}

        table = render_region_table(climate)

        assert "none linked" in table

    def test_region_with_only_commodities_watch_key_renders_gracefully(self) -> None:
        region = _region(watch={"commodities": ["soybeans (ZS)"]})
        climate = {"regions": {"brazil_mato_grosso": region}, "flagged_regions": []}

        table = render_region_table(climate)

        assert "soybeans (ZS)" in table


class TestExposureCapping:
    def test_exposure_list_caps_at_four_with_ellipsis(self) -> None:
        region = _region(
            watch={
                "commodities": ["A", "B"],
                "upstream": ["C"],
                "midstream": ["D"],
                "downstream": ["E"],
                "other_assets": ["F"],
            }
        )
        climate = {"regions": {"us_corn_belt": region}, "flagged_regions": []}

        table = render_region_table(climate)

        assert "A, B, C, D…" in table
        assert "A, B, C, D, E" not in table  # the 5th entry must not leak in uncapped

    def test_exposure_list_under_cap_has_no_ellipsis(self) -> None:
        region = _region(watch={"commodities": ["A", "B"]})
        climate = {"regions": {"us_corn_belt": region}, "flagged_regions": []}

        table = render_region_table(climate)

        assert "A, B" in table
        assert "A, B…" not in table


class TestFailedOrEmptyInput:
    def test_missing_climate_dict_returns_fail_line_without_raising(self) -> None:
        result = render_region_table(None)  # type: ignore[arg-type]

        assert FAIL in result
        assert "\n" not in result

    def test_empty_climate_dict_returns_fail_line(self) -> None:
        result = render_region_table({})

        assert FAIL in result

    def test_error_key_returns_fail_line(self) -> None:
        result = render_region_table({"error": "provider timeout"})

        assert FAIL in result

    def test_empty_regions_dict_returns_fail_line(self) -> None:
        result = render_region_table({"regions": {}, "flagged_regions": []})

        assert FAIL in result


class TestDeterminism:
    def test_same_input_renders_identical_string_across_runs(self) -> None:
        climate = {
            "regions": {
                "us_corn_belt": _region(label="US Corn Belt (Iowa)", signal="normal"),
                "brazil_mato_grosso": _region(
                    label="Mato Grosso, Brazil",
                    temp_anomaly_c=-3.0,
                    precip_anomaly_pct=None,
                    signal="FLAGGED — cooler than normal by 3.0°C",
                ),
            },
            "flagged_regions": ["Mato Grosso, Brazil"],
        }

        first = render_region_table(climate)
        second = render_region_table(climate)

        assert first == second

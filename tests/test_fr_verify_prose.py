"""Tests for the prose drift checker.

It exists because prose carried across a collector re-run silently describes the
previous run. All three real instances from 2026-08-12 are encoded here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fr_render import FAIL  # noqa: E402
from fr_verify_prose import (  # noqa: E402
    split_report,
    unsupported_failure_claims,
    unverified_numbers,
    verify,
)


def _report(prose: str, tables: str) -> str:
    return f"# FR\n\n{tables}\n\n<!-- PROSE:s4 -->\n{prose}\n<!-- /PROSE:s4 -->\n"


TABLES = (
    "| Measure | Value | Read |\n"
    "|---|---|---|\n"
    "| Priced change to horizon | 38bp |  |\n"
    "| Credit-card delinquency | 2.92% |  |\n"
    "| Stablecoin supply | 306,401,459,887.04 |  |\n"
    "| China large-cap (FXI) YTD | -9.87% |  |\n"
)


class TestNumberDrift:
    def test_a_stale_figure_from_the_previous_run_is_caught(self) -> None:
        """The real bug: prose said 36bp after the value moved to 38bp."""
        assert "36" in unverified_numbers("36bp of hikes priced.", TABLES)

    def test_the_current_figure_passes(self) -> None:
        assert unverified_numbers("38bp of hikes priced.", TABLES) == []

    def test_rounding_is_accepted(self) -> None:
        """Prose legitimately rounds -9.87% to -9.9%."""
        assert unverified_numbers("China (−9.9%) keeps dragging.", TABLES) == []

    def test_unit_rescaling_is_accepted(self) -> None:
        """$306,401,459,887 quoted as $306.4bn is the same figure."""
        assert unverified_numbers("Stablecoins flat at $306.4bn.", TABLES) == []

    def test_years_and_small_counts_are_not_flagged(self) -> None:
        prose = "Since 1959, three of six signals; the 2026 print."
        assert unverified_numbers(prose, TABLES) == []

    def test_labelled_external_anchors_are_allowed(self) -> None:
        """EY's scenario ladder is a fixed published anchor, not a live figure."""
        prose = "EY's ladder: Optimistic $74 / Baseline $88 / Adverse $100 / Severe $150."
        assert unverified_numbers(prose, TABLES) == []


class TestFailureClaims:
    def test_claiming_a_failure_with_no_sentinel_is_caught(self) -> None:
        """The subtle one: the source recovered, the prose still says it failed."""
        claims = unsupported_failure_claims(
            "Both delinquency sources failed this run.", TABLES
        )
        assert len(claims) == 1

    def test_a_real_failure_is_not_flagged(self) -> None:
        tables = TABLES + f"| Consumer-loan delinquency | {FAIL} | source failed |\n"
        assert unsupported_failure_claims("The source failed this run.", tables) == []

    def test_prose_without_failure_language_is_not_flagged(self) -> None:
        assert unsupported_failure_claims("Credit spreads are tight.", TABLES) == []

    @pytest.mark.parametrize(
        "phrase",
        [
            "the source failed this run",
            "data unavailable this run",
            "the endpoint connect-timed out",
            "both sources are failing",
            "the calendar did not load",
        ],
    )
    def test_common_failure_phrasings_are_recognised(self, phrase: str) -> None:
        assert unsupported_failure_claims(f"Note: {phrase}.", TABLES)


class TestReportSplitting:
    def test_only_prose_is_checked_not_the_tables(self) -> None:
        """A figure in a table must never be reported as unverified prose."""
        prose, tables = split_report(_report("All quiet.", TABLES))
        assert "38bp" in tables
        assert "38bp" not in prose


class TestExitCodes:
    def test_clean_report_exits_zero(self, tmp_path: Path) -> None:
        p = tmp_path / "clean-fr.md"
        p.write_text(_report("Priced hikes eased to 38bp.", TABLES))
        assert verify(p) == 0

    def test_drifted_report_exits_nonzero(self, tmp_path: Path) -> None:
        p = tmp_path / "drift-fr.md"
        p.write_text(_report("Priced hikes eased to 36bp.", TABLES))
        assert verify(p) == 1

    def test_unfilled_report_exits_nonzero(self, tmp_path: Path) -> None:
        """An unfilled report is not a passing report."""
        p = tmp_path / "empty-fr.md"
        p.write_text(_report("", TABLES))
        assert verify(p) == 1


class TestNoiseSuppression:
    def test_digits_inside_urls_are_not_treated_as_figures(self) -> None:
        prose = "Map: https://claude.ai/code/artifact/1c4e1a63-3fa1-4501-9f4e-91cae1f4593f"
        assert unverified_numbers(prose, TABLES) == []

    def test_http_status_codes_are_allowed(self) -> None:
        assert unverified_numbers("The calendar is premium-walled (HTTP 403).", TABLES) == []


class TestRescaleIsNotAWildcard:
    """Rescaling must not turn the check into a rubber stamp.

    On 2026-08-12 the prose said cyclicals "thinned to 0.39pp from 0.64pp". The
    0.64 was carried from an earlier run and appears nowhere in the tables, but
    the check passed: it multiplied 0.64 by 1000 and matched "643 months" from an
    unrelated Read cell, having scaled the tolerance up to ±11 as well.
    """

    TABLES_WITH_643 = (
        "| Measure | Value | Read |\n"
        "|---|---|---|\n"
        "| Cyclicals vs defensives (3mo) | 0.39pp |  |\n"
        "| Excess bond premium | -0.32pp | EBP at the 16.5th percentile of 643 months |\n"
        "| Stablecoin supply | 306,401,459,887.04 |  |\n"
    )

    def test_a_small_figure_does_not_match_an_unrelated_count_via_scaling(self) -> None:
        assert "0.64" in unverified_numbers("thinned to 0.39pp from 0.64pp", self.TABLES_WITH_643)

    def test_the_real_figure_still_passes(self) -> None:
        assert unverified_numbers("cyclicals lead by 0.39pp", self.TABLES_WITH_643) == []

    def test_genuine_rescaling_of_a_large_value_still_passes(self) -> None:
        """$306,401,459,887 quoted as "$306.4bn" must keep working."""
        assert unverified_numbers("Stablecoins at $306.4bn.", self.TABLES_WITH_643) == []


class TestPrecisionDecidesTheMatch:
    """The written precision, not a blanket floor, decides what counts as equal."""

    NEIGHBOURS = (
        "| Measure | Value | Read |\n"
        "|---|---|---|\n"
        "| EFFR | 0.63% |  |\n"
    )

    def test_a_two_decimal_figure_does_not_match_its_neighbour(self) -> None:
        """0.64 covers [0.635, 0.645); 0.63 is a different number."""
        assert unverified_numbers("the spread was 0.64pp", self.NEIGHBOURS) == ["0.64"]

    def test_a_one_decimal_figure_does_match_within_its_own_rounding(self) -> None:
        """0.6 covers [0.55, 0.65), which does include 0.63."""
        assert unverified_numbers("the spread was 0.6pp", self.NEIGHBOURS) == []

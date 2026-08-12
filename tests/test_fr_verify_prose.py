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

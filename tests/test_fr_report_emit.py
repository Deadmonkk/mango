"""The collector, not the model, owns deterministic report generation.

These guard the property the redesign exists for: everything except prose is
produced in code, exactly once, and the model never has to transport a table.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fr_prose import ProseError, inject, unfilled  # noqa: E402
from fr_report import (  # noqa: E402
    PROSE_PLACEHOLDER,
    PROSE_SLOTS,
    REPORT_TITLES,
    VALUES_PREFIX,
    build_report,
    delta_rows,
    extract_values,
    load_prior_values,
    render_delta_table,
)
from fr_sections import render_digest  # noqa: E402


@pytest.fixture(scope="module")
def payload() -> tuple[dict, dict]:
    """A real captured collector payload, if one is present on this machine."""
    briefs = Path.home() / "Desktop/TerminalIQ Reports/.briefs"
    files = sorted(briefs.glob("fr_raw_*.json")) if briefs.exists() else []
    if not files:
        pytest.skip("no captured fr_raw_*.json available")
    from fr_collect import derive

    raw = json.loads(files[-1].read_text())
    return raw, derive(raw)


# --- structure -------------------------------------------------------------


def test_report_contains_every_numbered_section(payload):
    raw, derived = payload
    report = build_report(raw, derived, "2026-01-01")
    for number, title in REPORT_TITLES.items():
        assert f"## {number}. {title}" in report
    assert "## 0. What Changed Since Last Run" in report
    assert "## 12. Synthesis" in report


def test_every_prose_slot_is_present_and_empty(payload):
    raw, derived = payload
    report = build_report(raw, derived, "2026-01-01")
    for key, _ in PROSE_SLOTS:
        assert f"<!-- PROSE:{key} -->" in report
        assert f"<!-- /PROSE:{key} -->" in report
    assert unfilled(report) == [k for k, _ in PROSE_SLOTS]
    assert report.count(PROSE_PLACEHOLDER) == len(PROSE_SLOTS)


def test_deterministic_tables_are_populated_not_stubbed(payload):
    raw, derived = payload
    report = build_report(raw, derived, "2026-01-01")
    data_rows = [ln for ln in report.splitlines() if ln.startswith("| ") and not ln.startswith("|---")]
    assert len(data_rows) > 80, "tables look empty — the collector must populate them"


def test_generation_is_deterministic(payload):
    raw, derived = payload
    assert build_report(raw, derived, "2026-01-01") == build_report(raw, derived, "2026-01-01")


# --- no duplication --------------------------------------------------------


HEADER_ROWS = ("| Measure | Value | Read |", "| Component | Weight | Score | Driver |",
               "| Region | Temp anomaly | Precip anomaly | Status | Linked exposure |")


def test_no_data_row_is_emitted_twice(payload):
    """Each section repeats its column header; no DATA row may repeat.

    A duplicated data row is the signature of the old failure mode — the same
    table rendered into the document more than once.
    """
    raw, derived = payload
    report = build_report(raw, derived, "2026-01-01")
    # The §0 delta legitimately restates every label, so scope to section tables.
    body = report.split("\n---\n", 1)[1]
    rows = [ln for ln in body.splitlines()
            if ln.startswith("| ") and not ln.startswith("|---") and ln not in HEADER_ROWS]
    duplicates = {r for r in rows if rows.count(r) > 1}
    assert not duplicates, f"row(s) emitted more than once: {sorted(duplicates)[:3]}"
    assert rows, "sanity: report has rows"


def test_each_section_heading_appears_exactly_once(payload):
    raw, derived = payload
    report = build_report(raw, derived, "2026-01-01")
    for number, title in REPORT_TITLES.items():
        assert report.count(f"## {number}. {title}") == 1


# --- calculations unchanged ------------------------------------------------


_SCORE_RE = r"\*\*(Equity|Crypto) Regime Score: ([\d.]+)/100 — ([^*]+)\*\*"


def test_scores_match_the_digest_exactly(payload):
    raw, derived = payload
    report = build_report(raw, derived, "2026-01-01")
    digest = render_digest(raw, derived, "2026-01-01", "fr")
    assert re.findall(_SCORE_RE, report) == re.findall(_SCORE_RE, digest)


def test_every_digest_table_row_survives_into_the_report(payload):
    raw, derived = payload
    report = build_report(raw, derived, "2026-01-01")
    digest = render_digest(raw, derived, "2026-01-01", "fr")
    digest_rows = {ln for ln in digest.splitlines()
                   if ln.startswith("| ") and not ln.startswith("|---")}
    report_rows = {ln for ln in report.splitlines()
                   if ln.startswith("| ") and not ln.startswith("|---")}
    assert not (digest_rows - report_rows), "the report lost data the digest carries"


# --- structured values / delta --------------------------------------------


def test_extract_values_covers_the_scores_and_is_serialisable(payload):
    raw, derived = payload
    values = extract_values(raw, derived)
    assert "Equity Regime Score" in values and "Crypto Regime Score" in values
    json.dumps(values)  # must not raise


def test_field_labels_are_unique_so_the_delta_cannot_silently_drop_a_metric():
    """`extract_values` keys on the label, so two fields sharing one would collide.

    Hermetic on purpose — it guards the spec, not a payload, and so still runs
    on a machine with no captured collector output.
    """
    from fr_sections import SECTIONS as ALL

    labels = [f.label for s in ALL for f in s.fields]
    duplicates = sorted({lb for lb in labels if labels.count(lb) > 1})
    assert not duplicates, f"duplicate Field labels collide in the delta snapshot: {duplicates}"


def test_first_run_states_the_baseline_instead_of_faking_a_delta():
    table = render_delta_table({"CAPE": 42.0}, {}, "")
    assert "First run" in table
    assert "|" not in table, "no delta table should be drawn without a prior"


def test_delta_reports_direction_and_percentage():
    rows = {r.label: r.change for r in delta_rows({"VIX": 16.5}, {"VIX": 15.0})}
    assert rows["VIX"].startswith("+1.5")
    assert "+10.00%" in rows["VIX"]


def test_delta_marks_an_unavailable_prior_rather_than_inventing_one():
    rows = {r.label: r.change for r in delta_rows({"VIX": 16.5}, {"VIX": None})}
    assert rows["VIX"] == "—"


def test_unchanged_values_are_labelled_not_shown_as_zero():
    rows = {r.label: r.change for r in delta_rows({"VIX": 15.0}, {"VIX": 15.0})}
    assert rows["VIX"] == "unchanged"


def test_load_prior_values_ignores_today_and_the_future(tmp_path):
    (tmp_path / f"{VALUES_PREFIX}2026-08-09.json").write_text(json.dumps({"VIX": 1}))
    (tmp_path / f"{VALUES_PREFIX}2026-08-10.json").write_text(json.dumps({"VIX": 2}))
    (tmp_path / f"{VALUES_PREFIX}2026-08-11.json").write_text(json.dumps({"VIX": 3}))
    values, date = load_prior_values(tmp_path, "2026-08-10")
    assert (values, date) == ({"VIX": 1}, "2026-08-09")


def test_load_prior_values_survives_a_corrupt_snapshot(tmp_path):
    (tmp_path / f"{VALUES_PREFIX}2026-08-09.json").write_text("{not json")
    assert load_prior_values(tmp_path, "2026-08-10") == ({}, "")


def test_no_prior_snapshots_yields_an_empty_baseline(tmp_path):
    assert load_prior_values(tmp_path, "2026-08-10") == ({}, "")


# --- prose injection -------------------------------------------------------


def test_injecting_prose_fills_the_slot_and_leaves_tables_untouched(payload):
    raw, derived = payload
    report = build_report(raw, derived, "2026-01-01")
    before_rows = [ln for ln in report.splitlines() if ln.startswith("| ")]
    filled = inject(report, "s3", "Credit is calm at the index level.")
    assert "Credit is calm at the index level." in filled
    assert [ln for ln in filled.splitlines() if ln.startswith("| ")] == before_rows
    assert "s3" not in unfilled(filled)


def test_injection_is_idempotent_and_refillable(payload):
    raw, derived = payload
    report = build_report(raw, derived, "2026-01-01")
    once = inject(report, "s3", "first")
    twice = inject(once, "s3", "second")
    assert "second" in twice and "first" not in twice
    assert twice.count("<!-- PROSE:s3 -->") == 1


def test_unknown_slot_fails_loudly(payload):
    raw, derived = payload
    report = build_report(raw, derived, "2026-01-01")
    with pytest.raises(ProseError, match="unknown prose slot"):
        inject(report, "s99", "text")


def test_missing_marker_fails_loudly_rather_than_appending():
    with pytest.raises(ProseError, match="not found"):
        inject("# a report with no markers", "s3", "text")


def test_empty_prose_is_refused(payload):
    raw, derived = payload
    report = build_report(raw, derived, "2026-01-01")
    with pytest.raises(ProseError, match="empty prose"):
        inject(report, "s3", "   \n  ")


def test_duplicated_marker_is_refused_rather_than_guessed(payload):
    raw, derived = payload
    report = build_report(raw, derived, "2026-01-01")
    doubled = report + "\n<!-- PROSE:s3 -->\nx\n<!-- /PROSE:s3 -->\n"
    with pytest.raises(ProseError, match="appears 2 times"):
        inject(doubled, "s3", "text")


# --- context-size regression ----------------------------------------------

# The point of the redesign. The digest is the ONLY large artefact the model
# reads; if it creeps back toward the old ~15k-token payload the budget is gone.
# Chars/4 is a deliberately crude proxy — it needs to catch a regression, not
# match a tokenizer.
DIGEST_TOKEN_CEILING = 4500


def test_digest_stays_within_the_context_budget(payload):
    raw, derived = payload
    approx_tokens = len(render_digest(raw, derived, "2026-01-01", "fr")) // 4
    assert approx_tokens < DIGEST_TOKEN_CEILING, (
        f"digest is ~{approx_tokens} tokens, over the {DIGEST_TOKEN_CEILING} ceiling — "
        "aggregate or summarise rather than growing the payload the model reads"
    )


def test_the_report_is_larger_than_the_digest_and_never_read_back(payload):
    """The report may be big precisely because the model never reads it."""
    raw, derived = payload
    report = build_report(raw, derived, "2026-01-01")
    digest = render_digest(raw, derived, "2026-01-01", "fr")
    assert len(report) > len(digest)


def test_delta_lists_only_what_moved_and_counts_the_rest():
    current = {"VIX": 16.5, "CAPE": 42.0, "HY": 2.7}
    prior = {"VIX": 15.0, "CAPE": 42.0, "HY": 2.7}
    table = render_delta_table(current, prior, "2026-08-09")
    assert "| VIX |" in table
    assert "| CAPE |" not in table, "an unchanged metric should not take a row"
    assert "1 of 3 tracked metrics moved" in table
    assert "CAPE" in table and "HY" in table, "unchanged metrics must still be named"


class TestDeltaCellFormatting:
    """The delta must render a value the way its own section table renders it.

    A CPI row reading "0.07%" in §1 previously appeared as "0.0737" in the §0
    delta, which reads as a different figure. The delta is the first table in the
    report, so a reader meets the mis-formatted version first.
    """

    def test_percent_fields_keep_their_unit_in_the_delta(self) -> None:
        table = render_delta_table(
            {"Core CPI m/m change": 0.21543}, {"Core CPI m/m change": 0.1}, "2026-08-11"
        )

        assert "0.2154%" in table
        assert "| 0.2154 |" not in table

    def test_delta_keeps_more_precision_than_the_section_table(self) -> None:
        """Two spreads both shown as "2.72pp" must still reveal a move."""
        table = render_delta_table({"HY spread": 2.7231}, {"HY spread": 2.7204}, "2026-08-11")

        assert "2.7231pp" in table
        assert "2.7204pp" in table

    def test_unknown_labels_still_render(self) -> None:
        table = render_delta_table(
            {"Equity Regime Score": 33.2}, {"Equity Regime Score": 25.6}, "2026-08-11"
        )

        assert "33.2" in table and "25.6" in table

"""EOD's deterministic blocks belong to code, exactly as FR's do.

Mirrors test_fr_report_emit.py for the after-close report, plus unit coverage of
the EOD-specific renderers — ranking, splitting and the ATR band arithmetic are
where a silent wrong answer would look plausible in the finished table.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from eod_render import (  # noqa: E402
    render_asset_classes,
    render_crypto_movers,
    render_expected_ranges,
    render_movers,
    render_scoreboard,
)
from eod_report import (  # noqa: E402
    _SLOT_FOR_SECTION,
    EOD_PROSE_SLOTS,
    EOD_TITLES,
    _movers_universe,
    build_eod_report,
    eod_report_path,
)
from fr_prose import ProseError, inject, known_keys, unfilled  # noqa: E402
from fr_render import FAIL  # noqa: E402
from fr_report import PROSE_PLACEHOLDER, PROSE_SLOTS  # noqa: E402
from fr_sections import render_digest  # noqa: E402


@pytest.fixture(scope="module")
def payload() -> tuple[dict, dict]:
    """A real captured EOD collector payload, if one is present on this machine."""
    briefs = Path.home() / "Desktop/TerminalIQ Reports/.briefs"
    files = sorted(briefs.glob("eod_raw_*.json")) if briefs.exists() else []
    if not files:
        pytest.skip("no captured eod_raw_*.json available")
    from fr_collect import derive

    raw = json.loads(files[-1].read_text())
    return raw, derive(raw)


# --- structure -------------------------------------------------------------


def test_report_contains_every_numbered_section(payload):
    raw, derived = payload
    report = build_eod_report(raw, derived, "2026-01-01")
    for number, title in EOD_TITLES:
        assert f"## {number}. {title}" in report
        assert report.count(f"## {number}. {title}") == 1


def test_every_prose_slot_is_present_and_empty(payload):
    raw, derived = payload
    report = build_eod_report(raw, derived, "2026-01-01")
    for key, _ in EOD_PROSE_SLOTS:
        assert f"<!-- PROSE:{key} -->" in report
        assert f"<!-- /PROSE:{key} -->" in report
    assert unfilled(report) == [k for k, _ in EOD_PROSE_SLOTS]
    assert report.count(PROSE_PLACEHOLDER) == len(EOD_PROSE_SLOTS)


def test_deterministic_tables_are_populated_not_stubbed(payload):
    raw, derived = payload
    report = build_eod_report(raw, derived, "2026-01-01")
    rows = [ln for ln in report.splitlines() if ln.startswith("| ") and not ln.startswith("|---")]
    assert len(rows) > 30, "tables look empty — the collector must populate them"


def test_generation_is_deterministic(payload):
    raw, derived = payload
    assert build_eod_report(raw, derived, "2026-01-01") == build_eod_report(raw, derived, "2026-01-01")


def test_every_digest_table_row_survives_into_the_report(payload):
    raw, derived = payload
    report = build_eod_report(raw, derived, "2026-01-01")
    digest = render_digest(raw, derived, "2026-01-01", "eod")
    digest_rows = {ln for ln in digest.splitlines()
                   if ln.startswith("| ") and not ln.startswith("|---")}
    report_rows = {ln for ln in report.splitlines()
                   if ln.startswith("| ") and not ln.startswith("|---")}
    assert not (digest_rows - report_rows), "the report lost data the digest carries"


def test_attribution_and_forecast_caveats_are_structural(payload):
    """The two EOD-specific integrity rules live in the template, not the prose."""
    raw, derived = payload
    report = build_eod_report(raw, derived, "2026-01-01")
    assert "NOT " in report and "measured causation" in report
    assert "not a forecast" in report.lower()


def test_report_path_is_one_file_per_day(tmp_path):
    assert eod_report_path(tmp_path, "2026-08-11") == tmp_path / "2026-08-11-eod.md"


def test_every_section_number_maps_to_its_own_prose_slot():
    """The three parallel orderings must stay aligned or prose lands under the wrong heading."""
    assert list(_SLOT_FOR_SECTION) == [n for n, _ in EOD_TITLES]
    assert list(_SLOT_FOR_SECTION.values()) == [k for k, _ in EOD_PROSE_SLOTS]


def test_a_failed_mover_universe_falls_back_to_the_watchlist():
    """A failed source is a truthy dict, so `or` would have kept the failure."""
    raw = {"quotes_batch_movers": {"_value": FAIL, "_error": "boom"},
           "quotes_batch_watchlist": [_q("AAPL", 1.0)]}
    assert _movers_universe(raw) == raw["quotes_batch_watchlist"]


def test_a_working_mover_universe_is_preferred_over_the_watchlist():
    raw = {"quotes_batch_movers": [_q("NVDA", 2.0)], "quotes_batch_watchlist": [_q("AAPL", 1.0)]}
    assert _movers_universe(raw) == raw["quotes_batch_movers"]


# --- prose injection routes to the EOD slot set ----------------------------


def test_eod_slots_are_accepted_and_fr_slots_are_not(payload):
    raw, derived = payload
    report = build_eod_report(raw, derived, "2026-01-01")
    assert known_keys(report) == {k for k, _ in EOD_PROSE_SLOTS}
    filled = inject(report, "drivers", "Rates did the work today.")
    assert "Rates did the work today." in filled
    with pytest.raises(ProseError, match="unknown prose slot"):
        inject(report, "s3", "an FR slot has no place in an EOD report")


def test_fr_reports_still_validate_against_the_fr_slot_set():
    fr_like = "\n".join(f"<!-- PROSE:{k} -->\nx\n<!-- /PROSE:{k} -->" for k, _ in PROSE_SLOTS)
    assert known_keys(fr_like) == {k for k, _ in PROSE_SLOTS}


def test_injection_leaves_every_table_row_untouched(payload):
    raw, derived = payload
    report = build_eod_report(raw, derived, "2026-01-01")
    before = [ln for ln in report.splitlines() if ln.startswith("| ")]
    filled = inject(report, "synthesis", "A quiet day.")
    assert [ln for ln in filled.splitlines() if ln.startswith("| ")] == before


# --- scoreboard ------------------------------------------------------------


def _q(sym: str, pct: float, price: float = 100.0) -> dict:
    return {"symbol": sym, "percent_change": pct, "current_price": price}


def _tickers(table: str, col: int = 1) -> list[str]:
    return [ln.split("|")[col].strip() for ln in table.splitlines()
            if ln.startswith("| ") and not ln.startswith("|---")][1:]


def test_scoreboard_ranks_best_first():
    table = render_scoreboard([_q("A", -1.0), _q("B", 2.0), _q("C", 0.5)], "Sector ETF")
    assert _tickers(table) == ["B", "C", "A"]


def test_scoreboard_labels_only_the_two_tails():
    table = render_scoreboard([_q("A", -1.0), _q("B", 2.0), _q("C", 0.5)])
    assert table.count("day's leader") == 1
    assert table.count("day's laggard") == 1


def test_scoreboard_flags_an_outsized_mid_pack_move():
    table = render_scoreboard([_q("A", 9.0), _q("B", 4.0), _q("C", -9.0)])
    assert "check for name-specific news" in table


def test_scoreboard_skips_quotes_with_no_percent_change():
    table = render_scoreboard([_q("A", 1.0), {"symbol": "B"}])
    assert "| B |" not in table


def test_scoreboard_degrades_to_fail_on_an_empty_payload():
    assert FAIL in render_scoreboard([])
    assert FAIL in render_scoreboard(None)
    assert FAIL in render_scoreboard({"not": "a list"})


# --- movers ----------------------------------------------------------------


def test_movers_splits_gainers_and_losers_by_rank():
    quotes = [_q(s, p) for s, p in [("A", 3.0), ("B", 1.0), ("C", -1.0), ("D", -3.0)]]
    table = render_movers(quotes, shown=2)
    gainers = [ln for ln in table.splitlines() if ln.startswith("| gainer")]
    losers = [ln for ln in table.splitlines() if ln.startswith("| loser")]
    assert [ln.split("|")[2].strip() for ln in gainers] == ["A", "B"]
    assert [ln.split("|")[2].strip() for ln in losers] == ["D", "C"], "losers list worst first"


def test_movers_never_lists_the_same_name_as_both_gainer_and_loser():
    quotes = [_q(s, p) for s, p in [("A", 3.0), ("B", 1.0), ("C", -1.0)]]
    table = render_movers(quotes, shown=8)
    names = [ln.split("|")[2].strip() for ln in table.splitlines() if ln.startswith("| gainer")
             or ln.startswith("| loser")]
    assert len(names) == len(set(names))


def test_a_single_quote_is_labelled_by_the_sign_of_its_move():
    assert "| loser | A |" in render_movers([_q("A", -1.0)])
    assert "| gainer | A |" in render_movers([_q("A", 1.0)])


def test_movers_degrades_to_fail_on_an_empty_payload():
    assert FAIL in render_movers([])
    assert FAIL in render_movers(None)


# --- asset classes ---------------------------------------------------------


def _ac(name: str, one_mo) -> dict:
    return {"name": name, "current": 100.0, "1mo": one_mo, "3mo": 0.0, "ytd": 0.0, "1y": 0.0}


def test_asset_classes_are_ordered_by_the_one_month_column():
    payload = {"asset_classes": {"X": _ac("X", 1.0), "Y": _ac("Y", 5.0), "Z": _ac("Z", -2.0)}}
    order = [ln.split("|")[1].strip().split(" ")[0] for ln in render_asset_classes(payload).splitlines()
             if ln.startswith("| ") and not ln.startswith("|---")][1:]
    assert order == ["Y", "X", "Z"]


def test_an_asset_class_missing_its_one_month_return_sorts_last_not_first():
    payload = {"asset_classes": {"X": _ac("X", 1.0), "Q": _ac("Q", None)}}
    rows = [ln for ln in render_asset_classes(payload).splitlines() if ln.startswith("| ")]
    assert rows[-1].split("|")[1].strip().startswith("Q")


def test_a_malformed_asset_class_entry_is_dropped_not_raised():
    payload = {"asset_classes": {"X": _ac("X", 1.0), "BAD": "not a dict"}}
    rendered = render_asset_classes(payload)
    assert "| X (X) |" in rendered
    assert "BAD" not in rendered


def test_asset_classes_degrade_to_fail_on_an_empty_payload():
    assert FAIL in render_asset_classes({})
    assert FAIL in render_asset_classes({"asset_classes": {}})
    assert FAIL in render_asset_classes(None)


# --- crypto movers ---------------------------------------------------------


def test_crypto_movers_rank_by_the_24h_move():
    coins = [{"symbol": s, "current_price": 1.0, "price_change_pct_24h": p}
             for s, p in [("BTC", -1.0), ("ETH", 3.0)]]
    assert _tickers(render_crypto_movers(coins)) == ["ETH", "BTC"]


def test_crypto_movers_degrade_to_fail_on_an_empty_payload():
    assert FAIL in render_crypto_movers([])
    assert FAIL in render_crypto_movers(None)


# --- expected ranges -------------------------------------------------------


TECHNICALS = {"price": 100.0, "atr": {"atr": 2.0}}
OVERVIEW = {"markets": {"^GSPC": {"current": 5000.0}, "^IXIC": {"current": 20000.0},
                        "^RUT": {"current": 2000.0}, "^DJI": {"current": 40000.0}}}


def _band(table: str, name: str) -> tuple[float, float]:
    row = next(ln for ln in table.splitlines() if ln.startswith(f"| {name} |"))
    cells = [c.strip().replace(",", "") for c in row.split("|")]
    return float(cells[3]), float(cells[4])


def test_spy_band_is_exactly_plus_minus_one_measured_atr():
    assert _band(render_expected_ranges(TECHNICALS, OVERVIEW), "SPY") == (98.0, 102.0)


def test_index_bands_scale_spys_atr_as_a_percentage():
    """ATR 2 on a price of 100 is 2%; a 5000 index therefore gets a ±100 band."""
    table = render_expected_ranges(TECHNICALS, OVERVIEW)
    assert _band(table, "S&P 500") == (4900.0, 5100.0)
    assert _band(table, "Nasdaq") == (19600.0, 20400.0)
    assert "±2.00%" in table


def test_the_band_is_labelled_a_volatility_range_not_a_forecast():
    assert "NOT a directional forecast" in render_expected_ranges(TECHNICALS, OVERVIEW)


def test_an_index_with_no_level_fails_in_its_own_row_only():
    table = render_expected_ranges(TECHNICALS, {"markets": {"^GSPC": {"current": 5000.0}}})
    assert f"| Nasdaq | — | — | — | {FAIL} |" in table
    assert _band(table, "S&P 500") == (4900.0, 5100.0)


def test_expected_ranges_fall_back_to_the_overview_for_spys_level():
    table = render_expected_ranges({"atr": {"atr": 2.0}}, {"markets": {"SPY": {"current": 100.0}}})
    assert _band(table, "SPY") == (98.0, 102.0)


def test_a_malformed_market_entry_fails_its_own_row_rather_than_the_report():
    table = render_expected_ranges(TECHNICALS, {"markets": {"^GSPC": "not a dict"}})
    assert f"| S&P 500 | — | — | — | {FAIL} |" in table
    assert _band(table, "SPY") == (98.0, 102.0)


def test_expected_ranges_degrade_to_fail_without_an_atr():
    assert FAIL in render_expected_ranges({}, OVERVIEW)
    assert FAIL in render_expected_ranges(None, OVERVIEW)


def test_expected_ranges_refuse_to_scale_against_a_missing_spy_level():
    out = render_expected_ranges({"atr": {"atr": 2.0}}, {"markets": {}})
    assert FAIL in out and "no SPY level" in out

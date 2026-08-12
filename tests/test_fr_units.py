"""Units and labelling contract for the report's macro rows (schema v3).

On 2026-08-12 an external reviewer read the FR report against the BLS release and
concluded the CPI section was fabricated. It was not — every figure traced to a
FRED payload — but the report was *functionally misleading*:

* the CPI "m/m change" rows printed the provider's INDEX-POINT delta under a
  header that reads as percent, magnifying the core print 3.6x (+0.72 vs +0.2%);
* "Nonfarm payrolls" printed the employment LEVEL (158,858k) for a month in which
  payrolls FELL 23k, and the prose concluded the labor market was fine;
* "Dollar index" printed FRED's broad index (119) under a label every reader
  takes to mean ICE DXY (~100);
* no row carried an as-of date, so June sentiment read as current.

These tests pin the conversions to the actual BLS prints for July 2026 so the
same class of defect cannot return silently.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fr_render import (  # noqa: E402
    FAIL,
    STALE_AFTER_DAYS_DEFAULT,
    STALE_AFTER_DAYS_MONTHLY,
    STALE_AFTER_DAYS_QUARTERLY,
    STALE_TAG,
    Field,
    event_priors,
    is_stale,
    field_value,
    fmt_asof,
    level_change,
    pct_change,
    pct_distance,
    render_event_table,
    render_read,
)

# The real 2026-08-12 cpi_components payload, trimmed to the rows under test.
CPI_PAYLOAD = {
    "indicators": {
        "cpi": {"latest_value": 332.813, "previous_value": 332.568,
                "change": 0.245, "latest_date": "2026-07-01"},
        "core_cpi": {"latest_value": 336.789, "previous_value": 336.065,
                     "change": 0.724, "latest_date": "2026-07-01"},
        "cpi_energy": {"latest_value": 314.553, "previous_value": 319.29,
                       "change": -4.737, "latest_date": "2026-07-01"},
        "cpi_shelter": {"latest_value": 429.095, "previous_value": 428.501,
                        "change": 0.594, "latest_date": "2026-07-01"},
    }
}

MACRO_PAYLOAD = {
    "indicators": {
        "nonfarm_payrolls": {"latest_value": 158858.0, "previous_value": 158881.0,
                             "change": -23.0, "latest_date": "2026-07-01"},
        "consumer_sentiment": {"latest_value": 49.5, "previous_value": 44.8,
                               "change": 4.7, "latest_date": "2026-06-01"},
    }
}


# ---------------------------------------------------------------------------
# CPI: index points -> percent, checked against the BLS July 2026 release
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "series,bls_pct",
    [
        ("cpi", 0.1),        # BLS headline CPI, July 2026 SA
        ("core_cpi", 0.2),   # BLS core
        ("cpi_energy", -1.5),  # BLS energy
        ("cpi_shelter", 0.1),  # BLS shelter
    ],
)
def test_cpi_mom_matches_the_bls_print_when_rounded(series, bls_pct):
    computed = pct_change(f"indicators.{series}")(CPI_PAYLOAD)

    assert round(computed, 1) == bls_pct


def test_cpi_mom_is_not_the_raw_index_point_delta():
    """The bug: 0.724 index points rendered under a percent-reading header."""
    computed = pct_change("indicators.core_cpi")(CPI_PAYLOAD)

    assert computed == pytest.approx(0.2154, abs=1e-3)
    assert computed != CPI_PAYLOAD["indicators"]["core_cpi"]["change"]


def test_pct_change_returns_none_rather_than_dividing_by_zero():
    payload = {"x": {"latest_value": 5.0, "previous_value": 0.0}}

    assert pct_change("x")(payload) is None


def test_pct_change_returns_none_when_a_leg_is_unusable():
    """Node present but incomplete -> null (the provider had nothing to give)."""
    assert pct_change("x")({"x": {"latest_value": 5.0}}) is None


def test_pct_change_raises_when_the_path_is_absent():
    """Absent path -> a source failure, which must not render as 'not meaningful'."""
    with pytest.raises(KeyError):
        pct_change("nope")({})


# ---------------------------------------------------------------------------
# Payrolls: the change, not the level
# ---------------------------------------------------------------------------


def test_payrolls_field_reports_the_monthly_change():
    """BLS reported -23k for July 2026; the level is 158,858k."""
    assert level_change("indicators.nonfarm_payrolls")(MACRO_PAYLOAD) == -23.0


def test_payrolls_change_falls_back_to_differencing_the_levels():
    payload = {"p": {"latest_value": 158858.0, "previous_value": 158881.0}}

    assert level_change("p")(payload) == pytest.approx(-23.0)


def test_level_change_returns_none_when_it_cannot_be_computed():
    assert level_change("p")({"p": {"latest_value": 1.0}}) is None


# ---------------------------------------------------------------------------
# field_value: computed fields resolve like path fields
# ---------------------------------------------------------------------------


def test_field_value_uses_value_fn_and_ignores_path():
    f = Field("Core CPI m/m change", "cpi_components", "indicators.core_cpi.change",
              value_fn=pct_change("indicators.core_cpi"))

    value, status = field_value(CPI_PAYLOAD, f)

    assert status == "ok"
    assert value == pytest.approx(0.2154, abs=1e-3)


def test_field_value_reports_missing_when_the_computation_fails():
    f = Field("x", "src", "", value_fn=pct_change("indicators.absent"))

    _, status = field_value(CPI_PAYLOAD, f)

    assert status == "missing"


def test_field_value_falls_through_to_the_path_when_no_value_fn():
    f = Field("Consumer sentiment", "macro_dashboard",
              "indicators.consumer_sentiment.latest_value")

    assert field_value(MACRO_PAYLOAD, f) == (49.5, "ok")


# ---------------------------------------------------------------------------
# As-of dates
# ---------------------------------------------------------------------------


def test_asof_date_leads_the_read_column():
    """A fresh observation shows the date alone, with no stale tag."""
    fresh = (dt.date.today() - dt.timedelta(days=2)).isoformat()
    payload = {"indicators": {"x": {"latest_date": fresh}}}
    f = Field("x", "s", "indicators.x.v", asof_path="indicators.x.latest_date")

    read = render_read({"s": payload}, f, 49.5)

    assert read.startswith("as of ")
    assert STALE_TAG not in read


def test_asof_date_prefixes_an_existing_verdict_without_replacing_it():
    fresh = (dt.date.today() - dt.timedelta(days=2)).isoformat()
    payload = {"indicators": {"x": {"latest_date": fresh, "signal": "tight"}}}
    f = Field("x", "s", "indicators.x.v", read_path="indicators.x.signal",
              asof_path="indicators.x.latest_date")

    read = render_read({"s": payload}, f, 1.0)

    assert read.startswith("as of ")
    assert read.endswith(" — tight")


def test_rows_without_an_asof_path_are_unchanged():
    f = Field("x", "s", "p")

    assert render_read({"s": {}}, f, 1.0) == ""


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-06-01", "Jun 2026"),
        ("2026-08-07", "7 Aug 2026"),
        ("2026-13-01", "2026-13-01"),  # impossible month: pass through, never crash
        ("garbage", "garbage"),
        (None, ""),
        (42, ""),
    ],
)
def test_fmt_asof_handles_real_and_malformed_dates(raw, expected):
    assert fmt_asof(raw) == expected


# ---------------------------------------------------------------------------
# Distance from a moving average, not the average itself
# ---------------------------------------------------------------------------

SPY_PAYLOAD = {"sma": {"current_price": 773.24, "sma_200": 701.74}}


def test_spy_vs_200d_reports_the_gap_not_the_average():
    """The bug: 701.74 (the SMA) printed under a row labelled "vs 200d SMA"."""
    computed = pct_distance("sma.current_price", "sma.sma_200")(SPY_PAYLOAD)

    assert computed == pytest.approx(10.19, abs=0.01)
    assert computed != SPY_PAYLOAD["sma"]["sma_200"]


def test_pct_distance_returns_none_on_an_unusable_level():
    assert pct_distance("p", "l")({"p": 5.0, "l": 0.0}) is None
    assert pct_distance("p", "l")({"p": 5.0}) is None


# ---------------------------------------------------------------------------
# §11 economic calendar (FRED release schedule, replacing the 403'd Finnhub)
# ---------------------------------------------------------------------------

CALENDAR = {
    "events": [
        {"date": "2026-08-13", "event": "PPI (producer prices)", "impact": "high",
         "why": "pipeline inflation; feeds CPI with a lag"},
        {"date": "2026-08-14", "event": "Retail Sales", "impact": "high",
         "why": "consumer spending — the largest component of GDP"},
    ]
}


def test_event_table_renders_event_date_prior():
    table = render_event_table(CALENDAR, {"Retail Sales": "$768,553M"})

    assert "| Event | Date | Prior |" in table
    assert "| Retail Sales | 2026-08-14 | $768,553M |" in table
    # No prior gathered for PPI in this call -> em dash, never a guess.
    assert "| PPI (producer prices) | 2026-08-13 | — |" in table


def test_event_table_carries_the_why_line_per_event():
    table = render_event_table(CALENDAR)

    assert "**Retail Sales** — consumer spending" in table


def test_event_table_degrades_loudly_when_the_source_failed():
    assert FAIL in render_event_table({"error": "no key"})
    assert FAIL in render_event_table({})


def test_event_table_says_so_when_the_window_is_genuinely_empty():
    """An empty window is not a failure and must not read as one."""
    out = render_event_table({"events": []})

    assert FAIL not in out
    assert "no high-impact releases" in out


def test_event_priors_are_drawn_only_from_this_run():
    raw = {
        "cpi_components": CPI_PAYLOAD,
        "macro_dashboard": MACRO_PAYLOAD,
        "mc_RSAFS": {"latest": 768553.0},
    }
    priors = event_priors(raw)

    # The event is headline CPI, so the prior is headline (+0.07%), not core.
    assert priors["CPI (inflation)"] == "+0.07% m/m"
    assert priors["Retail Sales"] == "$768,553M"
    # PPI was not gathered here, so it is absent rather than invented.
    assert "PPI (producer prices)" not in priors


def test_event_priors_are_empty_when_nothing_was_gathered():
    assert event_priors({}) == {}


# ---------------------------------------------------------------------------
# Auto-stale flags
# ---------------------------------------------------------------------------

TODAY = dt.date(2026, 8, 12)


def test_a_month_old_monthly_series_flags_itself():
    """Consumer sentiment sat at June's value in a mid-August report uncaught."""
    assert is_stale("2026-06-01", STALE_AFTER_DAYS_MONTHLY, TODAY) is True


def test_a_current_monthly_series_does_not_flag():
    assert is_stale("2026-07-01", STALE_AFTER_DAYS_MONTHLY, TODAY) is False


def test_a_freshly_published_quarter_does_not_flag():
    """Q2 is stamped 2026-04-01 and is ~133 days 'old' the day it is published.

    A flat day-count rule would scream at every quarterly series and train the
    reader to ignore the tag.
    """
    assert is_stale("2026-04-01", STALE_AFTER_DAYS_QUARTERLY, TODAY) is False
    assert is_stale("2026-04-01", STALE_AFTER_DAYS_DEFAULT, TODAY) is True  # the naive rule


def test_daily_series_flag_only_after_the_default_window():
    assert is_stale("2026-08-07", STALE_AFTER_DAYS_DEFAULT, TODAY) is False
    assert is_stale("2026-07-01", STALE_AFTER_DAYS_DEFAULT, TODAY) is True


def test_an_unparseable_date_is_never_reported_stale():
    """A formatting problem is not a staleness claim."""
    assert is_stale("garbage", 15, TODAY) is False
    assert is_stale(None, 15, TODAY) is False
    assert is_stale(42, 15, TODAY) is False


def test_stale_rows_carry_the_tag_in_the_read_column():
    payload = {"indicators": {"x": {"latest_date": "2026-06-01"}}}
    f = Field("Consumer sentiment", "s", "indicators.x.v",
              asof_path="indicators.x.latest_date",
              stale_after_days=STALE_AFTER_DAYS_MONTHLY)

    read = render_read({"s": payload}, f, 49.5)

    assert read.startswith(STALE_TAG)
    assert "as of Jun 2026" in read

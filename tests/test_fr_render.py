"""Tests for the deterministic FR rendering layer.

These guard the properties that make it safe to hand tables to the model as
FINAL: a missing value must never become a guess, weights must renormalise when
a source fails, and the scoring arithmetic must be reproducible.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fr_render import (  # noqa: E402
    FAIL,
    Component,
    Field,
    Section,
    band,
    clamp,
    dig,
    equity_components,
    fmt_value,
    lerp_score,
    render_table,
    score,
)


class TestDig:
    def test_resolves_nested_path(self) -> None:
        assert dig({"a": {"b": {"c": 5}}}, "a.b.c") == 5

    def test_returns_default_on_missing_key(self) -> None:
        assert dig({"a": {}}, "a.b.c", "MISS") == "MISS"

    def test_treats_explicit_none_as_missing(self) -> None:
        # A provider returning null must not render as "None" in a report.
        assert dig({"a": None}, "a", "MISS") == "MISS"

    def test_indexes_into_lists(self) -> None:
        assert dig({"xs": [{"v": 9}]}, "xs.0.v") == 9


class TestFmtValue:
    def test_whole_floats_render_as_integers(self) -> None:
        assert fmt_value(6.0, "", 2) == "6"

    def test_thousands_separated(self) -> None:
        assert fmt_value(197000.0, "", 2) == "197,000"

    def test_decimals_and_unit_preserved(self) -> None:
        assert fmt_value(2.784, "pp", 2) == "2.78pp"

    def test_none_renders_as_fail_sentinel(self) -> None:
        assert fmt_value(None, "%", 2) == FAIL


class TestLerpScore:
    def test_endpoints_map_to_zero_and_hundred(self) -> None:
        assert lerp_score(10, 10, 50) == 0.0
        assert lerp_score(50, 10, 50) == 100.0

    def test_inverted_scale_supported(self) -> None:
        # Higher VIX = more bottom-like, so the scale can run downward too.
        assert lerp_score(0, 100, 0) == 100.0

    def test_clamps_outside_range(self) -> None:
        assert lerp_score(999, 10, 50) == 100.0
        assert lerp_score(-999, 10, 50) == 0.0

    def test_degenerate_range_is_neutral(self) -> None:
        assert lerp_score(5, 7, 7) == 50.0


class TestScore:
    def test_weighted_average(self) -> None:
        comps = [Component("a", 0.5, 100.0, ""), Component("b", 0.5, 0.0, "")]
        assert score(comps)[0] == 50.0

    def test_renormalises_when_a_component_is_missing(self) -> None:
        # b unavailable -> a carries the whole score, and partial flag is set.
        comps = [Component("a", 0.3, 80.0, ""), Component("b", 0.7, None, "")]
        value, _, partial = score(comps)
        assert value == 80.0
        assert partial is True

    def test_all_missing_returns_fail(self) -> None:
        value, label, partial = score([Component("a", 1.0, None, "")])
        assert value is None and label == FAIL and partial is True

    def test_not_partial_when_all_present(self) -> None:
        assert score([Component("a", 1.0, 50.0, "")])[2] is False


class TestBand:
    @pytest.mark.parametrize(
        "value,expected",
        [(10, "Euphoric"), (35, "Mid-cycle"), (55, "Neutral"), (70, "Bottom-forming"), (90, "Deep-value")],
    )
    def test_band_labels(self, value: float, expected: str) -> None:
        assert expected in band(value)


class TestRenderTable:
    def test_failed_source_renders_sentinel_not_a_guess(self) -> None:
        sec = Section("1", "T", (Field("Missing", "src", "nope.path"),))
        out = render_table({"src": {}}, sec)
        assert FAIL in out
        assert "0" not in out.split("|")[-3]

    def test_prefers_provider_signal_for_read_column(self) -> None:
        sec = Section("1", "T", (Field("HY", "cs", "v", "pp", read_path="sig"),))
        out = render_table({"cs": {"v": 2.78, "sig": "tight — complacent"}}, sec)
        assert "tight — complacent" in out
        assert "2.78pp" in out

    def test_empty_section_renders_nothing(self) -> None:
        assert render_table({}, Section("9", "Empty")) == ""


class TestEquityComponents:
    def test_weights_sum_to_one(self) -> None:
        comps = equity_components({}, {})
        assert round(sum(c.weight for c in comps), 6) == 1.0

    def test_rich_valuation_scores_near_zero(self) -> None:
        raw = {"market_valuation": {"cape": {"latest": 41.5, "percentile": 99.1}}}
        val = next(c for c in equity_components(raw, {}) if c.name == "Valuation")
        assert val.score is not None and val.score < 5

    def test_missing_sources_yield_none_not_zero(self) -> None:
        # A failed source must not score 0 (which would read as "euphoric").
        for c in equity_components({}, {}):
            assert c.score is None

    def test_credit_needs_both_index_and_tail(self) -> None:
        # HY alone cannot verify the low-quality tail is calm. When the CCC-BB
        # gap is missing the component must drop out, not score near-max —
        # otherwise a source outage renders as maximally bullish credit.
        raw = {"mc_hy_spread": {"percentile_since_start": 4.4}}
        credit = next(c for c in equity_components(raw, {}) if c.name == "Credit stress & quality")
        assert credit.score is None

    def test_credit_scores_when_both_legs_present(self) -> None:
        raw = {"mc_hy_spread": {"percentile_since_start": 4.4}}
        credit = next(
            c for c in equity_components(raw, {"ccc_minus_bb_pp": 8.6})
            if c.name == "Credit stress & quality"
        )
        # Tight index (95.6 raw) docked for a stressed tail — must land well below it.
        assert credit.score is not None and credit.score < 80


def test_clamp_bounds() -> None:
    assert clamp(-5) == 0.0
    assert clamp(150) == 100.0
    assert clamp(42.0) == 42.0


class TestCryptoValuationLeg:
    """The on-chain valuation leg is 30% of the Crypto Regime Score and had NO
    source until 2026-08-04, so it was silently renormalised out of every run."""

    def test_mvrv_resolves_when_ranked_against_history(self) -> None:
        from fr_render import crypto_components

        raw = {"btc_valuation": {"mvrv": 1.2134, "mvrv_percentile": 20.8}}
        leg = next(c for c in crypto_components(raw, {}) if c.name == "On-chain valuation")
        assert leg.score == 79.2
        assert leg.weight == 0.30

    def test_cheap_mvrv_scores_high_by_percentile(self) -> None:
        # Scored by rank vs its own history, NOT a fixed band map. The band map
        # put MVRV 1.20 at 92/100 when its true rank was the 21st percentile.
        from fr_render import crypto_components

        cheap = {"btc_valuation": {"mvrv": 0.5, "mvrv_percentile": 1.0}}
        rich = {"btc_valuation": {"mvrv": 5.0, "mvrv_percentile": 99.0}}
        cheap_leg = next(c for c in crypto_components(cheap, {}) if c.name == "On-chain valuation")
        rich_leg = next(c for c in crypto_components(rich, {}) if c.name == "On-chain valuation")
        assert cheap_leg.score == 99.0
        assert rich_leg.score == 1.0

    def test_absent_source_still_yields_none_not_zero(self) -> None:
        from fr_render import crypto_components

        leg = next(c for c in crypto_components({}, {}) if c.name == "On-chain valuation")
        assert leg.score is None

    def test_crypto_weights_sum_to_one(self) -> None:
        from fr_render import crypto_components

        assert round(sum(c.weight for c in crypto_components({}, {})), 6) == 1.0


class TestMvrvTwoSource:
    """MVRV feeds 30% of the Crypto Regime Score off free endpoints, so provenance
    and source disagreement must be visible in the report, never silently averaged."""

    def test_signal_bands(self) -> None:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
        from terminalq.providers.crypto_analytics import _mvrv_signal

        assert "capitulation" in _mvrv_signal(0.85)
        assert "stretched" in _mvrv_signal(4.0)
        assert "neither" in _mvrv_signal(1.2)

    def test_realized_price_derivation_is_exact(self) -> None:
        # realized price = (market cap / supply) / MVRV. Verified against the two
        # live sources on 2026-08-04: 1,273,346,363,617 / 20,065,323 / 1.2028.
        mcap, supply, mvrv = 1_273_346_363_617.17, 20_065_323.01, 1.202801204686642675
        assert round(mcap / supply / mvrv, 2) == 52760.21


class TestScoreBounds:
    """Every `100 - percentile` component must clamp. An out-of-range percentile
    from a provider would otherwise corrupt the weighted average and could land
    the total outside every band label."""

    def test_mvrv_percentile_over_100_clamps_to_zero(self) -> None:
        from fr_render import crypto_components

        raw = {"btc_valuation": {"mvrv": 1.2, "mvrv_percentile": 140}}
        leg = next(c for c in crypto_components(raw, {}) if c.name == "On-chain valuation")
        assert leg.score == 0.0

    def test_negative_percentile_clamps_to_hundred(self) -> None:
        from fr_render import crypto_components

        raw = {"btc_valuation": {"mvrv": 1.2, "mvrv_percentile": -5}}
        leg = next(c for c in crypto_components(raw, {}) if c.name == "On-chain valuation")
        assert leg.score == 100.0

    def test_cape_and_fear_greed_also_clamp(self) -> None:
        from fr_render import crypto_components, equity_components

        cape = next(c for c in equity_components({"market_valuation": {"cape": {"percentile": 120}}}, {})
                    if c.name == "Valuation")
        fg = next(c for c in crypto_components({"fear_greed": {"current": {"value": 130}}}, {})
                  if c.name == "Pessimism")
        assert cape.score == 0.0 and fg.score == 0.0

    def test_no_band_map_fallback_when_history_missing(self) -> None:
        # A known-miscalibrated method must not run just because the right one
        # is unavailable — drop the leg and renormalise instead.
        from fr_render import crypto_components

        leg = next(c for c in crypto_components({"btc_valuation": {"mvrv": 1.2}}, {})
                   if c.name == "On-chain valuation")
        assert leg.score is None
        assert "no history" in leg.detail

    def test_every_component_score_is_in_range(self) -> None:
        from fr_render import crypto_components, equity_components

        raw = {"market_valuation": {"cape": {"percentile": 99.1, "latest": 41.5}},
               "btc_valuation": {"mvrv": 1.2028, "mvrv_percentile": 20.8},
               "fear_greed": {"current": {"value": 25}},
               "equity_sentiment": {"vix_term_structure": {"vix": 16.5}}}
        for c in equity_components(raw, {}) + crypto_components(raw, {}):
            if c.score is not None:
                assert 0.0 <= c.score <= 100.0, f"{c.name} out of range: {c.score}"


class TestAnomalyDetection:
    """Surprising-but-valid data, as distinct from malformed data. Flags must
    never suppress a value — extreme readings are real exactly when they matter."""

    def test_historical_high_flagged(self) -> None:
        from fr_render import detect_anomalies

        flags = detect_anomalies({"mc_REVOLSL": {"percentile_since_start": 100.0, "latest": 1344207.79,
                                                 "observations": 701, "history_start": "1968-01-01",
                                                 "latest_date": "2026-05-01"}})
        assert len(flags) == 1 and "HISTORICAL HIGH" in flags[0]

    def test_historical_low_flagged(self) -> None:
        from fr_render import detect_anomalies

        flags = detect_anomalies({"mc_X": {"percentile_since_start": 0.4, "latest": 1.0,
                                           "observations": 5000, "history_start": "1960-01-01",
                                           "latest_date": "2026-01-01"}})
        assert "HISTORICAL LOW" in flags[0]

    def test_midrange_value_not_flagged(self) -> None:
        from fr_render import detect_anomalies

        assert detect_anomalies({"mc_X": {"percentile_since_start": 50.0, "latest": 1.0}}) == []

    def test_flag_never_removes_the_value(self) -> None:
        # The number must still be visible in the flag itself.
        from fr_render import detect_anomalies

        flags = detect_anomalies({"mc_X": {"percentile_since_start": 99.9, "latest": 42.5,
                                           "observations": 5000, "history_start": "1960-01-01",
                                           "latest_date": "2026-01-01"}})
        assert "42.5" in flags[0]

    def test_step_change_uses_share_of_historical_range(self) -> None:
        # Unit-free: a move is judged against the series' OWN range, so this
        # works identically for VIX and for the 10y yield.
        from fr_render import detect_anomalies

        raw = {"credit_spreads": {"indicators": {"hy": {"latest_value": 10.0, "previous_value": 2.0}}},
               "mc_hy": {"min": 0.0, "max": 20.0}}
        assert any("historical range" in f for f in detect_anomalies(raw))

    def test_small_move_not_flagged_as_step_change(self) -> None:
        from fr_render import detect_anomalies

        raw = {"credit_spreads": {"indicators": {"hy": {"latest_value": 2.05, "previous_value": 2.0}}},
               "mc_hy": {"min": 0.0, "max": 20.0}}
        assert detect_anomalies(raw) == []

    def test_malformed_payload_does_not_crash(self) -> None:
        from fr_render import detect_anomalies

        for bad in ({"x": None}, {"x": "str"}, {"x": {"percentile_since_start": "n/a"}},
                    {"x": {"indicators": {"y": None}}}):
            assert isinstance(detect_anomalies(bad), list)


class TestHistoryWindowHonesty:
    """A percentile is only as strong as the history behind it. The ICE BofA CCC
    and BB series lost everything before 2023-08-07 to a license change, so their
    ranks cover ~3 years and must never be described as historical records."""

    def test_short_history_is_flagged_and_barred_from_record_language(self) -> None:
        from fr_render import detect_anomalies

        raw = {"mc_BAMLH0A3HYC": {"percentile_since_start": 97.8, "latest": 10.28,
                                  "observations": 806, "history_start": "2023-07-07",
                                  "latest_date": "2026-08-03"}}
        flag = detect_anomalies(raw)[0]
        assert "SHORT WINDOW" in flag
        assert "HISTORICAL HIGH" not in flag
        assert "2023-07-07" in flag

    def test_long_history_may_claim_a_record(self) -> None:
        from fr_render import detect_anomalies

        raw = {"mc_hy_spread": {"percentile_since_start": 99.5, "latest": 20.0,
                                "observations": 7727, "history_start": "1996-12-31",
                                "latest_date": "2026-08-03"}}
        flag = detect_anomalies(raw)[0]
        assert "HISTORICAL HIGH" in flag
        assert "7,727 obs since 1996-12-31" in flag

    def test_every_percentile_states_its_window(self) -> None:
        from fr_render import detect_anomalies

        raw = {"mc_X": {"percentile_since_start": 99.9, "latest": 1,
                        "observations": 5000, "history_start": "1960-01-01",
                        "latest_date": "2026-01-01"}}
        assert "obs since" in detect_anomalies(raw)[0]

    def test_missing_window_metadata_treated_as_short(self) -> None:
        # Unknown provenance must degrade to the cautious reading, not the bold one.
        from fr_render import detect_anomalies, is_short_history

        assert is_short_history({}) is True
        flag = detect_anomalies({"mc_X": {"percentile_since_start": 99.9, "latest": 1}})[0]
        assert "SHORT WINDOW" in flag


class TestLongHistoryCreditReference:
    """ICE restricted every BAML* series to a rolling 3-year window in April 2026,
    so no ICE percentile can support a claim about historical extremes. The
    Gilchrist-Zakrajsek spread (Fed Board, 1973+, keyless, non-ICE) is the
    long-history reference that can."""

    def test_gz_percentile_passes_the_short_history_gate(self) -> None:
        from fr_render import is_short_history

        gz = {"history_start": "1973-01-01", "latest_date": "2026-06-01", "observations": 642}
        ice = {"history_start": "2023-08-07", "latest_date": "2026-08-03", "observations": 806}
        assert is_short_history(gz) is False
        assert is_short_history(ice) is True

    def test_gz_flag_may_claim_a_historical_extreme(self) -> None:
        from fr_render import detect_anomalies

        raw = {"mc_gz": {"percentile_since_start": 0.5, "latest": 0.5641,
                         "observations": 642, "history_start": "1973-01-01",
                         "latest_date": "2026-06-01"}}
        flag = detect_anomalies(raw)[0]
        assert "HISTORICAL LOW" in flag and "1973-01-01" in flag


class TestFundingWeighting:
    """Funding feeds 35% of the Crypto Regime Score. An unweighted cross-venue mean
    overstated it ~38x (a $412K venue quoting +9.52%/8h moved the average), pinning
    the liquidation leg at 0 and inventing a 'crowded long' signal that did not exist."""

    def test_dust_venue_cannot_move_the_aggregate(self) -> None:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
        from terminalq.providers.crypto_funding import _weighted_funding

        contracts = [
            {"market": "BigVenue", "funding_rate": 0.002, "open_interest": 20e9},
            {"market": "Ostium", "funding_rate": 9.5192, "open_interest": 412_616.0},
        ]
        out = _weighted_funding(contracts)
        assert out["venues_weighted"] == 1
        assert abs(out["funding_8h_pct"] - 0.002) < 1e-9
        assert out["excluded_as_outliers"][0]["market"] == "Ostium"

    def test_outlier_band_rejects_impossible_btc_quotes(self) -> None:
        from terminalq.providers.crypto_funding import OUTLIER_ABS_PCT_8H, _weighted_funding

        # Worst major-venue ALTCOIN funding observed was ~0.38%/8h; a BTC quote
        # beyond the band is bad data, not a market.
        assert OUTLIER_ABS_PCT_8H > 0.38
        out = _weighted_funding([
            {"market": "ok", "funding_rate": 0.01, "open_interest": 5e9},
            {"market": "bad", "funding_rate": 5.0, "open_interest": 9e9},
        ])
        assert out["venues_weighted"] == 1
        assert out["funding_8h_pct"] == 0.01

    def test_oi_weighting_favours_the_deep_venue(self) -> None:
        from terminalq.providers.crypto_funding import _weighted_funding

        out = _weighted_funding([
            {"market": "deep", "funding_rate": 0.00, "open_interest": 40e9},
            {"market": "thin", "funding_rate": 0.20, "open_interest": 1.1e9},
        ])
        # Unweighted mean would be 0.10; OI-weighting must land near the deep venue.
        assert out["funding_8h_pct"] < 0.01

    def test_no_qualifying_venues_errors_rather_than_guessing(self) -> None:
        from terminalq.providers.crypto_funding import _weighted_funding

        assert "error" in _weighted_funding([{"market": "x", "funding_rate": 0.01, "open_interest": 5.0}])

    def test_annualization_matches_convention(self) -> None:
        from terminalq.providers.crypto_funding import annualize_8h

        assert annualize_8h(0.01) == 10.95   # the ~11%/yr historical norm
        assert annualize_8h(0.0023) == 2.52  # Coinglass OI-weighted read, 2026-08-05

    def test_recalibrated_bands_score_neutral_funding_as_uncrowded(self) -> None:
        from fr_render import crypto_components

        raw = {"crypto_funding": {"funding_annualized_pct": -0.77}}
        liq = next(c for c in crypto_components(raw, {}) if c.name == "Liquidation/vol regime")
        assert liq.score is not None and liq.score > 60, "near-zero funding is washed out, not crowded"

"""Tests for the FR/EOD section specs — the report's data contract.

These guard the two newly-wired sources (dealer gamma into §6, climate risk into
a new §7) render correctly, and that the digest never silently drops a section or
substitutes a guess for a value a source failed to provide.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fr_render import FAIL  # noqa: E402

from fr_sections import SECTIONS, render_digest  # noqa: E402

# Realistic dealer-gamma payload, verified live 2026-08-06 (see task brief).
DEALER_GAMMA_SPY = {
    "symbol": "SPY",
    "spot": 769.05,
    "net_dealer_gamma": 335127313.0,
    "net_gamma_regime": "positive",
    "call_wall": 775.0,
    "put_wall": 720.0,
    "put_call_oi_ratio": 1.89,
    "signal": (
        "POSITIVE net dealer gamma — dealers hedge against the trend (buy dips, "
        "sell rips), which dampens volatility and pins price toward the call wall. "
        "Spot $769.05. call wall ~$775 (resistance); put wall ~$720 (support)."
    ),
    "note": "estimate from free Yahoo options chains",
    "source": "yahoo_finance (options, computed)",
}

# Shape confirmed against providers/climate.py's get_climate_risk_watch().
CLIMATE_RISK = {
    "regions": {
        "permian_basin": {"label": "Permian Basin (oil/gas)", "signal": "FLAGGED: +2.4C vs normal"},
        "midwest_grains": {"label": "US Midwest (grains)", "signal": "normal"},
    },
    "flagged_regions": ["Permian Basin (oil/gas)"],
    "note": "Anomalies vs 2001-2020 NASA POWER climatological normal.",
    "source": "NASA POWER (power.larc.nasa.gov) — free, no API key",
}


def _section(number: str):
    return next(s for s in SECTIONS if s.number == number)


class TestSectionSevenPlacement:
    def test_section_seven_exists(self) -> None:
        numbers = [s.number for s in SECTIONS]
        assert "7" in numbers

    def test_section_seven_titled_for_climate(self) -> None:
        sec = _section("7")
        assert "Climate" in sec.title

    def test_section_seven_positioned_between_six_and_eight(self) -> None:
        numbers = [s.number for s in SECTIONS]
        assert numbers.index("6") < numbers.index("7") < numbers.index("8")

    def test_existing_sections_not_reordered(self) -> None:
        # 6, 8, 9, 10, 12 must keep their original relative order.
        numbers = [s.number for s in SECTIONS]
        kept = [n for n in numbers if n in {"1", "2", "3", "4", "5", "6", "8", "9", "10", "12"}]
        assert kept == ["1", "2", "3", "4", "5", "6", "8", "9", "10", "12"]

    def test_section_seven_keeps_original_commodity_fields(self) -> None:
        """Keyed on the data path, not the label.

        Labels are presentational and are deliberately revised — "Dollar index"
        became "Dollar index (FRED broad, Jan 2006=100 — NOT ICE DXY)" in schema
        v3 precisely because the short label was misread as ICE DXY. What must
        not change is that §7 still sources these four series.
        """
        sec = _section("7")
        paths = [f.path for f in sec.fields]
        assert "indicators.wti_oil.latest_value" in paths
        assert "indicators.gold_price.latest_value" in paths
        assert "indicators.gasoline_price.latest_value" in paths
        assert "indicators.dollar_index.latest_value" in paths


class TestDealerGammaFieldsInSectionSix:
    def test_gamma_fields_appended_to_section_six(self) -> None:
        sec = _section("6")
        labels = [f.label for f in sec.fields]
        assert any("gamma" in l.lower() for l in labels)
        assert any("wall" in l.lower() for l in labels)
        assert any("put/call oi" in l.lower() for l in labels)

    def test_section_six_original_fields_still_present(self) -> None:
        sec = _section("6")
        labels = [f.label for f in sec.fields]
        assert "VIX" in labels
        assert "S&P 500" in labels

    def test_gamma_fields_resolve_against_realistic_payload(self) -> None:
        from fr_render import render_table

        sec = _section("6")
        raw = {"dealer_gamma_SPY": DEALER_GAMMA_SPY, "market_overview": {}, "equity_sentiment": {},
               "retail_sentiment": {}, "sector_rotation": {}, "technicals_SPY": {}}
        out = render_table(raw, sec)
        assert "335,127,313" in out
        assert "positive" in out
        assert "775" in out
        assert "720" in out
        assert "1.89" in out

    def test_gamma_signal_used_as_read_verdict(self) -> None:
        from fr_render import render_table

        sec = _section("6")
        raw = {"dealer_gamma_SPY": DEALER_GAMMA_SPY, "market_overview": {}, "equity_sentiment": {},
               "retail_sentiment": {}, "sector_rotation": {}, "technicals_SPY": {}}
        out = render_table(raw, sec)
        assert "POSITIVE net dealer gamma" in out

    def test_missing_dealer_gamma_source_renders_fail_sentinel(self) -> None:
        # A failed/missing source must render the FAIL sentinel, never a guess
        # or a zero.
        from fr_render import render_table

        sec = _section("6")
        raw = {"market_overview": {}, "equity_sentiment": {}, "retail_sentiment": {},
               "sector_rotation": {}, "technicals_SPY": {}}
        out = render_table(raw, sec)
        assert FAIL in out


class TestClimateFieldsVerifiedPaths:
    def test_commodity_paths_resolve_against_real_payload_shape(self) -> None:
        from fr_render import render_table

        sec = _section("7")
        raw = {
            "commodities": {
                "indicators": {
                    "wti_oil": {"latest_value": 81.96},
                    "gold_price": {"latest_value": 4307.1},
                    "gasoline_price": {"latest_value": 3.935},
                    "dollar_index": {"latest_value": 119.7034},
                }
            }
        }
        out = render_table(raw, sec)
        assert "81.96" in out
        assert "4,307.1" in out
        assert "3.94" in out or "3.935" in out
        assert "119.7" in out

    def test_missing_commodities_source_renders_fail_sentinel(self) -> None:
        from fr_render import render_table

        sec = _section("7")
        out = render_table({}, sec)
        assert FAIL in out


class TestRenderDigestClimateSubheading:
    def test_digest_includes_climate_subheading(self) -> None:
        raw = {"climate_risk": CLIMATE_RISK, "dealer_gamma_SPY": DEALER_GAMMA_SPY}
        out = render_digest(raw, {}, "2026-08-06", mode="fr")
        assert "### ESG & Climate Production-Risk Watch" in out

    def test_climate_subheading_appears_after_section_seven_table(self) -> None:
        raw = {"climate_risk": CLIMATE_RISK, "dealer_gamma_SPY": DEALER_GAMMA_SPY}
        out = render_digest(raw, {}, "2026-08-06", mode="fr")
        sec7_idx = out.index("## 7. Commodities")
        climate_idx = out.index("### ESG & Climate Production-Risk Watch")
        sec8_idx = out.index("## 8. Crypto Pulse")
        assert sec7_idx < climate_idx < sec8_idx

    def test_eod_mode_has_no_section_seven_or_climate_subheading(self) -> None:
        # EOD_SECTIONS has no "7", so the climate hook must not fire there.
        raw = {"climate_risk": CLIMATE_RISK}
        out = render_digest(raw, {}, "2026-08-06", mode="eod")
        assert "### ESG & Climate Production-Risk Watch" not in out

    def test_missing_climate_risk_source_does_not_crash_digest(self) -> None:
        # No climate_risk key at all — the render function (or its fallback
        # guard) must degrade gracefully, never raise.
        raw = {"dealer_gamma_SPY": DEALER_GAMMA_SPY}
        out = render_digest(raw, {}, "2026-08-06", mode="fr")
        assert "### ESG & Climate Production-Risk Watch" in out

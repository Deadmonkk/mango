"""Tests for terminalq.providers.rsu_tax — RSU vest tax-timing estimates."""

from unittest.mock import patch

import pytest

from terminalq.providers import rsu_tax


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


_SCHEDULE = [
    {"date": "2099-06-20", "grant": "2026 Grant", "pct_of_grant": "25%", "est_value": "$25,000"},
    {"date": "2099-12-20", "grant": "2026 Grant", "pct_of_grant": "25%", "est_value": "$25,000"},
    {"date": "2000-01-01", "grant": "Old Grant", "pct_of_grant": "25%", "est_value": "$10,000"},
]


async def test_rsu_tax_computes_upcoming_totals():
    with patch("terminalq.providers.rsu_tax.load_rsu_schedule", return_value=_SCHEDULE):
        result = await rsu_tax.get_rsu_tax_analysis(marginal_rate=0.30, ltcg_rate=0.15)

    assert result["source"] == "rsu_tax (local)"
    # Two future vests of $25k each = $50k gross; past vest excluded from upcoming
    totals = result["upcoming_totals"]
    assert totals["gross_value"] == 50000
    assert totals["est_ordinary_tax"] == 15000  # 30% of 50k
    assert totals["net_after_tax"] == 35000
    assert len(result["upcoming_vests"]) == 2
    assert len(result["all_vests"]) == 3
    assert "CPA" in result["note"]


async def test_rsu_tax_no_schedule_returns_error():
    with patch("terminalq.providers.rsu_tax.load_rsu_schedule", return_value=[]):
        result = await rsu_tax.get_rsu_tax_analysis()
    assert "error" in result
    assert "No RSU schedule" in result["error"]


async def test_rsu_tax_rejects_bad_rates():
    result = await rsu_tax.get_rsu_tax_analysis(marginal_rate=1.5)
    assert "error" in result


async def test_rsu_tax_skips_unparseable_rows():
    schedule = [
        {"date": "not-a-date", "est_value": "$25,000"},
        {"date": "2099-06-20", "est_value": "garbage"},
        {"date": "2099-07-20", "grant": "G", "pct_of_grant": "25%", "est_value": "$12,000"},
    ]
    with patch("terminalq.providers.rsu_tax.load_rsu_schedule", return_value=schedule):
        result = await rsu_tax.get_rsu_tax_analysis(marginal_rate=0.25)

    assert len(result["all_vests"]) == 1
    assert result["upcoming_totals"]["gross_value"] == 12000
    assert result["upcoming_totals"]["est_ordinary_tax"] == 3000


def test_parse_dollars():
    assert rsu_tax._parse_dollars("$25,000") == 25000.0
    assert rsu_tax._parse_dollars("garbage") is None
    assert rsu_tax._parse_dollars("") is None

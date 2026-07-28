"""Tests for terminalq.providers.valuation — CAPE, earnings yield, equity risk premium."""

from unittest.mock import AsyncMock, patch

import pytest

from terminalq.providers import valuation


@pytest.fixture(autouse=True)
def clear_caches(tmp_cache_dir):
    """Ensure every test starts with empty cache."""
    pass


_SAMPLE_HTML = """
<html><body>
<table id="datatable">
<tr><th>Date</th><th>Value</th></tr>
<tr><td class="left">Jun 10, 2026</td><td class="right">37.50
 estimate</td></tr>
<tr><td class="left">May 1, 2026</td><td class="right">36.20</td></tr>
<tr><td class="left">Apr 1, 2026</td><td class="right">34.80</td></tr>
</table>
</body></html>
"""


def test_parse_multpl_table_newest_first():
    rows = valuation._parse_multpl_table(_SAMPLE_HTML)

    assert rows[0] == ("Jun 10, 2026", 37.50)
    assert rows[1] == ("May 1, 2026", 36.20)
    assert len(rows) == 3


def test_parse_multpl_table_no_table_returns_empty():
    assert valuation._parse_multpl_table("<html><body>nope</body></html>") == []


async def test_get_market_valuation_computes_erp_and_percentiles():
    # CAPE newest-first: latest 35.0 ranks high vs history
    cape_values = [35.0] + [float(v) for v in range(10, 31)]
    # Earnings yield latest 3.3%
    ey_values = [3.3, 4.0, 5.0, 6.0]

    def fake_multpl(url: str):
        return cape_values if "shiller" in url else ey_values

    with (
        patch.object(valuation, "_fetch_multpl_values", AsyncMock(side_effect=fake_multpl)),
        patch.object(valuation, "_fetch_10y_yield", AsyncMock(return_value=4.4)),
    ):
        result = await valuation.get_market_valuation()

    assert result["source"] == "multpl+fred"
    assert result["cape"]["latest"] == 35.0
    assert result["cape"]["percentile"] == 100.0
    assert result["earnings_yield_pct"] == 3.3
    assert result["treasury_10y_pct"] == 4.4
    # ERP = 3.3 − 4.4
    assert result["equity_risk_premium_pct"] == round(3.3 - 4.4, 2)
    assert "negative" in result["erp_signal"].lower()
    assert "note" in result


async def test_get_market_valuation_missing_10y_skips_erp():
    with (
        patch.object(valuation, "_fetch_multpl_values", AsyncMock(return_value=[30.0, 20.0, 25.0])),
        patch.object(valuation, "_fetch_10y_yield", AsyncMock(return_value=None)),
    ):
        result = await valuation.get_market_valuation()

    assert result["cape"]["latest"] == 30.0
    assert result["equity_risk_premium_pct"] is None
    assert "unavailable" in result["erp_signal"].lower()


async def test_get_market_valuation_all_sources_failed_returns_error():
    with (
        patch.object(valuation, "_fetch_multpl_values", AsyncMock(return_value=None)),
        patch.object(valuation, "_fetch_10y_yield", AsyncMock(return_value=None)),
    ):
        result = await valuation.get_market_valuation()

    assert "error" in result
    assert result["source"] == "multpl"

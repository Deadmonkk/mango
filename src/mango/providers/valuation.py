"""Market valuation — Shiller CAPE, S&P 500 earnings yield, and equity risk premium.

CAPE and earnings yield are scraped from multpl.com's monthly tables
(free, no key; data originates from Robert Shiller's public dataset).
The 10-year Treasury yield comes from FRED. Equity risk premium (ERP) =
earnings yield minus 10y yield: how much you are paid to own stocks
instead of risk-free bonds.
"""

import asyncio
import re

import httpx

from mango.core import http
from mango.core.logging import log

from mango.core import cache
from mango.analytics import percentiles
from mango.ext_settings import CACHE_TTL_VALUATION, ERP_THIN_CUSHION_PP
from mango.core import html as _html
from mango.core import fred

CAPE_URL = "https://www.multpl.com/shiller-pe/table/by-month"
EARNINGS_YIELD_URL = "https://www.multpl.com/s-p-500-earnings-yield/table/by-month"

_DATE_RE = re.compile(r"^\w{3} \d{1,2}, \d{4}$")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _parse_multpl_table(html: str) -> list[tuple[str, float]]:
    """Parse a multpl.com monthly table into (date, value) rows, newest first."""
    rows: list[tuple[str, float]] = []
    for cells in _html.table_rows(html):
        if len(cells) < 2 or not _DATE_RE.match(cells[0]):
            continue
        match = _NUM_RE.search(cells[1].replace(",", ""))
        if not match:
            continue
        rows.append((cells[0], float(match.group())))
    return rows


async def _fetch_multpl_values(url: str) -> list[float] | None:
    """Newest-first monthly values from a multpl.com table, or None if unavailable."""
    try:
        page_html = await http.fetch_text(url, headers=_html.BROWSER_HEADERS, timeout=15)
    except httpx.HTTPError as e:
        log.warning("Valuation: multpl fetch failed for %s: %s", url, e)
        return None

    values = [value for _, value in _parse_multpl_table(page_html)]
    if not values:
        log.warning("Valuation: no table rows parsed from %s — layout may have changed", url)
        return None
    return values


async def _fetch_10y_yield() -> float | None:
    """Latest 10-year Treasury yield from FRED, or None if unavailable."""
    result = await fred.get_series("DGS10", limit=1)
    observations = result.get("observations") if "error" not in result else None
    if not observations:
        log.warning("Valuation: 10y Treasury yield unavailable from FRED")
        return None
    return float(observations[0]["value"])


def _cape_block(values: list[float] | None) -> dict | None:
    if not values:
        return None
    latest = values[0]
    pct = percentiles.percentile_rank(values, latest)
    return {
        "latest": latest,
        "percentile": pct,
        "min": min(values),
        "max": max(values),
        "interpretation": f"CAPE is {percentiles.describe_percentile(pct)}",
    }


def _erp_signal(erp: float | None) -> str:
    if erp is None:
        return "unavailable — missing earnings yield or 10y Treasury data"
    if erp < 0:
        return (
            f"negative ({erp}pp) — stocks yield less than risk-free Treasuries; "
            "the market is priced for perfection with no valuation cushion"
        )
    if erp < ERP_THIN_CUSHION_PP:
        return (
            f"thin ({erp}pp) — minimal extra compensation for owning stocks over bonds; "
            "valuation offers little downside protection"
        )
    return f"adequate ({erp}pp) — stocks pay a reasonable premium over risk-free bonds"


async def get_market_valuation() -> dict:
    """Get market valuation: Shiller CAPE (with percentile), earnings yield, and ERP.

    Returns:
        Dict with CAPE context vs ~150 years of monthly history, the S&P 500
        earnings yield, the 10y Treasury yield, and the equity risk premium
        with a plain-English signal — or an error dict if multpl is down.
    """
    cache_key = "market_valuation"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    cape_values, ey_values, treasury_10y = await asyncio.gather(
        _fetch_multpl_values(CAPE_URL),
        _fetch_multpl_values(EARNINGS_YIELD_URL),
        _fetch_10y_yield(),
    )
    if cape_values is None and ey_values is None:
        return {
            "error": "Could not fetch valuation tables from multpl.com — site blocked or layout changed",
            "source": "multpl",
        }

    earnings_yield = ey_values[0] if ey_values else None
    erp = round(earnings_yield - treasury_10y, 2) if earnings_yield is not None and treasury_10y is not None else None

    result = {
        "cape": _cape_block(cape_values),
        "earnings_yield_pct": earnings_yield,
        "treasury_10y_pct": treasury_10y,
        "equity_risk_premium_pct": erp,
        "erp_signal": _erp_signal(erp),
        "note": (
            "CAPE (cyclically-adjusted P/E) = price over 10-year average real earnings; "
            "percentile is vs all monthly observations since 1871. Equity risk premium = "
            "S&P 500 earnings yield minus 10y Treasury yield — the extra annual return "
            "stocks offer over risk-free bonds at today's prices."
        ),
        "source": "multpl+fred",
    }
    cache.set(cache_key, result, CACHE_TTL_VALUATION)
    return result

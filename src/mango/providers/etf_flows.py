"""Farside Investors provider — daily spot Bitcoin ETF flows (free, scraped, no API key).

Daily creations/redemptions across the US spot Bitcoin ETFs (IBIT, FBTC,
GBTC, ...) are the most direct gauge of institutional demand for Bitcoin.
"""

import re

import httpx

from mango.core import http
from mango.core.logging import log

from mango.core import cache
from mango.ext_settings import CACHE_TTL_ETF_FLOWS, ETF_FLOWS_DEFAULT_DAYS
from mango.core import html as _html

BASE_URL = "https://farside.co.uk/btc/"

_DATE_RE = re.compile(r"^\d{1,2} \w{3} \d{4}$")
_TICKER_RE = re.compile(r"^[A-Z]{2,6}$")
_MIN_TICKER_CELLS = 3


def _parse_value(text: str) -> float:
    """Parse a Farside flow cell: '(123.4)' = −123.4, '-' or '' = 0, commas stripped."""
    cleaned = text.strip().replace(",", "")
    if not cleaned or cleaned in {"-", "–", "—"}:
        return 0.0
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    try:
        value = float(cleaned)
    except ValueError:
        return 0.0
    return -value if negative else value


def _is_fund_header(cells: list[str]) -> bool:
    """True for the row carrying ETF ticker names (IBIT, FBTC, ...).

    The live page has misleading neighbors: a logo row whose only text is
    'Total', and a fee row of percentages. The ticker row is the one where
    the non-first cells are uppercase symbols (a trailing 'Total' label or
    empty cells are tolerated).
    """
    tail = [c for c in cells[1:] if c]
    tickers = [c for c in tail if _TICKER_RE.match(c)]
    return len(tickers) >= _MIN_TICKER_CELLS and all(_TICKER_RE.match(c) or c == "Total" for c in tail)


def _parse_farside_html(html: str) -> list[dict]:
    """Parse the Farside flows table into daily entries (oldest first).

    Returns [{"date", "total_usd_m", "flows": {fund: usd_m}}], skipping
    the cumulative summary row whose first cell is not a date.
    """
    funds: list[str] = []
    days: list[dict] = []

    for cells in _html.table_rows(html):
        if len(cells) < 3:
            continue
        if not funds and _is_fund_header(cells):
            funds = cells[1:-1]
            continue
        if not _DATE_RE.match(cells[0]):
            continue  # cumulative "Total"/"Average" summary rows
        values = [_parse_value(c) for c in cells[1:]]
        flows = dict(zip(funds, values[: len(funds)])) if funds else {}
        days.append({"date": cells[0], "total_usd_m": values[-1], "flows": flows})

    return days


async def get_btc_etf_flows(days: int = ETF_FLOWS_DEFAULT_DAYS) -> dict:
    """Get recent daily spot Bitcoin ETF flows in US$ millions.

    Args:
        days: Number of most recent daily rows to return.

    Returns:
        Dict with daily flows, latest-day detail, window net flow, and a
        demand signal — or an error dict if Farside is unreachable.
    """
    cache_key = f"farside_btc_etf_flows_{days}"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    try:
        page_html = await http.fetch_text(
            BASE_URL, headers=_html.BROWSER_HEADERS, timeout=15
        )
    except httpx.TimeoutException:
        log.warning("Farside timeout")
        return {"error": "Request timed out", "source": "farside"}
    except httpx.HTTPStatusError as e:
        log.warning("Farside HTTP %d", e.response.status_code)
        return {
            "error": f"HTTP {e.response.status_code} — Farside may be blocking automated requests",
            "source": "farside",
        }
    except httpx.HTTPError as e:
        log.error("Farside connection failed: %s", e)
        return {"error": "Connection failed", "source": "farside"}

    all_days = _parse_farside_html(page_html)
    if not all_days:
        log.warning("Farside page fetched but no flows table found — layout may have changed")
        return {"error": "Could not parse flows table — page layout may have changed", "source": "farside"}

    window = all_days[-days:]
    latest = window[-1]
    net_flow = round(sum(d["total_usd_m"] for d in window), 1)

    if net_flow > 0:
        signal = f"net INFLOW of ${net_flow}M over last {len(window)} sessions — institutions accumulating"
    elif net_flow < 0:
        signal = f"net OUTFLOW of ${abs(net_flow)}M over last {len(window)} sessions — institutions distributing"
    else:
        signal = "flat — no net institutional flow"

    result = {
        "daily": window,
        "latest": latest,
        "window_net_flow_usd_m": net_flow,
        "signal": signal,
        "note": "Values in US$ millions. Daily creations/redemptions across US spot Bitcoin ETFs — the most direct gauge of institutional BTC demand.",
        "source": "farside",
    }
    cache.set(cache_key, result, CACHE_TTL_ETF_FLOWS)
    return result

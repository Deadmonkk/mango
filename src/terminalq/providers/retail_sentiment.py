"""Retail sentiment — AAII weekly survey (scrape) + SPY options put/call (yfinance).

AAII is the classic retail mood survey (running since 1987): extreme bearish
readings have historically been contrarian buy signals. The SPY put/call
volume ratio is the options market's real-money version of the same question.
CBOE's official total put/call feed is bot-walled, so the ratio here is
computed from SPY's listed option chain — labeled accordingly.
"""

import asyncio
import re
import statistics

import httpx
from terminalq.logging_config import log

from terminalq import cache
from terminalq._lazy_yfinance import yfinance
from terminalq.ext_settings import (
    AAII_SPREAD_EXTREME_PP,
    CACHE_TTL_RETAIL_SENTIMENT,
    PUT_CALL_COMPLACENT_RATIO,
    PUT_CALL_FEAR_RATIO,
)
from terminalq.providers import _html

AAII_URL = "https://www.aaii.com/sentimentsurvey/sent_results"

_WEEK_RE = re.compile(r"^[A-Z][a-z]{2} \d{1,2}$")
_PCT_RE = re.compile(r"(\d{1,2}\.\d)\s*%")

_TREND_WEEKS = 4
_EXPIRATIONS_SAMPLED = 3  # nearest SPY expirations to aggregate


def _parse_aaii_table(page_html: str) -> list[dict]:
    """Parse the AAII results table into weekly rows, newest first."""
    rows: list[dict] = []
    for cells in _html.table_rows(page_html):
        if len(cells) < 4 or not _WEEK_RE.match(cells[0]):
            continue
        percents = [_PCT_RE.search(c) for c in cells[1:4]]
        if not all(percents):
            continue
        bullish, neutral, bearish = (float(m.group(1)) for m in percents)
        rows.append({"week": cells[0], "bullish": bullish, "neutral": neutral, "bearish": bearish})
    return rows


async def _fetch_aaii_survey() -> list[dict]:
    """Weekly AAII survey rows (newest first), or [] if unavailable."""
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(AAII_URL, headers=_html.BROWSER_HEADERS, timeout=20)
            resp.raise_for_status()
            page_html = resp.text
    except httpx.HTTPError as e:
        log.warning("AAII fetch failed: %s", e)
        return []

    rows = _parse_aaii_table(page_html)
    if not rows:
        log.warning("AAII page fetched but no survey table parsed — layout may have changed")
    return rows


async def _fetch_spy_put_call() -> dict | None:
    """SPY options put/call volume ratio across the nearest expirations."""

    def _volumes() -> tuple[float, float]:
        ticker = yfinance.Ticker("SPY")
        puts = calls = 0.0
        for expiration in ticker.options[:_EXPIRATIONS_SAMPLED]:
            chain = ticker.option_chain(expiration)
            puts += float(chain.puts["volume"].fillna(0).sum())
            calls += float(chain.calls["volume"].fillna(0).sum())
        return puts, calls

    try:
        put_volume, call_volume = await asyncio.to_thread(_volumes)
    except Exception as e:  # yfinance raises a grab-bag of exception types
        log.warning("SPY option chain fetch failed: %s", e)
        return None

    if not call_volume:
        return None
    return {
        "ratio": round(put_volume / call_volume, 2),
        "put_volume": put_volume,
        "call_volume": call_volume,
    }


def _aaii_block(survey: list[dict]) -> dict | None:
    if not survey:
        return None
    latest = survey[0]
    spread = round(latest["bullish"] - latest["bearish"], 1)
    recent = survey[:_TREND_WEEKS]
    spread_4wk = round(statistics.mean(r["bullish"] - r["bearish"] for r in recent), 1)

    if spread <= -AAII_SPREAD_EXTREME_PP:
        signal = f"excessive pessimism — bears outnumber bulls by {abs(spread)}pp; historically a contrarian BUY zone"
    elif spread >= AAII_SPREAD_EXTREME_PP:
        signal = f"excessive optimism — bulls outnumber bears by {spread}pp; historically a contrarian caution zone"
    else:
        signal = "balanced — retail mood near neutral, no contrarian edge"

    return {
        "week": latest["week"],
        "bullish_pct": latest["bullish"],
        "neutral_pct": latest["neutral"],
        "bearish_pct": latest["bearish"],
        "bull_bear_spread": spread,
        "bull_bear_spread_4wk_avg": spread_4wk,
        "signal": signal,
    }


def _put_call_block(put_call: dict | None) -> dict:
    if put_call is None:
        return {"ratio": None, "signal": "data unavailable (source failed)"}
    ratio = put_call["ratio"]
    if ratio >= PUT_CALL_FEAR_RATIO:
        signal = f"elevated ({ratio}) — heavy put buying, fear/hedging dominates"
    elif ratio <= PUT_CALL_COMPLACENT_RATIO:
        signal = f"low ({ratio}) — call-heavy, complacency/speculation dominates"
    else:
        signal = f"normal ({ratio}) — balanced hedging activity"
    return {**put_call, "signal": signal}


async def get_retail_sentiment() -> dict:
    """Get retail sentiment: AAII weekly survey + SPY options put/call ratio.

    Returns:
        Dict with the latest AAII bullish/neutral/bearish split, bull-bear
        spread with 4-week trend, and the SPY put/call volume ratio — or an
        error dict if both sources failed.
    """
    cache_key = "retail_sentiment"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    survey, put_call = await asyncio.gather(_fetch_aaii_survey(), _fetch_spy_put_call())

    aaii = _aaii_block(survey)
    if aaii is None and put_call is None:
        return {"error": "Both AAII and SPY option chain sources failed", "source": "aaii + yahoo_finance"}

    result = {
        "aaii_survey": aaii if aaii else {"signal": "data unavailable (source failed)"},
        "spy_put_call": _put_call_block(put_call),
        "note": (
            "AAII surveys individual investors weekly (since 1987); the bull-bear spread "
            "beyond ±10pp has historically been a contrarian signal. Put/call is the SPY "
            "option volume ratio across the nearest expirations (computed from Yahoo; "
            "CBOE's official total ratio is not freely accessible) — above ~1.2 = fear, "
            "below ~0.7 = complacency."
        ),
        "source": "aaii + yahoo_finance",
    }
    cache.set(cache_key, result, CACHE_TTL_RETAIL_SENTIMENT)
    return result

"""FOMC meeting calendar — scraped from federalreserve.gov (free, no key).

Free fallback for the economic calendar: FOMC decision dates are the
highest-impact scheduled events for risk assets, and the Fed publishes
them years in advance on a stable page.
"""

import re
from datetime import date, timedelta

import httpx
from terminalq.logging_config import log

from terminalq import cache
from terminalq.ext_settings import CACHE_TTL_FOMC
from terminalq.providers import _html

CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

_YEAR_RE = re.compile(r"(\d{4}) FOMC Meetings")
_MEETING_RE = re.compile(
    r"fomc-meeting__month[^>]*>(.*?)</div>\s*<div[^>]*fomc-meeting__date[^>]*>(.*?)</div>",
    re.S,
)
_MONTHS = {
    name: number
    for number, name in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        start=1,
    )
}


def _parse_fomc_html(html: str) -> list[date]:
    """Decision dates (final day of each meeting), parsed per year block.

    Month-spanning meetings ('April/May', '30-1') resolve to the second
    month and the last listed day — the day the decision is announced.
    """
    year_blocks = list(_YEAR_RE.finditer(html))
    meetings: list[date] = []
    for index, year_match in enumerate(year_blocks):
        year = int(year_match.group(1))
        block_start = year_match.end()
        block_end = year_blocks[index + 1].start() if index + 1 < len(year_blocks) else len(html)
        for meeting in _MEETING_RE.finditer(html[block_start:block_end]):
            month_text = _html.strip_tags(meeting.group(1))
            date_text = _html.strip_tags(meeting.group(2))
            day_numbers = re.findall(r"\d{1,2}", date_text)
            month = _MONTHS.get(month_text.split("/")[-1].strip().lower())
            if not month or not day_numbers:
                continue
            try:
                meetings.append(date(year, month, int(day_numbers[-1])))
            except ValueError:
                continue
    return sorted(meetings)


async def get_fomc_meetings(days_ahead: int = 7) -> dict:
    """Get upcoming FOMC meetings within a window, plus the next decision date.

    Args:
        days_ahead: Window size in days for the events list.

    Returns:
        Dict with events in the window, the next FOMC decision date with a
        countdown, and a note — or an error dict if the Fed site is unreachable.
    """
    cache_key = f"fomc_meetings_{days_ahead}"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(CALENDAR_URL, headers=_html.BROWSER_HEADERS, timeout=15)
            resp.raise_for_status()
            page_html = resp.text
    except httpx.TimeoutException:
        log.warning("Fed calendar timeout")
        return {"error": "Request timed out", "source": "federalreserve.gov"}
    except httpx.HTTPStatusError as e:
        log.warning("Fed calendar HTTP %d", e.response.status_code)
        return {"error": f"HTTP {e.response.status_code}", "source": "federalreserve.gov"}
    except httpx.HTTPError as e:
        log.error("Fed calendar connection failed: %s", e)
        return {"error": "Connection failed", "source": "federalreserve.gov"}

    meetings = _parse_fomc_html(page_html)
    if not meetings:
        log.warning("Fed calendar fetched but no meetings parsed — layout may have changed")
        return {"error": "Could not parse FOMC calendar — page layout may have changed", "source": "federalreserve.gov"}

    today = date.today()
    upcoming = [m for m in meetings if m >= today]
    window_end = today + timedelta(days=days_ahead)
    events = [
        {"date": m.isoformat(), "event": "FOMC meeting — rate decision", "impact": "high"}
        for m in upcoming
        if m <= window_end
    ]
    next_meeting = upcoming[0] if upcoming else None

    result = {
        "events": events,
        "next_fomc": (
            {"date": next_meeting.isoformat(), "days_until": (next_meeting - today).days} if next_meeting else None
        ),
        "note": (
            "FOMC dates from the Federal Reserve's published calendar. Each date is the "
            "final (decision/press conference) day of the meeting."
        ),
        "source": "federalreserve.gov",
    }
    cache.set(cache_key, result, CACHE_TTL_FOMC)
    return result

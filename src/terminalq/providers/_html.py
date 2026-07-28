"""Shared HTML-scraping helpers for the table-scraping providers.

Used by the free scraped sources (Farside ETF flows, multpl valuation, AAII
sentiment, federalreserve.gov FOMC calendar) so the browser User-Agent and the
tag/row/cell parsing live in one place instead of being copied per module.
"""

import html as _html_lib
import re

# Several sources sit behind basic bot filtering — send a browser-like User-Agent.
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}

_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def strip_tags(fragment: str) -> str:
    """Remove HTML tags, unescape entities, and trim whitespace."""
    return _html_lib.unescape(_TAG_RE.sub("", fragment)).strip()


def table_rows(html: str) -> list[list[str]]:
    """Parse every <tr> into a list of its plain-text cell contents."""
    return [[strip_tags(cell) for cell in _CELL_RE.findall(row)] for row in _ROW_RE.findall(html)]

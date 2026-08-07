"""HTML scraping utilities: browser headers, tag stripping, table parsing.

This module provides lightweight HTML extraction without third-party parsers.
It is robust to real-world malformed HTML (unclosed tags, uppercase tag names,
attributes containing `>` in quotes, nested tables, extra whitespace).
"""

from __future__ import annotations

import re
# Avoid name collision with this module; html_stdlib.unescape() is the escape-sequence decoder.
import html as html_stdlib

# Browser headers that make requests look like a user's browser, rather than
# a Python script. Some sites (e.g., multpl.com, federalreserve.gov) throttle
# or reject default Python user agents. This is the minimal set to pass.
BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}

# Regex to find any HTML tag (opening, closing, or self-closing), including
# malformed ones (uppercase, attributes, extra spaces). The `?` makes `.*?`
# non-greedy, so `<a>text</a>` matches `<a>` and `</a>` separately, not the
# entire string.
#
# Handles:
# - `<tag>`, `</tag>`, `<tag />`, `<TAG>`, `</TAG>`
# - Attributes: `<div class="x">`, `<div class="x > y">`
# - Extra spaces: `<  tr  >`, `<  /td  >`, `<  div  >`
# A tag runs to the first `>` that is NOT inside a quoted attribute value.
# `[^>]*` breaks on markup like `<a href="x>y">`, leaving `y">` as visible text —
# and that pattern is common in real pages (query strings, inline JSON).
_TAG_RE = re.compile(r"""<\s*/?\s*[A-Za-z!/?][^>"']*(?:(?:"[^"]*"|'[^']*')[^>"']*)*>""")

# Row/cell boundaries are matched on OPENING tags only; see table_rows for why.
_ROW_OPEN_RE = re.compile(r"<\s*tr\b[^>]*>", re.IGNORECASE)
_ROW_CLOSE_RE = re.compile(r"<\s*/\s*tr\s*>", re.IGNORECASE)
_TABLE_CLOSE_RE = re.compile(r"<\s*/\s*table\s*>", re.IGNORECASE)
_CELL_OPEN_RE = re.compile(r"<\s*(?:td|th)\b[^>]*>", re.IGNORECASE)
_CELL_CLOSE_RE = re.compile(r"<\s*/\s*(?:td|th)\s*>", re.IGNORECASE)

# Regex for consecutive whitespace (spaces, tabs, newlines). Used to collapse
# runs of whitespace to a single space after tag removal.
_WHITESPACE_RE = re.compile(r"\s+")


def strip_tags(html: str) -> str:
    """Remove HTML tags and decode entities from a fragment, returning clean text.

    Handles:
    - HTML entities: `&amp;` → `&`, `&nbsp;` → space, `&#160;` → space, etc.
    - Tag removal: `<div>text</div>` → `text`
    - Whitespace collapse: multiple spaces/newlines → single space
    - Edge cases: non-string or empty input → `""`

    Args:
        html: HTML fragment as a string.

    Returns:
        Plain text with tags stripped, entities decoded, and whitespace normalized.
    """
    if not isinstance(html, str) or not html:
        return ""

    # Remove all HTML tags (opening, closing, self-closing), replacing each
    # with a space to preserve word boundaries (e.g., "word1<br/>word2" → "word1 word2").
    text = _TAG_RE.sub(" ", html)

    # Decode HTML entities (`&amp;` → `&`, `&nbsp;` → ` `, numeric entities, etc).
    text = html_stdlib.unescape(text)

    # Collapse consecutive whitespace (spaces, tabs, newlines) to single spaces.
    text = _WHITESPACE_RE.sub(" ", text)

    # Strip leading and trailing whitespace.
    return text.strip()


def table_rows(html: str) -> list[list[str]]:
    """Extract rows from HTML tables as lists of cell text.

    Parses `<table>` elements and returns one list of cell strings per `<tr>`.
    Treats both `<td>` and `<th>` as cells. Each cell is passed through the
    same tag-stripping and entity-decoding logic as `strip_tags()`.

    Tolerates:
    - Malformed HTML: unclosed tags, uppercase tag names, extra whitespace
    - Attributes with `>` inside quotes: `<td data="x > y">` doesn't break parsing
    - Nested tables (extracts outer table's cells, skipping inner `<tr>/<td>`)
    - No tables or unparseable input → returns `[]` instead of raising

    Args:
        html: HTML document or fragment as a string.

    Returns:
        List of rows, each row a list of cell strings. Rows with no cells are
        skipped. Returns `[]` if no tables are found or input is invalid.
    """
    if not isinstance(html, str) or not html:
        return []

    rows: list[list[str]] = []

    # Delimit rows and cells by their OPENING tags rather than by matched
    # open/close pairs. A pair-matching regex silently yields nothing for
    # `<tr><td>a<td>b</tr>`, and omitting `</td>` is legal HTML that real
    # scraped pages emit. Returning [] there would look like "no data" rather
    # than "parser too strict" — the failure would be invisible at the call
    # site, which scrapes CAPE, AAII sentiment and FOMC dates.
    for tr_match in _ROW_OPEN_RE.finditer(html):
        start = tr_match.end()
        end = _row_end(html, start)
        cells = _cells_in(html[start:end])
        if cells:  # skip rows that contain no cells at all
            rows.append(cells)

    return rows


def _row_end(html: str, start: int) -> int:
    """End of a row: the next row start, row close, or table close."""
    candidates = [
        m.start()
        for m in (
            _ROW_OPEN_RE.search(html, start),
            _ROW_CLOSE_RE.search(html, start),
            _TABLE_CLOSE_RE.search(html, start),
        )
        if m
    ]
    return min(candidates) if candidates else len(html)


def _cells_in(row_html: str) -> list[str]:
    """Split a row's inner HTML into cell texts, tolerating unclosed cells."""
    cells: list[str] = []
    for cell_match in _CELL_OPEN_RE.finditer(row_html):
        start = cell_match.end()
        nxt = _CELL_OPEN_RE.search(row_html, start)
        close = _CELL_CLOSE_RE.search(row_html, start)
        ends = [m.start() for m in (nxt, close) if m]
        end = min(ends) if ends else len(row_html)
        cells.append(strip_tags(row_html[start:end]))
    return cells

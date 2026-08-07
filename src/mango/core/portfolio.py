"""Markdown parsers for the user's local portfolio data files.

Reads three hand-maintained markdown files from `PORTFOLIO_DIR` (an env var,
defaulting to `~/.terminalq/`):

  - `portfolio-holdings.md` — one or more `## <account>` sections, each
    holding a pipe table of positions.
  - `rsu-schedule.md` — one or more `## <year> Grant: ...` sections, each
    (eventually) containing a vesting-schedule pipe table.
  - `watchlist.md` — a single pipe table of tracked symbols.

This is a clean-room implementation written directly from a written format
specification, not from any existing parser in this codebase family. Every
public function is defensive: a missing or malformed file degrades to an
empty result (plus a logged warning) rather than raising, since these files
are hand-edited and callers should not need to guard every call site.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from mango.core.logging import log

# --- File locations ---------------------------------------------------------

# Env var that overrides the default data directory (also used by tests to
# point at a synthetic fixture directory).
_ENV_VAR_PORTFOLIO_DIR = "PORTFOLIO_DIR"
_DEFAULT_PORTFOLIO_DIR = Path.home() / ".terminalq"

PORTFOLIO_HOLDINGS_FILENAME = "portfolio-holdings.md"
RSU_SCHEDULE_FILENAME = "rsu-schedule.md"
WATCHLIST_FILENAME = "watchlist.md"


def _resolve_portfolio_dir() -> Path:
    """Resolve the portfolio data directory once, at import time.

    Design choice: resolving `PORTFOLIO_DIR` per-call (instead of caching it
    as a module-level constant) defeats test isolation, because tests
    monkeypatch this module's `PORTFOLIO_DIR` attribute rather than the
    process environment. Per-call resolution has also previously caused a
    test run to silently fall through to real user data in this project, so
    resolving once at import time and treating the result as the single
    source of truth is the safer default.
    """
    raw_dir = os.environ.get(_ENV_VAR_PORTFOLIO_DIR)
    if raw_dir:
        return Path(raw_dir).expanduser()
    return _DEFAULT_PORTFOLIO_DIR


# Module-level constant — tests monkeypatch this attribute directly
# (`monkeypatch.setattr(portfolio, "PORTFOLIO_DIR", tmp_path)`).
PORTFOLIO_DIR: Path = _resolve_portfolio_dir()

# --- Portfolio holdings contract --------------------------------------------

# Fixed output-key order for a holdings row. Positional (not header-text
# derived) because the public contract for `load_portfolio()` is fixed by
# existing callers regardless of what the table's header cells literally say.
_PORTFOLIO_COLUMNS: tuple[str, ...] = (
    "symbol",
    "name",
    "shares",
    "cost_basis",
    "market_value",
    "unrealized_gl",
)

# Symbols that represent cash/placeholder rows rather than a tradable
# position — excluded from `get_unique_symbols()`.
_NON_TRADABLE_SYMBOLS = frozenset({"CASH"})

_GRANT_SECTION_KEY = "grant_section"

# --- Heading / value regexes -------------------------------------------------

# `# Portfolio Holdings (as of Jan 1, 2026)` -> "Jan 1, 2026"
_H1_AS_OF_PATTERN = re.compile(r"^#\s+Portfolio Holdings\s*\(as of\s+(?P<as_of>[^)]+)\)", re.IGNORECASE)

# `## <heading>` — deliberately does not match `### <heading>` (a third `#`
# is not whitespace, so `\s+` fails to match right after the required `##`).
_H2_HEADING_PATTERN = re.compile(r"^##\s+(?P<heading>.+?)\s*$")

# Strips a trailing parenthetical off an account heading, e.g.
# "Brokerage Account (1234)" -> "Brokerage Account".
_ACCOUNT_PAREN_STRIP_PATTERN = re.compile(r"\s*\([^)]*\)\s*$")

# Accounting-style negative: "(225.00)" -> negative 225.00.
_PAREN_NEGATIVE_PATTERN = re.compile(r"^\((.*)\)$")

# Placeholder tokens (case-insensitive) that mean "no value" -> 0.0.
_MISSING_VALUE_TOKENS = frozenset({"", "-", "—", "–", "n/a", "na"})


# --- Shared markdown-table parsing helper -----------------------------------
#
# All three files are pipe tables with the same shape (header row, `|---|`
# separator row, data rows). One shared helper is used everywhere instead of
# repeating table-parsing logic per file, per this project's DRY convention.


def _split_row(line: str) -> list[str]:
    """Split one markdown table row into stripped cell strings.

    Tolerates a missing leading/trailing `|` and extra surrounding
    whitespace, since hand-edited tables are inconsistent about both.
    """
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_table_row(line: str) -> bool:
    """True if `line` looks like a pipe-table row (header, separator, or data).

    Requires at least one `|` but does not require a leading/trailing pipe —
    GitHub-flavored markdown tables are valid without them, and hand-edited
    files are inconsistent about including them.
    """
    return "|" in line.strip()


def _is_separator_line(line: str) -> bool:
    """True if `line` is a `|---|:---:|---|`-style header/body separator.

    Checked on the whole line rather than per-cell so a ragged separator
    (mismatched column count, missing colons, etc.) is still recognized —
    the only requirement is that once pipes, colons, and whitespace are
    stripped away, only dashes are left.
    """
    condensed = line.strip().strip("|")
    core = condensed.replace("-", "").replace(":", "").replace("|", "").strip()
    return core == "" and "-" in condensed


def _parse_table_block(lines: list[str], start_idx: int) -> tuple[list[str], list[list[str]], int]:
    """Parse one markdown table starting at `lines[start_idx]` (the header row).

    Returns `(header_cells, data_rows, index_after_table)`. The `|---|`
    separator row is consumed and skipped if present; its absence does not
    stop parsing (best-effort — malformed tables should degrade, not raise).
    """
    header_cells = _split_row(lines[start_idx])
    idx = start_idx + 1
    if idx < len(lines) and _is_separator_line(lines[idx]):
        idx += 1

    data_rows: list[list[str]] = []
    while idx < len(lines) and _is_table_row(lines[idx]):
        if not _is_separator_line(lines[idx]):
            data_rows.append(_split_row(lines[idx]))
        idx += 1

    return header_cells, data_rows, idx


def _header_to_key(header: str) -> str:
    """Snake-case a table header cell for use as a dict key.

    E.g. "Pct of Grant" -> "pct_of_grant", "Est Value" -> "est_value".
    """
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", header.strip().lower())
    return cleaned.strip("_")


def _row_from_cells(keys: list[str], cells: list[str]) -> dict[str, str]:
    """Zip a data row's cells onto `keys`, padding/truncating on a mismatch.

    Ragged tables (a row with fewer or more cells than the header) must not
    raise — pad short rows with empty strings, truncate long ones.
    """
    padded = (list(cells) + [""] * len(keys))[: len(keys)]
    return {keys[i]: padded[i] for i in range(len(keys))}


def _read_file(filename: str) -> str | None:
    """Read a portfolio data file, returning None (with a warning) on any failure."""
    path = PORTFOLIO_DIR / filename
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.warning("mango.portfolio: %s not found under %s", filename, PORTFOLIO_DIR)
        return None
    except OSError as exc:
        log.warning("mango.portfolio: failed to read %s: %s", filename, exc)
        return None


def _coerce_numeric(raw: str) -> float:
    """Coerce a raw table cell into the float the portfolio contract requires.

    Handles a leading `$`, thousands-separator commas, a trailing `%`, and
    accounting-style parenthesized negatives. Blank/dash/em-dash placeholders
    and anything else unparseable become `0.0` (logged) rather than raising —
    callers sum these fields directly (`sum(h["market_value"] for h in ...)`),
    so a non-numeric value would break every caller, not just this one.
    """
    if raw is None:
        return 0.0

    text = raw.strip()
    if text.lower() in _MISSING_VALUE_TOKENS:
        return 0.0

    is_negative = False
    paren_match = _PAREN_NEGATIVE_PATTERN.match(text)
    if paren_match:
        is_negative = True
        text = paren_match.group(1)

    text = text.replace("$", "").replace(",", "").replace("%", "").strip()
    if text.lower() in _MISSING_VALUE_TOKENS:
        return 0.0

    try:
        value = float(text)
    except ValueError:
        log.warning("mango.portfolio: could not parse numeric value %r, defaulting to 0.0", raw)
        return 0.0

    return -value if is_negative else value


def _strip_account_parenthetical(heading: str) -> str:
    return _ACCOUNT_PAREN_STRIP_PATTERN.sub("", heading).strip()


def _build_holding(account: str, cells: list[str]) -> dict[str, Any]:
    padded = (list(cells) + [""] * len(_PORTFOLIO_COLUMNS))[: len(_PORTFOLIO_COLUMNS)]
    symbol, name, shares, cost_basis, market_value, unrealized_gl = padded
    return {
        "account": account,
        "symbol": symbol.strip(),
        "name": name.strip(),
        "shares": _coerce_numeric(shares),
        "cost_basis": _coerce_numeric(cost_basis),
        "market_value": _coerce_numeric(market_value),
        "unrealized_gl": _coerce_numeric(unrealized_gl),
    }


# --- Public API ---------------------------------------------------------------


def load_portfolio() -> list[dict[str, Any]]:
    """Load every holding row across every `## <account>` section.

    Returns `[]` if the file is missing. `account` is the enclosing `## `
    heading with any trailing parenthetical stripped (e.g.
    "Brokerage Account (1234)" -> "Brokerage Account"). The four numeric
    fields (`shares`, `cost_basis`, `market_value`, `unrealized_gl`) are
    always `float`; unparseable or missing values become `0.0`.
    """
    text = _read_file(PORTFOLIO_HOLDINGS_FILENAME)
    if text is None:
        return []

    lines = text.splitlines()
    holdings: list[dict[str, Any]] = []
    current_account: str | None = None
    idx = 0
    while idx < len(lines):
        heading_match = _H2_HEADING_PATTERN.match(lines[idx])
        if heading_match:
            current_account = _strip_account_parenthetical(heading_match.group("heading"))
            idx += 1
            continue

        if current_account is not None and _is_table_row(lines[idx]):
            _header_cells, data_rows, next_idx = _parse_table_block(lines, idx)
            for cells in data_rows:
                holdings.append(_build_holding(current_account, cells))
            idx = next_idx
            continue

        idx += 1

    return holdings


def get_portfolio_as_of() -> str | None:
    """Return the "as of" text from the H1 title, or None if absent/missing file."""
    text = _read_file(PORTFOLIO_HOLDINGS_FILENAME)
    if text is None:
        return None

    for line in text.splitlines():
        match = _H1_AS_OF_PATTERN.match(line)
        if match:
            return match.group("as_of").strip()

    return None


def get_unique_symbols() -> list[str]:
    """Return unique holding symbols, first-seen order, excluding cash placeholders."""
    seen: dict[str, None] = {}
    for holding in load_portfolio():
        symbol = holding["symbol"]
        if not symbol or symbol.upper() in _NON_TRADABLE_SYMBOLS:
            continue
        seen.setdefault(symbol, None)
    return list(seen.keys())


def load_rsu_schedule() -> list[dict[str, str]]:
    """Load every vesting row across every `## <year> Grant: ...` section.

    Values are kept as raw strings (callers parse dollars/dates themselves)
    per the fixed contract. Each row additionally carries `grant_section`,
    the enclosing `## ` heading text verbatim. Returns `[]` if the file is
    missing or contains no tables.
    """
    text = _read_file(RSU_SCHEDULE_FILENAME)
    if text is None:
        return []

    lines = text.splitlines()
    rows: list[dict[str, str]] = []
    current_section: str | None = None
    idx = 0
    while idx < len(lines):
        heading_match = _H2_HEADING_PATTERN.match(lines[idx])
        if heading_match:
            current_section = heading_match.group("heading").strip()
            idx += 1
            continue

        if current_section is not None and _is_table_row(lines[idx]):
            header_cells, data_rows, next_idx = _parse_table_block(lines, idx)
            keys = [_header_to_key(h) for h in header_cells]
            for cells in data_rows:
                row = _row_from_cells(keys, cells)
                row[_GRANT_SECTION_KEY] = current_section
                rows.append(row)
            idx = next_idx
            continue

        idx += 1

    return rows


def load_watchlist() -> list[dict[str, str]]:
    """Load the single watchlist table as `{symbol, name, notes}` dicts.

    Returns `[]` if the file is missing or contains no table.
    """
    text = _read_file(WATCHLIST_FILENAME)
    if text is None:
        return []

    lines = text.splitlines()
    idx = 0
    while idx < len(lines) and not _is_table_row(lines[idx]):
        idx += 1
    if idx >= len(lines):
        return []

    header_cells, data_rows, _next_idx = _parse_table_block(lines, idx)
    keys = [_header_to_key(h) for h in header_cells]
    return [_row_from_cells(keys, cells) for cells in data_rows]

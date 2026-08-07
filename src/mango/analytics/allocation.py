"""Portfolio asset-class allocation breakdown and concentration flag.

Clean-room implementation written directly from a written specification, not
from any prior allocation module in this codebase family.

Asset-class mapping comes from the user's `etf-classifications.md` under
`PORTFOLIO_DIR` — a markdown pipe table with columns
`Symbol | Name | Asset Class | Region | Sub-Class`. This module does **not**
reimplement a markdown-table parser: `mango.core.portfolio` already owns one
(used for `portfolio-holdings.md`, `rsu-schedule.md`, `watchlist.md`), so the
same private helpers (`_read_file`, `_is_table_row`, `_parse_table_block`,
`_header_to_key`, `_row_from_cells`) are reused here for the classifications
file, exactly mirroring the shape of `portfolio.load_watchlist()`. This is a
DRY reuse of that module's existing table-parsing helper, not a new parser.
"""

from __future__ import annotations

from typing import Any

from mango.core import portfolio
from mango.core.logging import get_logger

log = get_logger("analytics.allocation")

SOURCE = "allocation"

CLASSIFICATIONS_FILENAME = "etf-classifications.md"

# Bucket for any symbol not found in etf-classifications.md — unmapped
# symbols are surfaced, never silently dropped from the breakdown.
UNCLASSIFIED_BUCKET = "Unclassified"
UNKNOWN_BUCKET = "Unknown"  # region / sub-class with no mapping

# A single position exceeding this share of total portfolio market value is
# flagged as a concentration risk. 20% is a common rule-of-thumb single-name
# concentration line (e.g. used informally in diversification guidance);
# there is no regulatory standard for a taxable brokerage account, so this
# is a judgement call — override if a different threshold is wanted.
CONCENTRATION_THRESHOLD_PCT = 20.0

_ROUND_DP = 2


def _load_classifications() -> dict[str, dict[str, str]]:
    """Load `etf-classifications.md` into `{SYMBOL: {asset_class, name, region, sub_class}}`.

    Reuses `mango.core.portfolio`'s private table-parsing helpers (see
    module docstring) rather than writing a second parser. Returns `{}` if
    the file is missing or contains no table — callers treat every symbol as
    `Unclassified` in that case rather than erroring.
    """
    text = portfolio._read_file(CLASSIFICATIONS_FILENAME)
    if text is None:
        return {}

    lines = text.splitlines()
    idx = 0
    while idx < len(lines) and not portfolio._is_table_row(lines[idx]):
        idx += 1
    if idx >= len(lines):
        return {}

    header_cells, data_rows, _next_idx = portfolio._parse_table_block(lines, idx)
    keys = [portfolio._header_to_key(h) for h in header_cells]

    classifications: dict[str, dict[str, str]] = {}
    for cells in data_rows:
        row = portfolio._row_from_cells(keys, cells)
        symbol = row.get("symbol", "").strip().upper()
        if not symbol:
            continue
        classifications[symbol] = row
    return classifications


def _aggregate_market_value_by_symbol(holdings: list[dict[str, Any]]) -> dict[str, float]:
    """Sum market value per symbol across every account, blank symbols dropped."""
    totals: dict[str, float] = {}
    for holding in holdings:
        symbol = (holding.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        market_value = holding.get("market_value", 0.0)
        if not isinstance(market_value, (int, float)):
            continue
        totals[symbol] = totals.get(symbol, 0.0) + float(market_value)
    return totals


def _asset_class_for(symbol: str, classifications: dict[str, dict[str, str]]) -> str:
    row = classifications.get(symbol)
    if not row:
        return UNCLASSIFIED_BUCKET
    asset_class = (row.get("asset_class") or "").strip()
    return asset_class or UNCLASSIFIED_BUCKET


def compute_allocation() -> dict:
    """Break the portfolio down by asset class and flag single-position concentration.

    Reads holdings via `mango.core.portfolio.load_portfolio()` and maps each
    symbol to an asset class via `etf-classifications.md` (see module
    docstring). A symbol with no entry in that file lands in the
    `"Unclassified"` bucket rather than being dropped from the totals.

    Never raises: no holdings, or total portfolio market value of zero,
    returns `{"error": ..., "source": "allocation"}`.
    """
    holdings = portfolio.load_portfolio()
    if not holdings:
        return {"error": "No portfolio holdings found", "source": SOURCE}

    market_value_by_symbol = _aggregate_market_value_by_symbol(holdings)
    total_value = sum(market_value_by_symbol.values())
    if total_value == 0:
        return {"error": "Total portfolio market value is zero", "source": SOURCE}

    classifications = _load_classifications()

    class_totals: dict[str, float] = {}
    unclassified_symbols: list[str] = []
    for symbol, market_value in market_value_by_symbol.items():
        asset_class = _asset_class_for(symbol, classifications)
        if asset_class == UNCLASSIFIED_BUCKET:
            unclassified_symbols.append(symbol)
        class_totals[asset_class] = class_totals.get(asset_class, 0.0) + market_value

    asset_classes = [
        {
            "asset_class": asset_class,
            "market_value": round(value, _ROUND_DP),
            "pct": round(value / total_value * 100, _ROUND_DP),
        }
        for asset_class, value in sorted(class_totals.items(), key=lambda kv: kv[1], reverse=True)
    ]

    # `by_region` and `by_sub_class` mirror `by_asset_class`: a plain
    # {label: market_value} map. The classifications file already carries both
    # columns, so omitting them would drop real breakdowns the report offers.
    def _totals_by(field: str) -> dict[str, float]:
        totals: dict[str, float] = {}
        for symbol, market_value in market_value_by_symbol.items():
            row = classifications.get(symbol) or {}
            # "Unknown" matches the vocabulary already used for region and
            # sub-class in saved output; "Unclassified" is reserved for asset
            # class. Unmapped symbols are still counted, so these totals
            # reconcile to total_value — dropping them would not.
            label = (row.get(field) or "").strip() or UNKNOWN_BUCKET
            totals[label] = totals.get(label, 0.0) + market_value
        return {k: round(v, _ROUND_DP) for k, v in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)}

    holdings_detail = []
    for symbol, market_value in sorted(market_value_by_symbol.items(), key=lambda kv: kv[1], reverse=True):
        row = classifications.get(symbol) or {}
        holdings_detail.append({
            "symbol": symbol,
            "name": (row.get("name") or "").strip(),
            "asset_class": _asset_class_for(symbol, classifications),
            "region": (row.get("region") or "").strip() or UNKNOWN_BUCKET,
            "sub_class": (row.get("sub_class") or "").strip() or UNKNOWN_BUCKET,
            "market_value": round(market_value, _ROUND_DP),
            "weight_pct": round(market_value / total_value * 100, _ROUND_DP),
        })

    largest_symbol, largest_value = max(market_value_by_symbol.items(), key=lambda kv: kv[1])
    largest_pct = round(largest_value / total_value * 100, _ROUND_DP)
    concentration_flag = largest_pct > CONCENTRATION_THRESHOLD_PCT

    return {
        # --- the shape existing consumers read ---
        "total_value": round(total_value, _ROUND_DP),
        "num_holdings": len(market_value_by_symbol),
        "by_asset_class": {k: round(v, _ROUND_DP) for k, v in
                           sorted(class_totals.items(), key=lambda kv: kv[1], reverse=True)},
        "by_region": _totals_by("region"),
        "by_sub_class": _totals_by("sub_class"),
        "holdings": holdings_detail,
        "unclassified": sorted(unclassified_symbols),
        # --- additions ---
        "as_of": portfolio.get_portfolio_as_of(),
        "asset_classes": asset_classes,
        "largest_position": {
            "symbol": largest_symbol,
            "market_value": round(largest_value, _ROUND_DP),
            "pct": largest_pct,
        },
        "concentration_flag": concentration_flag,
        "concentration_threshold_pct": CONCENTRATION_THRESHOLD_PCT,
        "unclassified_symbols": sorted(unclassified_symbols),
        "source": SOURCE,
    }

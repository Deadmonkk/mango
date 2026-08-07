"""Plain-text/ASCII chart rendering for terminal and chat display.

Clean-room implementation written directly from a written specification, not
from any prior charting module in this codebase family. Every public
function renders a deterministic string and never raises: empty input, a
single data point, a zero-range series (all values identical), and NaN/None
entries must all degrade to a readable string (or a short "no data" line)
rather than blow up a caller that is about to print the result straight to
a terminal or chat message.

Inputs that carry OHLCV bars use the shape already produced by
``mango.core.historical.get_historical``::

    {"date": "YYYY-MM-DD", "open": float, "high": float, "low": float,
     "close": float, "volume": int}

ascending by date.
"""

from __future__ import annotations

import math
from typing import Any

# --- Layout constants ---------------------------------------------------

# Default plot height (rows) for the multi-row charts. Matches the public
# `line_chart`/`candlestick_chart` signature defaults.
DEFAULT_HEIGHT = 15

# Maximum number of columns a multi-row chart will render. Series longer
# than this are downsampled (evenly-spaced picks, not aggregated) so the
# output stays a fixed, terminal-friendly width regardless of input size.
MAX_COLUMNS = 60

# Sparkline / comparison-chart row width, deliberately narrower than
# MAX_COLUMNS since these render inline next to a label.
SPARKLINE_WIDTH = 40

# Characters used to draw a point/line grid.
POINT_CHAR = "█"  # "█"
LINE_FILL_CHAR = "│"  # "│"

# Block levels for `sparkline`, lowest to highest — 8 steps per the spec.
SPARK_BLOCKS = "▁▂▃▄▅▆▇█"  # ▁▂▃▄▅▆▇█

# Horizontal-bar width for `heatmap` / `allocation_pie`.
BAR_WIDTH = 30
BAR_FILL_CHAR = "█"  # "█"

# Candlestick body/wick characters.
CANDLE_WICK_CHAR = "│"  # "│"
CANDLE_UP_CHAR = "█"  # "█" — close >= open
CANDLE_DOWN_CHAR = "░"  # "░" — close < open

NO_DATA_MESSAGE = "(no data)"


# --- Shared numeric helpers ----------------------------------------------


def _is_valid_number(value: Any) -> bool:
    """True for a real, finite number — the only values safe to plot."""
    if value is None or isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return not (isinstance(value, float) and (math.isnan(value) or math.isinf(value)))


def _clean_points(labels: list[Any], values: list[Any]) -> tuple[list[str], list[float]]:
    """Pair `labels` with `values`, dropping any row with a non-plottable value.

    Keeps label/value alignment intact — a dropped value drops its label too.
    """
    clean_labels: list[str] = []
    clean_values: list[float] = []
    for label, value in zip(labels, values):
        if not _is_valid_number(value):
            continue
        clean_labels.append(str(label))
        clean_values.append(float(value))
    return clean_labels, clean_values


def _downsample(labels: list[str], values: list[float], max_len: int) -> tuple[list[str], list[float]]:
    """Evenly-spaced downsample to at most `max_len` points.

    Picks existing points rather than averaging — a chart is a visual guide,
    not a data reduction, so no synthetic values are invented.
    """
    n = len(values)
    if n <= max_len or max_len <= 0:
        return labels, values
    step = n / max_len
    idxs = sorted({int(i * step) for i in range(max_len)})
    return [labels[i] for i in idxs], [values[i] for i in idxs]


def _normalize_to_rows(values: list[float], height: int) -> list[int]:
    """Map each value onto a plot row in [0, height-1]; row 0 is the top (highest value)."""
    lo, hi = min(values), max(values)
    span = hi - lo
    if span == 0:
        mid = max(height // 2, 0)
        return [mid for _ in values]
    return [round((height - 1) * (1 - (v - lo) / span)) for v in values]


def _render_point_grid(rows: list[int], height: int) -> list[str]:
    """Render a connected point plot: `rows[i]` is the row index for column i."""
    width = len(rows)
    grid = [[" "] * width for _ in range(height)]
    for i, row in enumerate(rows):
        if i > 0:
            prev = rows[i - 1]
            top, bottom = min(prev, row), max(prev, row)
            for y in range(top, bottom + 1):
                grid[y][i] = LINE_FILL_CHAR
        grid[row][i] = POINT_CHAR
    return ["".join(line) for line in grid]


def _header(title: str) -> list[str]:
    return [title] if title else []


def _no_data(title: str) -> str:
    lines = _header(title)
    lines.append(NO_DATA_MESSAGE)
    return "\n".join(lines)


def _fmt(value: float) -> str:
    return f"{value:,.2f}"


# --- Public: series-style charts -------------------------------------------


def _series_chart(
    labels: list[str],
    values: list[float],
    title: str,
    height: int,
    value_fmt: str = "{:,.2f}",
) -> str:
    """Shared renderer for a labelled-value line chart (dates, tenors, ...)."""
    if not values:
        return _no_data(title)

    plot_height = max(height, 1)
    plot_labels, plot_values = _downsample(labels, values, MAX_COLUMNS)
    rows = _normalize_to_rows(plot_values, plot_height)
    grid_lines = _render_point_grid(rows, plot_height)

    hi, lo = max(plot_values), min(plot_values)
    out = _header(title)
    out.append(f"high: {value_fmt.format(hi)}")
    out.extend(grid_lines)
    out.append(f"low:  {value_fmt.format(lo)}")
    out.append(f"{plot_labels[0]}  ...  {plot_labels[-1]}")
    return "\n".join(out)


def line_chart(series: list[dict], title: str = "", height: int = DEFAULT_HEIGHT) -> str:
    """Render an OHLCV-shaped series' closing price as an ASCII line chart."""
    if not series:
        return _no_data(title)
    dates = [row.get("date") for row in series]
    closes = [row.get("close") for row in series]
    labels, values = _clean_points(dates, closes)
    return _series_chart(labels, values, title, height)


def yield_curve_chart(points: list[tuple[str, float]], title: str = "") -> str:
    """Render tenor/yield pairs (e.g. `[("3M", 5.3), ("10Y", 4.2)]`) as an ASCII chart."""
    if not points:
        return _no_data(title)
    tenors = [p[0] if len(p) > 0 else None for p in points]
    yields = [p[1] if len(p) > 1 else None for p in points]
    labels, values = _clean_points(tenors, yields)
    return _series_chart(labels, values, title, DEFAULT_HEIGHT, value_fmt="{:,.2f}%")


def comparison_chart(series_by_symbol: dict[str, list[dict]], title: str = "") -> str:
    """Render each symbol's % return from its own first close as one sparkline row.

    Percent-return-from-first-point (not raw price) is what makes differently
    priced symbols visually comparable on one shared scale.
    """
    if not series_by_symbol:
        return _no_data(title)

    out = _header(title)
    name_width = max((len(sym) for sym in series_by_symbol), default=0)
    for symbol, series in series_by_symbol.items():
        closes = [row.get("close") for row in (series or [])]
        _labels, values = _clean_points(range(len(closes)), closes)
        if not values or values[0] == 0:
            out.append(f"{symbol.ljust(name_width)}  {NO_DATA_MESSAGE}")
            continue
        base = values[0]
        pct_returns = [(v - base) / base * 100 for v in values]
        _, plotted = _downsample([""] * len(pct_returns), pct_returns, SPARKLINE_WIDTH)
        spark = sparkline(plotted)
        out.append(f"{symbol.ljust(name_width)}  {spark}  {pct_returns[-1]:+.1f}%")

    return "\n".join(out)


# --- Public: bar-style charts -----------------------------------------------


def _proportional_bars(rows: list[tuple[str, float]], title: str, denominator: float | None = None) -> str:
    """Shared renderer for `heatmap`/`allocation_pie`: sorted, proportional bars."""
    labels = [r[0] if len(r) > 0 else None for r in rows]
    values = [r[1] if len(r) > 1 else None for r in rows]
    clean_labels, clean_values = _clean_points(labels, values)
    if not clean_values:
        return _no_data(title)

    paired = sorted(zip(clean_labels, clean_values), key=lambda p: p[1], reverse=True)
    total = denominator if denominator is not None else sum(abs(v) for _label, v in paired)
    max_abs = max(abs(v) for _label, v in paired) or 1.0
    name_width = max(len(label) for label, _v in paired)

    out = _header(title)
    for label, value in paired:
        bar_len = round((abs(value) / max_abs) * BAR_WIDTH)
        bar = BAR_FILL_CHAR * bar_len
        pct = f"  ({value / total * 100:.1f}%)" if total else ""
        out.append(f"{label.ljust(name_width)}  {bar.ljust(BAR_WIDTH)}  {_fmt(value)}{pct}")
    return "\n".join(out)


def heatmap(rows: list[tuple[str, float]], title: str = "") -> str:
    """Render `(label, value)` rows as sorted, proportional horizontal bars."""
    if not rows:
        return _no_data(title)
    return _proportional_bars(rows, title)


def allocation_pie(slices: list[tuple[str, float]], title: str = "") -> str:
    """Render `(label, value)` allocation slices as sorted, proportional bars.

    A true pie chart cannot be drawn in monospace text; a proportional bar
    ranked by size conveys the same "share of whole" information without a
    misleading ASCII circle.
    """
    if not slices:
        return _no_data(title)
    return _proportional_bars(slices, title)


def sparkline(values: list[float]) -> str:
    """Render `values` as a single line of block characters (▁▂▃▄▅▆▇█)."""
    _labels, clean_values = _clean_points(range(len(values)), values)
    if not clean_values:
        return NO_DATA_MESSAGE

    lo, hi = min(clean_values), max(clean_values)
    span = hi - lo
    n_levels = len(SPARK_BLOCKS)
    if span == 0:
        mid_char = SPARK_BLOCKS[n_levels // 2]
        return mid_char * len(clean_values)

    chars = []
    for v in clean_values:
        level = round((v - lo) / span * (n_levels - 1))
        chars.append(SPARK_BLOCKS[level])
    return "".join(chars)


# --- Public: candlestick chart ----------------------------------------------


def candlestick_chart(bars: list[dict], title: str = "", height: int = DEFAULT_HEIGHT) -> str:
    """Render OHLCV bars as an ASCII candlestick chart: wick = high/low, body = open/close."""
    if not bars:
        return _no_data(title)

    clean_bars: list[dict[str, float]] = []
    for bar in bars:
        o, h, l, c = bar.get("open"), bar.get("high"), bar.get("low"), bar.get("close")
        if not all(_is_valid_number(v) for v in (o, h, l, c)):
            continue
        clean_bars.append({"open": float(o), "high": float(h), "low": float(l), "close": float(c)})

    if not clean_bars:
        return _no_data(title)

    if len(clean_bars) > MAX_COLUMNS:
        step = len(clean_bars) / MAX_COLUMNS
        idxs = sorted({int(i * step) for i in range(MAX_COLUMNS)})
        clean_bars = [clean_bars[i] for i in idxs]

    plot_height = max(height, 1)
    all_highs = [b["high"] for b in clean_bars]
    all_lows = [b["low"] for b in clean_bars]
    lo, hi = min(all_lows), max(all_highs)
    span = hi - lo

    def _row_for(value: float) -> int:
        if span == 0:
            return plot_height // 2
        return round((plot_height - 1) * (1 - (value - lo) / span))

    width = len(clean_bars)
    grid = [[" "] * width for _ in range(plot_height)]
    for i, bar in enumerate(clean_bars):
        wick_top = _row_for(bar["high"])
        wick_bottom = _row_for(bar["low"])
        for y in range(wick_top, wick_bottom + 1):
            grid[y][i] = CANDLE_WICK_CHAR

        body_top = _row_for(max(bar["open"], bar["close"]))
        body_bottom = _row_for(min(bar["open"], bar["close"]))
        body_char = CANDLE_UP_CHAR if bar["close"] >= bar["open"] else CANDLE_DOWN_CHAR
        for y in range(body_top, body_bottom + 1):
            grid[y][i] = body_char

    out = _header(title)
    out.append(f"high: {_fmt(hi)}")
    out.extend("".join(row) for row in grid)
    out.append(f"low:  {_fmt(lo)}")
    return "\n".join(out)

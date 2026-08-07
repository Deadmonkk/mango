"""Tests for mango.charts — ASCII/plain-text chart rendering.

All charts render strings; nothing here touches the network or the
filesystem. Fixtures use made-up OHLCV rows with round numbers.
"""

from __future__ import annotations

import math

from mango import charts

NAN = float("nan")


def _bar(date: str, open_: float, high: float, low: float, close: float) -> dict:
    return {"date": date, "open": open_, "high": high, "low": low, "close": close, "volume": 1000}


# --- line_chart ---------------------------------------------------------


def test_line_chart_renders_title_and_high_low_labels() -> None:
    # Arrange
    series = [_bar(f"2026-01-{i + 1:02d}", 10, 10, 10, 10 + i) for i in range(5)]

    # Act
    result = charts.line_chart(series, title="Test Series")

    # Assert
    assert "Test Series" in result
    assert "high: 14.00" in result
    assert "low:  10.00" in result
    assert "2026-01-01" in result and "2026-01-05" in result


def test_line_chart_returns_no_data_message_for_empty_input() -> None:
    # Arrange / Act
    result = charts.line_chart([], title="Empty")

    # Assert
    assert "Empty" in result
    assert charts.NO_DATA_MESSAGE in result


def test_line_chart_handles_single_data_point_without_raising() -> None:
    # Arrange
    series = [_bar("2026-01-01", 5, 5, 5, 5)]

    # Act
    result = charts.line_chart(series)

    # Assert
    assert "high: 5.00" in result
    assert "low:  5.00" in result


def test_line_chart_handles_zero_range_without_division_by_zero() -> None:
    # Arrange — every close is identical, so naive (v-lo)/(hi-lo) would divide by zero
    series = [_bar(f"2026-01-{i + 1:02d}", 7, 7, 7, 7) for i in range(4)]

    # Act
    result = charts.line_chart(series)

    # Assert
    assert "high: 7.00" in result
    assert "low:  7.00" in result


def test_line_chart_drops_nan_and_none_closes() -> None:
    # Arrange
    series = [
        _bar("2026-01-01", 1, 1, 1, 10.0),
        {"date": "2026-01-02", "open": 1, "high": 1, "low": 1, "close": NAN, "volume": 1},
        {"date": "2026-01-03", "open": 1, "high": 1, "low": 1, "close": None, "volume": 1},
        _bar("2026-01-04", 1, 1, 1, 20.0),
    ]

    # Act
    result = charts.line_chart(series)

    # Assert — only the two valid closes (10, 20) should drive the range
    assert "high: 20.00" in result
    assert "low:  10.00" in result


def test_line_chart_is_deterministic() -> None:
    # Arrange
    series = [_bar(f"2026-01-{i + 1:02d}", 1, 1, 1, 100 + i * 3.3) for i in range(10)]

    # Act
    first = charts.line_chart(series, title="Determinism")
    second = charts.line_chart(series, title="Determinism")

    # Assert
    assert first == second


# --- candlestick_chart ---------------------------------------------------


def test_candlestick_chart_renders_high_low_labels() -> None:
    # Arrange
    bars = [
        _bar("2026-01-01", open_=10, high=12, low=9, close=11),
        _bar("2026-01-02", open_=11, high=15, low=10, close=10.5),
        _bar("2026-01-03", open_=10.5, high=11, low=8, close=9),
    ]

    # Act
    result = charts.candlestick_chart(bars, title="Candles")

    # Assert
    assert "Candles" in result
    assert "high: 15.00" in result
    assert "low:  8.00" in result


def test_candlestick_chart_returns_no_data_message_for_empty_input() -> None:
    # Act
    result = charts.candlestick_chart([])

    # Assert
    assert charts.NO_DATA_MESSAGE in result


def test_candlestick_chart_handles_single_bar_without_raising() -> None:
    # Arrange
    bars = [_bar("2026-01-01", open_=10, high=11, low=9, close=10.5)]

    # Act
    result = charts.candlestick_chart(bars)

    # Assert
    assert "high: 11.00" in result
    assert "low:  9.00" in result


def test_candlestick_chart_handles_zero_range_ohlc_without_raising() -> None:
    # Arrange — a completely flat bar (open=high=low=close) on every day
    bars = [_bar(f"2026-01-{i + 1:02d}", 5, 5, 5, 5) for i in range(3)]

    # Act
    result = charts.candlestick_chart(bars)

    # Assert
    assert "high: 5.00" in result
    assert "low:  5.00" in result


def test_candlestick_chart_drops_bars_with_nan_or_none_fields() -> None:
    # Arrange
    bars = [
        _bar("2026-01-01", open_=10, high=11, low=9, close=10),
        {"date": "2026-01-02", "open": 10, "high": NAN, "low": 9, "close": 10, "volume": 1},
        {"date": "2026-01-03", "open": None, "high": 11, "low": 9, "close": 10, "volume": 1},
        _bar("2026-01-04", open_=10, high=20, low=1, close=15),
    ]

    # Act
    result = charts.candlestick_chart(bars)

    # Assert — the two malformed bars are dropped, so range comes from the two valid ones
    assert "high: 20.00" in result
    assert "low:  1.00" in result


def test_candlestick_chart_is_deterministic() -> None:
    # Arrange
    bars = [_bar(f"2026-01-{i + 1:02d}", 10 + i, 12 + i, 9 + i, 11 + i) for i in range(8)]

    # Act
    first = charts.candlestick_chart(bars, title="Determinism")
    second = charts.candlestick_chart(bars, title="Determinism")

    # Assert
    assert first == second


# --- comparison_chart -----------------------------------------------------


def test_comparison_chart_plots_percent_return_from_first_point() -> None:
    # Arrange — AAA doubles (+100%), BBB halves (-50%); differently priced but comparable
    series_by_symbol = {
        "AAA": [_bar(f"2026-01-{i + 1:02d}", 1, 1, 1, c) for i, c in enumerate([10, 15, 20])],
        "BBB": [_bar(f"2026-01-{i + 1:02d}", 1, 1, 1, c) for i, c in enumerate([1000, 750, 500])],
    }

    # Act
    result = charts.comparison_chart(series_by_symbol, title="Cmp")

    # Assert
    assert "Cmp" in result
    assert "+100.0%" in result
    assert "-50.0%" in result


def test_comparison_chart_returns_no_data_message_for_empty_input() -> None:
    # Act
    result = charts.comparison_chart({})

    # Assert
    assert charts.NO_DATA_MESSAGE in result


def test_comparison_chart_handles_symbol_with_no_valid_closes() -> None:
    # Arrange
    series_by_symbol = {"ZZZ": []}

    # Act
    result = charts.comparison_chart(series_by_symbol)

    # Assert
    assert "ZZZ" in result
    assert charts.NO_DATA_MESSAGE in result


def test_comparison_chart_is_deterministic() -> None:
    # Arrange
    series_by_symbol = {
        "AAA": [_bar(f"2026-01-{i + 1:02d}", 1, 1, 1, 10 + i) for i in range(5)],
        "BBB": [_bar(f"2026-01-{i + 1:02d}", 1, 1, 1, 20 - i) for i in range(5)],
    }

    # Act
    first = charts.comparison_chart(series_by_symbol, title="Determinism")
    second = charts.comparison_chart(series_by_symbol, title="Determinism")

    # Assert
    assert first == second


# --- yield_curve_chart -----------------------------------------------------


def test_yield_curve_chart_renders_tenors_and_yields() -> None:
    # Arrange
    points = [("3M", 5.3), ("2Y", 4.5), ("10Y", 4.2), ("30Y", 4.4)]

    # Act
    result = charts.yield_curve_chart(points, title="Curve")

    # Assert
    assert "Curve" in result
    assert "high: 5.30%" in result
    assert "low:  4.20%" in result
    assert "3M" in result and "30Y" in result


def test_yield_curve_chart_returns_no_data_message_for_empty_input() -> None:
    # Act
    result = charts.yield_curve_chart([])

    # Assert
    assert charts.NO_DATA_MESSAGE in result


def test_yield_curve_chart_drops_none_yields() -> None:
    # Arrange
    points = [("3M", 5.0), ("2Y", None), ("10Y", 4.0)]

    # Act
    result = charts.yield_curve_chart(points)

    # Assert
    assert "high: 5.00%" in result
    assert "low:  4.00%" in result


# --- heatmap ---------------------------------------------------------------


def test_heatmap_sorts_descending_by_value_and_labels_values() -> None:
    # Arrange
    rows = [("Energy", 2.5), ("Tech", -3.1), ("Utilities", 0.4)]

    # Act
    result = charts.heatmap(rows, title="Sectors")

    # Assert
    lines = [line for line in result.splitlines() if line and line != "Sectors"]
    assert lines[0].startswith("Energy")  # highest value first
    assert lines[-1].startswith("Tech")  # lowest (most negative) value last
    assert "2.50" in lines[0]


def test_heatmap_returns_no_data_message_for_empty_input() -> None:
    # Act
    result = charts.heatmap([])

    # Assert
    assert charts.NO_DATA_MESSAGE in result


def test_heatmap_drops_nan_rows_without_raising() -> None:
    # Arrange
    rows = [("A", 1.0), ("B", NAN), ("C", 3.0)]

    # Act
    result = charts.heatmap(rows)

    # Assert
    assert "B" not in result
    assert "A" in result and "C" in result


# --- allocation_pie ----------------------------------------------------------


def test_allocation_pie_sorts_descending_and_shows_percent_of_total() -> None:
    # Arrange — total = 100, so percentages are easy to hand-check
    slices = [("Bonds", 25.0), ("Equities", 60.0), ("Cash", 15.0)]

    # Act
    result = charts.allocation_pie(slices, title="Allocation")

    # Assert
    lines = [line for line in result.splitlines() if line and line != "Allocation"]
    assert lines[0].startswith("Equities")
    assert "(60.0%)" in lines[0]
    assert lines[-1].startswith("Cash")
    assert "(15.0%)" in lines[-1]


def test_allocation_pie_returns_no_data_message_for_empty_input() -> None:
    # Act
    result = charts.allocation_pie([])

    # Assert
    assert charts.NO_DATA_MESSAGE in result


def test_allocation_pie_handles_single_slice() -> None:
    # Arrange
    slices = [("Everything", 100.0)]

    # Act
    result = charts.allocation_pie(slices)

    # Assert
    assert "Everything" in result
    assert "(100.0%)" in result


# --- sparkline ---------------------------------------------------------------


def test_sparkline_uses_block_characters_and_spans_full_range() -> None:
    # Arrange
    values = [1, 2, 3, 4, 5, 6, 7, 8]

    # Act
    result = charts.sparkline(values)

    # Assert
    assert result[0] == charts.SPARK_BLOCKS[0]
    assert result[-1] == charts.SPARK_BLOCKS[-1]
    assert all(c in charts.SPARK_BLOCKS for c in result)


def test_sparkline_returns_no_data_message_for_empty_input() -> None:
    # Act
    result = charts.sparkline([])

    # Assert
    assert result == charts.NO_DATA_MESSAGE


def test_sparkline_handles_zero_range_without_division_by_zero() -> None:
    # Arrange — every value identical
    values = [4.0, 4.0, 4.0, 4.0]

    # Act
    result = charts.sparkline(values)

    # Assert
    assert len(result) == 4
    assert len(set(result)) == 1  # every char the same mid-level block


def test_sparkline_drops_nan_and_none_entries() -> None:
    # Arrange
    values = [1.0, NAN, None, 5.0]

    # Act
    result = charts.sparkline(values)

    # Assert
    assert len(result) == 2  # only the two valid values plotted


def test_sparkline_is_deterministic() -> None:
    # Arrange
    values = [3, 1, 4, 1, 5, 9, 2, 6]

    # Act
    first = charts.sparkline(values)
    second = charts.sparkline(values)

    # Assert
    assert first == second


def test_sparkline_handles_single_value() -> None:
    # Act
    result = charts.sparkline([42.0])

    # Assert
    assert len(result) == 1
    assert result in charts.SPARK_BLOCKS


def test_nan_helper_treats_infinity_as_invalid() -> None:
    # Arrange / Act / Assert — infinity must never reach a division or a grid index
    assert charts._is_valid_number(math.inf) is False
    assert charts._is_valid_number(-math.inf) is False
    assert charts._is_valid_number(1.5) is True
    assert charts._is_valid_number(None) is False

"""Tests for mango.providers.technical — the technical-indicator provider.

All network access is faked by monkeypatching `technical.get_historical`
directly; no test may touch the network or yfinance. Each indicator is
checked against an independent reference implementation written in this
file (not the provider's own internals), following the same textbook
formulas the provider claims to use. AAA structure throughout.
"""

from __future__ import annotations

import statistics

import pytest

from mango.providers import technical


# --- fixtures --------------------------------------------------------------


def _make_prices(count: int, *, start: float = 100.0, step: float = 1.3) -> list[dict]:
    """`count` ascending OHLCV rows with steadily rising closes and a small
    high/low spread around each close, so ATR has real (non-zero) true
    ranges to work with.
    """
    rows = []
    for i in range(count):
        close = start + i * step
        rows.append(
            {
                "date": f"2026-01-{(i % 28) + 1:02d}",
                "open": close - 0.2,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 1_000_000,
            }
        )
    return rows


def _async_return(value):
    """Build an async callable that ignores its args and returns `value`.

    Used to monkeypatch `technical.get_historical` (an async function) with
    a fixed fixture, without touching the network.
    """

    async def _fn(*args, **kwargs):
        return value

    return _fn


def _history_result(prices: list[dict], symbol: str = "TEST") -> dict:
    return {
        "symbol": symbol,
        "period": "1y",
        "interval": "1d",
        "prices": prices,
        "count": len(prices),
        "source": "yahoo_finance",
    }


# --- independent reference implementations (hand-computable formulas) -----


def _ref_sma(closes: list[float], window: int) -> float | None:
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def _ref_ema_series(closes: list[float], window: int) -> list[float] | None:
    if len(closes) < window:
        return None
    k = 2.0 / (window + 1)
    series = [sum(closes[:window]) / window]
    for c in closes[window:]:
        series.append((c - series[-1]) * k + series[-1])
    return series


def _ref_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(c, 0.0) for c in changes]
    losses = [max(-c, 0.0) for c in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _ref_bollinger(closes: list[float], price: float, period: int = 20, mult: int = 2):
    window = closes[-period:]
    middle = sum(window) / period
    stddev = statistics.pstdev(window)
    upper = middle + mult * stddev
    lower = middle - mult * stddev
    percent_b = (price - lower) / (upper - lower)
    return middle, upper, lower, percent_b


def _ref_atr(highs, lows, closes, period: int = 14) -> float:
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


# --- tests -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_sma_matches_hand_computed_average(monkeypatch):
    # Arrange: 40 rising bars — enough for SMA-20, not enough for SMA-50/200.
    prices = _make_prices(40)
    closes = [p["close"] for p in prices]
    monkeypatch.setattr(technical, "get_historical", _async_return(_history_result(prices)))

    # Act
    result = await technical.get_full_technicals("TEST")

    # Assert
    expected_sma_20 = round(_ref_sma(closes, 20), 2)
    assert result["sma"]["sma_20"] == expected_sma_20
    assert result["sma"]["current_price"] == round(closes[-1], 2)


@pytest.mark.asyncio
async def test_sma_yields_none_for_windows_exceeding_available_history(monkeypatch):
    # Arrange: only 40 bars — SMA-50 and SMA-200 cannot be computed honestly.
    prices = _make_prices(40)
    monkeypatch.setattr(technical, "get_historical", _async_return(_history_result(prices)))

    # Act
    result = await technical.get_full_technicals("TEST")

    # Assert
    assert result["sma"]["sma_50"] is None
    assert result["sma"]["sma_200"] is None
    assert result["sma"]["signals"]["above_sma_50"] is None
    assert result["sma"]["signals"]["above_sma_200"] is None
    assert result["sma"]["signals"]["golden_cross"] is None


@pytest.mark.asyncio
async def test_ema_matches_hand_computed_series(monkeypatch):
    # Arrange: 60 bars, comfortably covers EMA-12/26/50.
    prices = _make_prices(60)
    closes = [p["close"] for p in prices]
    monkeypatch.setattr(technical, "get_historical", _async_return(_history_result(prices)))

    # Act
    result = await technical.get_full_technicals("TEST")

    # Assert
    for window in (12, 26, 50):
        expected = round(_ref_ema_series(closes, window)[-1], 2)
        assert result["ema"][f"ema_{window}"] == expected


@pytest.mark.asyncio
async def test_rsi_is_100_when_every_change_is_a_gain(monkeypatch):
    # Arrange: strictly increasing closes -> every daily change is a gain,
    # zero losses -> RSI must be exactly 100 by the Wilder formula.
    prices = _make_prices(30, step=1.0)
    monkeypatch.setattr(technical, "get_historical", _async_return(_history_result(prices)))

    # Act
    result = await technical.get_full_technicals("TEST")

    # Assert
    assert result["rsi"]["rsi"] == 100.0
    assert result["rsi"]["signal"] == "overbought"
    assert result["rsi"]["period"] == 14


@pytest.mark.asyncio
async def test_rsi_yields_none_with_fewer_than_period_plus_one_bars(monkeypatch):
    # Arrange: 14 bars gives only 13 price changes — one short of the 14
    # required for a real 14-period RSI.
    prices = _make_prices(14)
    monkeypatch.setattr(technical, "get_historical", _async_return(_history_result(prices)))

    # Act
    result = await technical.get_full_technicals("TEST")

    # Assert
    assert result["rsi"]["rsi"] is None
    assert result["rsi"]["signal"] is None


@pytest.mark.asyncio
async def test_macd_histogram_matches_hand_computed_value(monkeypatch):
    # Arrange
    prices = _make_prices(60)
    closes = [p["close"] for p in prices]
    monkeypatch.setattr(technical, "get_historical", _async_return(_history_result(prices)))

    fast = _ref_ema_series(closes, 12)
    slow = _ref_ema_series(closes, 26)
    offset = len(fast) - len(slow)
    macd_series = [fast[offset + i] - slow[i] for i in range(len(slow))]
    signal_series = _ref_ema_series(macd_series, 9)
    expected_macd = round(macd_series[-1], 4)
    expected_signal = round(signal_series[-1], 4)
    expected_hist = round(macd_series[-1] - signal_series[-1], 4)

    # Act
    result = await technical.get_full_technicals("TEST")

    # Assert
    assert result["macd"]["macd_line"] == expected_macd
    assert result["macd"]["signal_line"] == expected_signal
    assert result["macd"]["histogram"] == expected_hist
    assert result["macd"]["signal"] in ("bullish", "bearish", "neutral")
    assert result["macd"]["parameters"] == {"fast": 12, "slow": 26, "signal": 9}


@pytest.mark.asyncio
async def test_bollinger_bands_match_hand_computed_values(monkeypatch):
    # Arrange
    prices = _make_prices(25)
    closes = [p["close"] for p in prices]
    monkeypatch.setattr(technical, "get_historical", _async_return(_history_result(prices)))
    price = closes[-1]
    expected_middle, expected_upper, expected_lower, expected_percent_b = _ref_bollinger(closes, price)

    # Act
    result = await technical.get_full_technicals("TEST")

    # Assert
    bb = result["bollinger"]
    assert bb["middle_band"] == round(expected_middle, 2)
    assert bb["upper_band"] == round(expected_upper, 2)
    assert bb["lower_band"] == round(expected_lower, 2)
    assert bb["percent_b"] == round(expected_percent_b, 4)
    assert bb["bandwidth"] == round(expected_upper - expected_lower, 2)


@pytest.mark.asyncio
async def test_bollinger_yields_none_with_fewer_than_20_bars(monkeypatch):
    # Arrange
    prices = _make_prices(10)
    monkeypatch.setattr(technical, "get_historical", _async_return(_history_result(prices)))

    # Act
    result = await technical.get_full_technicals("TEST")

    # Assert
    bb = result["bollinger"]
    assert bb["upper_band"] is None
    assert bb["lower_band"] is None
    assert bb["percent_b"] is None
    assert bb["signal"] is None


@pytest.mark.asyncio
async def test_atr_matches_hand_computed_value(monkeypatch):
    # Arrange
    prices = _make_prices(30)
    highs = [p["high"] for p in prices]
    lows = [p["low"] for p in prices]
    closes = [p["close"] for p in prices]
    monkeypatch.setattr(technical, "get_historical", _async_return(_history_result(prices)))
    expected_atr = round(_ref_atr(highs, lows, closes), 4)

    # Act
    result = await technical.get_full_technicals("TEST")

    # Assert
    assert result["atr"]["atr"] == expected_atr
    assert result["atr"]["period"] == 14


@pytest.mark.asyncio
async def test_atr_yields_none_with_fewer_than_period_bars(monkeypatch):
    # Arrange
    prices = _make_prices(10)
    monkeypatch.setattr(technical, "get_historical", _async_return(_history_result(prices)))

    # Act
    result = await technical.get_full_technicals("TEST")

    # Assert
    assert result["atr"]["atr"] is None


@pytest.mark.asyncio
async def test_output_shape_matches_the_fixed_contract(monkeypatch):
    # Arrange: enough history for every indicator to have a real value.
    prices = _make_prices(210)
    monkeypatch.setattr(technical, "get_historical", _async_return(_history_result(prices)))

    # Act
    result = await technical.get_full_technicals("SPY")

    # Assert: top-level keys
    assert set(result.keys()) == {
        "symbol", "price", "source", "overall_signal",
        "sma", "ema", "rsi", "macd", "bollinger", "atr",
    }
    assert result["symbol"] == "SPY"
    assert result["source"] == "computed from yahoo_finance data"
    assert result["overall_signal"] in ("bullish", "bearish", "neutral")

    # Assert: nested shapes
    assert set(result["sma"].keys()) == {"current_price", "sma_20", "sma_50", "sma_200", "signals"}
    assert set(result["sma"]["signals"].keys()) == {
        "above_sma_20", "above_sma_50", "above_sma_200", "golden_cross",
    }
    assert set(result["ema"].keys()) == {"ema_12", "ema_26", "ema_50"}
    assert set(result["rsi"].keys()) == {"rsi", "period", "signal"}
    assert set(result["macd"].keys()) == {"macd_line", "signal_line", "histogram", "signal", "parameters"}
    assert set(result["macd"]["parameters"].keys()) == {"fast", "slow", "signal"}
    assert set(result["bollinger"].keys()) == {
        "current_price", "upper_band", "middle_band", "lower_band", "bandwidth", "percent_b", "signal",
    }
    assert set(result["atr"].keys()) == {"atr", "period"}
    # All indicators had enough history in this fixture, so nothing is None.
    assert result["sma"]["sma_200"] is not None
    assert result["macd"]["macd_line"] is not None


@pytest.mark.asyncio
async def test_returns_error_dict_when_historical_fetch_fails(monkeypatch):
    # Arrange
    monkeypatch.setattr(
        technical,
        "get_historical",
        _async_return({"error": "Yahoo Finance fetch failed", "symbol": "BADSYM", "source": "yahoo_finance"}),
    )

    # Act
    result = await technical.get_full_technicals("BADSYM")

    # Assert
    assert "error" in result
    assert result["symbol"] == "BADSYM"
    assert result["source"] == technical.SOURCE


@pytest.mark.asyncio
async def test_returns_error_dict_when_price_history_is_empty(monkeypatch):
    # Arrange
    monkeypatch.setattr(technical, "get_historical", _async_return(_history_result([])))

    # Act
    result = await technical.get_full_technicals("EMPTY")

    # Assert
    assert "error" in result
    assert result["symbol"] == "EMPTY"

"""Market data provider — yfinance-based market overview, international markets, style box, asset classes, stock fundamentals, fed funds futures, and equity sentiment."""

import asyncio
from datetime import datetime, timezone

from mango.core.logging import log

from mango.core import cache
from mango._lazy_yfinance import yfinance
from mango.ext_settings import (
    CACHE_TTL_EQUITY_SENTIMENT,
    CACHE_TTL_FED_PATH,
    CACHE_TTL_FUNDAMENTALS,
    CACHE_TTL_HISTORY,
    FED_PATH_MONTHS_AHEAD,
    FED_PATH_SIGNAL_THRESHOLD_BP,
    SKEW_ELEVATED_THRESHOLD,
    VIX_ELEVATED_THRESHOLD,
    VIX_HIGH_THRESHOLD,
    VIX_LOW_THRESHOLD,
    VIX_TERM_BACKWARDATION_RATIO,
    VIX_TERM_COMPLACENCY_RATIO,
)

# ---------------------------------------------------------------------------
# Ticker registries
# ---------------------------------------------------------------------------

_MARKET_OVERVIEW_TICKERS: dict[str, str] = {
    "^GSPC": "S&P 500",
    "^DJI": "Dow Jones",
    "^IXIC": "Nasdaq Composite",
    "^RUT": "Russell 2000",
    "^VIX": "CBOE VIX",
    "DX-Y.NYB": "US Dollar (DXY)",
    "GC=F": "Gold",
    "CL=F": "WTI Crude Oil",
}

_INTERNATIONAL_TICKERS: dict[str, str] = {
    "EFA": "MSCI EAFE (Dev. ex-US)",
    "VWO": "MSCI Emerging Markets",
    "VGK": "Europe",
    "EWJ": "Japan",
    "EWG": "Germany",
    "EWU": "United Kingdom",
    "FXI": "China Large-Cap",
    "EWZ": "Brazil",
    "EWY": "South Korea",
    "INDA": "India",
}

_STYLE_BOX_TICKERS: dict[str, str] = {
    "IVW": "Large Growth",
    "IVE": "Large Value",
    "IWB": "Large Blend",
    "IJK": "Mid Growth",
    "IJJ": "Mid Value",
    "IWR": "Mid Blend",
    "IWO": "Small Growth",
    "IWN": "Small Value",
    "IWM": "Small Blend",
}

_ASSET_CLASS_TICKERS: dict[str, str] = {
    "SPY": "US Large Cap Equity",
    "IWM": "US Small Cap Equity",
    "EFA": "Int'l Developed Equity",
    "VWO": "Emerging Markets Equity",
    "AGG": "US Bonds (Aggregate)",
    "LQD": "Investment Grade Corp",
    "HYG": "High Yield Corp",
    "TLT": "Long Treasury (20yr+)",
    "TIP": "TIPS (Inflation-Protected)",
    "VNQ": "US REITs",
    "GLD": "Gold",
    "GSG": "Commodities",
    "BNDX": "Int'l Bonds",
}

# ---------------------------------------------------------------------------
# Shared fetch helpers
# ---------------------------------------------------------------------------


async def _fetch_weekly_performance(symbol: str) -> dict:
    """Fetch weekly OHLCV for 1 year and compute performance metrics."""
    ticker = yfinance.Ticker(symbol)
    try:
        hist = await asyncio.to_thread(ticker.history, period="1y", interval="1wk")
    except Exception as e:
        log.warning("yfinance weekly fetch failed for %s: %s", symbol, e)
        return {"error": str(e)}

    if hist.empty:
        return {"error": "no data"}

    closes = hist["Close"].dropna()
    if len(closes) < 2:
        return {"error": "insufficient data"}

    current = round(float(closes.iloc[-1]), 4)

    # YTD return — first week of the current calendar year
    current_year = closes.index[-1].year
    ytd_closes = closes[closes.index.year == current_year]
    ytd_start = float(ytd_closes.iloc[0]) if not ytd_closes.empty else float(closes.iloc[0])
    ytd_return = round((current / ytd_start - 1) * 100, 2) if ytd_start else None

    yr_return = round((current / float(closes.iloc[0]) - 1) * 100, 2)

    highs = hist["High"].dropna()
    lows = hist["Low"].dropna()

    return {
        "current": current,
        "ytd_return_pct": ytd_return,
        "1y_return_pct": yr_return,
        "52w_high": round(float(highs.max()), 4) if not highs.empty else None,
        "52w_low": round(float(lows.min()), 4) if not lows.empty else None,
    }


async def _fetch_monthly_returns(symbol: str) -> dict:
    """Fetch 5 years of monthly OHLCV and compute multi-period returns."""
    ticker = yfinance.Ticker(symbol)
    try:
        hist = await asyncio.to_thread(ticker.history, period="5y", interval="1mo")
    except Exception as e:
        log.warning("yfinance monthly fetch failed for %s: %s", symbol, e)
        return {"error": str(e)}

    if hist.empty:
        return {"error": "no data"}

    closes = hist["Close"].dropna()
    if len(closes) < 2:
        return {"error": "insufficient data"}

    current = float(closes.iloc[-1])

    def _period_return(n_months: int) -> float | None:
        if len(closes) < n_months + 1:
            return None
        start = float(closes.iloc[-n_months - 1])
        return round((current / start - 1) * 100, 2) if start else None

    current_year = closes.index[-1].year
    ytd_closes = closes[closes.index.year == current_year]
    ytd_start = float(ytd_closes.iloc[0]) if not ytd_closes.empty else current
    ytd = round((current / ytd_start - 1) * 100, 2) if ytd_start else None

    return {
        "current": round(current, 4),
        "1mo": _period_return(1),
        "3mo": _period_return(3),
        "6mo": _period_return(6),
        "ytd": ytd,
        "1y": _period_return(12),
        "3y": _period_return(36),
        "5y": _period_return(60),
    }


# ---------------------------------------------------------------------------
# Public dashboard functions
# ---------------------------------------------------------------------------


async def get_market_overview() -> dict:
    """Fetch major US indices, VIX, dollar, gold, and oil with YTD and 52-week context."""
    cache_key = "market_overview"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    symbols = list(_MARKET_OVERVIEW_TICKERS.keys())
    results = await asyncio.gather(
        *[_fetch_weekly_performance(sym) for sym in symbols],
        return_exceptions=True,
    )

    markets: dict[str, dict] = {}
    for sym, name, result in zip(symbols, _MARKET_OVERVIEW_TICKERS.values(), results):
        if isinstance(result, BaseException):
            markets[sym] = {"name": name, "error": str(result)}
        else:
            markets[sym] = {"name": name, **result}

    # VIX fear signal
    vix = markets.get("^VIX", {})
    vix_level = vix.get("current")
    if vix_level is not None:
        if vix_level < VIX_LOW_THRESHOLD:
            vix["signal"] = "low_fear — markets complacent"
        elif vix_level < VIX_ELEVATED_THRESHOLD:
            vix["signal"] = "normal"
        elif vix_level < VIX_HIGH_THRESHOLD:
            vix["signal"] = "elevated_fear"
        else:
            vix["signal"] = "high_fear — crisis territory"

    out = {"markets": markets, "source": "yahoo_finance"}
    cache.set(cache_key, out, CACHE_TTL_HISTORY)
    return out


async def get_international_markets() -> dict:
    """Fetch international equity ETF performance: EAFE, EM, and key country exposures."""
    cache_key = "international_markets"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    symbols = list(_INTERNATIONAL_TICKERS.keys())
    results = await asyncio.gather(
        *[_fetch_weekly_performance(sym) for sym in symbols],
        return_exceptions=True,
    )

    markets: dict[str, dict] = {}
    for sym, name, result in zip(symbols, _INTERNATIONAL_TICKERS.values(), results):
        if isinstance(result, BaseException):
            markets[sym] = {"name": name, "error": str(result)}
        else:
            markets[sym] = {"name": name, **result}

    out = {
        "markets": markets,
        "note": "All returns in USD. ETF prices reflect both local equity returns and currency movements.",
        "source": "yahoo_finance",
    }
    cache.set(cache_key, out, CACHE_TTL_HISTORY)
    return out


async def get_style_box() -> dict:
    """Fetch US equity style box performance: Large/Mid/Small × Growth/Value/Blend."""
    cache_key = "style_box"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    symbols = list(_STYLE_BOX_TICKERS.keys())
    results = await asyncio.gather(
        *[_fetch_weekly_performance(sym) for sym in symbols],
        return_exceptions=True,
    )

    styles: dict[str, dict] = {}
    for sym, name, result in zip(symbols, _STYLE_BOX_TICKERS.values(), results):
        if isinstance(result, BaseException):
            styles[sym] = {"name": name, "error": str(result)}
        else:
            styles[sym] = {"name": name, **result}

    out = {
        "style_box": styles,
        "note": "iShares ETF proxies. Returns in USD. Use to identify which size/style factor is leading or lagging.",
        "source": "yahoo_finance",
    }
    cache.set(cache_key, out, CACHE_TTL_HISTORY)
    return out


async def get_asset_class_returns() -> dict:
    """Fetch multi-period returns across asset classes: equities, bonds, REITs, gold, commodities."""
    cache_key = "asset_class_returns"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    symbols = list(_ASSET_CLASS_TICKERS.keys())
    results = await asyncio.gather(
        *[_fetch_monthly_returns(sym) for sym in symbols],
        return_exceptions=True,
    )

    asset_classes: dict[str, dict] = {}
    for sym, name, result in zip(symbols, _ASSET_CLASS_TICKERS.values(), results):
        if isinstance(result, BaseException):
            asset_classes[sym] = {"name": name, "error": str(result)}
        else:
            asset_classes[sym] = {"name": name, **result}

    out = {
        "asset_classes": asset_classes,
        "note": "ETF-based proxies. All returns in USD including dividends (total return). Periods: 1mo, 3mo, 6mo, ytd, 1y, 3y, 5y.",
        "source": "yahoo_finance",
    }
    cache.set(cache_key, out, CACHE_TTL_HISTORY)
    return out


async def _fetch_last_close(symbol: str) -> float | None:
    """Fetch the most recent daily close for a symbol, or None if unavailable."""
    ticker = yfinance.Ticker(symbol)
    try:
        hist = await asyncio.to_thread(ticker.history, period="5d", interval="1d")
    except Exception as e:
        log.warning("yfinance last-close fetch failed for %s: %s", symbol, e)
        return None
    if hist.empty:
        return None
    closes = hist["Close"].dropna()
    if closes.empty:
        return None
    return float(closes.iloc[-1])


async def fetch_gold_dashboard_entry() -> dict:
    """Fetch gold front-month futures (GC=F) shaped like a FRED dashboard indicator.

    FRED's LBMA gold series (GOLDAMGBD228NLBX) was discontinued, so the
    commodities dashboard sources gold from Yahoo instead.
    """
    ticker = yfinance.Ticker("GC=F")
    try:
        hist = await asyncio.to_thread(ticker.history, period="5d", interval="1d")
    except Exception as e:
        log.warning("yfinance gold fetch failed: %s", e)
        return {"error": str(e), "source": "yahoo_finance"}

    closes = hist["Close"].dropna() if not hist.empty else None
    if closes is None or closes.empty:
        return {"error": "no data", "source": "yahoo_finance"}

    latest = round(float(closes.iloc[-1]), 2)
    previous = round(float(closes.iloc[-2]), 2) if len(closes) > 1 else None
    return {
        "latest_value": latest,
        "latest_date": closes.index[-1].strftime("%Y-%m-%d"),
        "previous_value": previous,
        "change": round(latest - previous, 2) if previous is not None else None,
        "title": "Gold Futures Front Month (COMEX GC=F)",
        "units": "Dollars per Troy Ounce",
        "frequency": "Daily",
        "source": "yahoo_finance",
    }


# ---------------------------------------------------------------------------
# Fed funds futures — market-implied policy path
# ---------------------------------------------------------------------------

# CME month codes: Jan..Dec
_FED_FUNDS_MONTH_CODES = {
    1: "F",
    2: "G",
    3: "H",
    4: "J",
    5: "K",
    6: "M",
    7: "N",
    8: "Q",
    9: "U",
    10: "V",
    11: "X",
    12: "Z",
}


def _fed_funds_contracts(start_year: int, start_month: int, months: int) -> list[dict]:
    """Generate consecutive monthly ZQ (30-day fed funds) futures tickers."""
    contracts = []
    year, month = start_year, start_month
    for _ in range(months):
        code = _FED_FUNDS_MONTH_CODES[month]
        contracts.append({"ticker": f"ZQ{code}{year % 100:02d}.CBT", "month": f"{year:04d}-{month:02d}"})
        month += 1
        if month > 12:
            month = 1
            year += 1
    return contracts


async def get_fed_path() -> dict:
    """Fetch the market-implied Fed policy path from 30-day fed funds futures (ZQ).

    Implied rate = 100 − futures price. Comparing deferred contracts to the
    front month shows how many basis points of cuts or hikes are priced in.
    """
    cache_key = "fed_path"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    now = datetime.now(timezone.utc)
    contracts = _fed_funds_contracts(now.year, now.month, FED_PATH_MONTHS_AHEAD)
    closes = await asyncio.gather(
        *[_fetch_last_close(c["ticker"]) for c in contracts],
        return_exceptions=True,
    )

    path = []
    for contract, close in zip(contracts, closes):
        if isinstance(close, BaseException) or close is None:
            continue
        path.append({**contract, "implied_rate_pct": round(100 - close, 3)})

    if not path:
        return {"error": "No fed funds futures data available", "source": "yahoo_finance"}

    front_rate = path[0]["implied_rate_pct"]
    for point in path:
        point["change_from_front_bp"] = round((point["implied_rate_pct"] - front_rate) * 100)

    end_change_bp = path[-1]["change_from_front_bp"]
    horizon = path[-1]["month"]
    if end_change_bp <= -FED_PATH_SIGNAL_THRESHOLD_BP:
        signal = f"market prices ~{abs(end_change_bp)}bp of CUTS by {horizon}"
    elif end_change_bp >= FED_PATH_SIGNAL_THRESHOLD_BP:
        signal = f"market prices ~{end_change_bp}bp of HIKES by {horizon}"
    else:
        signal = "market prices the Fed roughly on hold"

    out = {
        "front_month_implied_rate_pct": front_rate,
        "path": path,
        "end_of_horizon_change_bp": end_change_bp,
        "signal": signal,
        "note": "Implied rate = 100 − ZQ futures price (CME 30-day fed funds). Each point is the average fed funds rate the market expects for that month. This is the same data behind CME FedWatch.",
        "source": "yahoo_finance",
    }
    cache.set(cache_key, out, CACHE_TTL_FED_PATH)
    return out


# ---------------------------------------------------------------------------
# Equity sentiment — VIX term structure, SKEW, market breadth
# ---------------------------------------------------------------------------


async def get_equity_sentiment() -> dict:
    """Fetch equity sentiment gauges: VIX term structure, CBOE SKEW, and RSP/SPY breadth.

    VIX above VIX3M (backwardation) marks acute fear; equal-weight (RSP)
    outperforming cap-weight (SPY) marks broad participation in the rally.
    """
    cache_key = "equity_sentiment"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    vix, vix3m, skew = await asyncio.gather(
        _fetch_last_close("^VIX"),
        _fetch_last_close("^VIX3M"),
        _fetch_last_close("^SKEW"),
        return_exceptions=True,
    )
    rsp, spy = await asyncio.gather(
        _fetch_monthly_returns("RSP"),
        _fetch_monthly_returns("SPY"),
        return_exceptions=True,
    )

    vix = None if isinstance(vix, BaseException) else vix
    vix3m = None if isinstance(vix3m, BaseException) else vix3m
    skew = None if isinstance(skew, BaseException) else skew
    rsp = {} if isinstance(rsp, BaseException) else rsp
    spy = {} if isinstance(spy, BaseException) else spy

    # VIX term structure
    ratio = round(vix / vix3m, 3) if vix and vix3m else None
    if ratio is None:
        term_signal = "no data"
    elif ratio > VIX_TERM_BACKWARDATION_RATIO:
        term_signal = "backwardation — acute near-term fear, historically near capitulation lows"
    elif ratio < VIX_TERM_COMPLACENCY_RATIO:
        term_signal = "steep contango — markets complacent, hedges cheap"
    else:
        term_signal = "normal contango — orderly market"

    # Tail-risk skew
    if skew is None:
        skew_signal = "no data"
    elif skew > SKEW_ELEVATED_THRESHOLD:
        skew_signal = "elevated — heavy demand for crash protection (tail-risk hedging)"
    else:
        skew_signal = "normal — no unusual tail-risk hedging"

    # Breadth: equal-weight vs cap-weight S&P 500
    def _spread(period: str) -> float | None:
        rsp_val, spy_val = rsp.get(period), spy.get(period)
        if rsp_val is None or spy_val is None:
            return None
        return round(rsp_val - spy_val, 2)

    breadth_1mo = _spread("1mo")
    breadth_3mo = _spread("3mo")
    if breadth_1mo is None:
        breadth_signal = "no data"
    elif breadth_1mo > 0:
        breadth_signal = "broad participation — average stock beating the cap-weighted index (healthy)"
    else:
        breadth_signal = "narrow leadership — gains concentrated in mega-caps (fragile)"

    out = {
        "vix_term_structure": {
            "vix": vix,
            "vix3m": vix3m,
            "ratio": ratio,
            "signal": term_signal,
        },
        "skew": {"value": skew, "signal": skew_signal},
        "breadth": {
            "rsp_vs_spy_1mo_pct": breadth_1mo,
            "rsp_vs_spy_3mo_pct": breadth_3mo,
            "signal": breadth_signal,
        },
        "note": "VIX/VIX3M >1 = backwardation (acute fear). SKEW >145 = heavy tail hedging. RSP−SPY spread >0 = broad breadth.",
        "source": "yahoo_finance",
    }
    cache.set(cache_key, out, CACHE_TTL_EQUITY_SENTIMENT)
    return out


async def get_stock_fundamentals(symbol: str) -> dict:
    """Fetch deep fundamental metrics for a stock: valuation, margins, growth, leverage, and sentiment."""
    cache_key = f"fundamentals_extended_{symbol}"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    ticker = yfinance.Ticker(symbol)
    try:
        info = await asyncio.to_thread(lambda: ticker.info)
    except Exception as e:
        log.warning("yfinance fundamentals failed for %s: %s", symbol, e)
        return {"symbol": symbol, "error": str(e), "source": "yahoo_finance"}

    if not info or not info.get("regularMarketPrice"):
        return {"symbol": symbol, "error": "No fundamental data available", "source": "yahoo_finance"}

    result = {
        "symbol": symbol,
        "name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        # Valuation multiples
        "valuation": {
            "forward_pe": info.get("forwardPE"),
            "trailing_pe": info.get("trailingPE"),
            "peg_ratio": info.get("pegRatio"),
            "price_to_book": info.get("priceToBook"),
            "price_to_sales": info.get("priceToSalesTrailing12Months"),
            "ev_to_ebitda": info.get("enterpriseToEbitda"),
            "ev_to_revenue": info.get("enterpriseToRevenue"),
        },
        # Profitability
        "margins": {
            "gross_margin": info.get("grossMargins"),
            "operating_margin": info.get("operatingMargins"),
            "profit_margin": info.get("profitMargins"),
            "return_on_equity": info.get("returnOnEquity"),
            "return_on_assets": info.get("returnOnAssets"),
        },
        # Growth
        "growth": {
            "revenue_growth_yoy": info.get("revenueGrowth"),
            "earnings_growth_yoy": info.get("earningsGrowth"),
            "earnings_quarterly_growth": info.get("earningsQuarterlyGrowth"),
        },
        # Financial health
        "balance_sheet": {
            "debt_to_equity": info.get("debtToEquity"),
            "current_ratio": info.get("currentRatio"),
            "quick_ratio": info.get("quickRatio"),
            "total_cash": info.get("totalCash"),
            "total_debt": info.get("totalDebt"),
            "free_cashflow": info.get("freeCashflow"),
        },
        # Market / sentiment
        "market_data": {
            "beta": info.get("beta"),
            "52w_change": info.get("52WeekChange"),
            "sp500_52w_change": info.get("SandP52WeekChange"),
            "short_ratio": info.get("shortRatio"),
            "short_pct_float": info.get("shortPercentOfFloat"),
            "institutional_pct": info.get("heldPercentInstitutions"),
            "insider_pct": info.get("heldPercentInsiders"),
        },
        "source": "yahoo_finance",
    }
    cache.set(cache_key, result, CACHE_TTL_FUNDAMENTALS)
    return result

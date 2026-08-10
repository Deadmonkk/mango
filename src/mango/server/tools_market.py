"""Equity market tools: quotes, fundamentals, filings, technicals, screening, search."""

from __future__ import annotations

from mango.providers import edgar, finnhub, screener, search, technical
from mango.core import historical
from mango.server import csv_symbols, tool


@tool
async def get_quote(symbol: str) -> dict:
    """Real-time price quote for one ticker: last price, change, day range."""
    return await finnhub.get_quote(symbol.upper())


@tool
async def get_quotes_batch(symbols: str) -> dict:
    """Quotes for several tickers at once. Pass a comma-separated list."""
    return {"quotes": await finnhub.get_quotes_batch(csv_symbols(symbols))}


@tool
async def get_company_profile(symbol: str) -> dict:
    """Company overview: name, industry, market cap, exchange, country."""
    return await finnhub.get_company_profile(symbol.upper())


@tool
async def get_news(symbol: str, days: int = 7) -> dict:
    """Recent news headlines for a company, over the last `days` days."""
    return await finnhub.get_company_news(symbol.upper(), days)


@tool
async def get_earnings(symbol: str) -> dict:
    """Earnings history and forward estimates: reported vs expected EPS."""
    return await finnhub.get_earnings(symbol.upper())


@tool
async def get_analyst_ratings(symbol: str) -> dict:
    """Analyst buy/hold/sell consensus and price targets."""
    return await finnhub.get_analyst_ratings(symbol.upper())


@tool
async def get_economic_calendar(days: int = 7) -> dict:
    """Upcoming economic releases. Falls back to a free source when the
    premium calendar is unavailable, which is the normal case."""
    return await finnhub.get_economic_calendar(days)


@tool
async def get_historical(symbol: str, period: str = "1y", interval: str = "1d") -> dict:
    """Historical OHLCV bars, oldest first. Periods like 1mo, 6mo, 1y, 5y."""
    return await historical.get_historical(symbol.upper(), period=period, interval=interval)


@tool
async def get_dividends(symbol: str, years: int = 5) -> dict:
    """Dividend payment history with an inferred payout frequency."""
    return await historical.get_dividends(symbol.upper(), years=years)


@tool
async def get_financials(symbol: str, statement: str = "income", periods: int = 4) -> dict:
    """Financial statements from SEC filings. `statement`: income, balance, cash."""
    return await edgar.get_financials(symbol.upper(), statement=statement, periods=periods)


@tool
async def get_filings(symbol: str, filing_type: str = "", limit: int = 10) -> dict:
    """SEC filing index for a company, optionally filtered by form type (10-K, 8-K...)."""
    return await edgar.get_filings(symbol.upper(), filing_type=filing_type, limit=limit)


@tool
async def get_insider_transactions(symbol: str, limit: int = 10) -> dict:
    """Insider buys and sells from Form 4 filings, with a net summary.

    Routine board grants and open-market purchases both appear here and mean
    very different things; the transaction type distinguishes them.
    """
    return await edgar.get_insider_transactions(symbol.upper(), limit=limit)


@tool
async def get_13f_holdings(institution: str, limit: int = 20) -> dict:
    """Latest 13F holdings for a tracked institution (berkshire, scion, ark...).

    13F filings lag the quarter by up to 45 days, so this is a snapshot of the
    past, not current positioning.
    """
    return await edgar.get_13f_holdings(institution, limit=limit)


@tool
async def get_technicals(symbol: str) -> dict:
    """Technical indicators: SMA/EMA, RSI, MACD, Bollinger bands, ATR."""
    return await technical.get_full_technicals(symbol.upper())


@tool
async def get_stock_fundamentals(symbol: str) -> dict:
    """Profile, latest quote and analyst view for one ticker, combined."""
    ticker = symbol.upper()
    return {
        "symbol": ticker,
        "profile": await finnhub.get_company_profile(ticker),
        "quote": await finnhub.get_quote(ticker),
        "ratings": await finnhub.get_analyst_ratings(ticker),
    }


@tool
async def screen_stocks(
    sector: str = "",
    min_market_cap: float = 0,
    max_market_cap: float = 0,
    limit: int = 20,
) -> dict:
    """Screen S&P 500 constituents by sector and market-cap range.

    Check `is_complete`: when False the result was cut short by a fetch budget
    rather than by the filters, so absence does not mean no match.
    """
    return await screener.screen_stocks(
        sector=sector, min_market_cap=min_market_cap, max_market_cap=max_market_cap, limit=limit
    )


@tool
async def web_search(query: str, count: int = 5) -> dict:
    """Web search for market research. Results are external and unverified."""
    return await search.web_search(query, count=count)

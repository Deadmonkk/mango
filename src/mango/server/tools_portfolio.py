"""Portfolio tools: holdings, watchlist, RSUs, allocation, risk, charts."""

from __future__ import annotations

from mango import charts
from mango.analytics import allocation, risk
from mango.core import historical, portfolio
from mango.providers import finnhub, fred_ext, rsu_tax, sectors
from mango.server import csv_symbols, tool

# Sector ETFs used for the heatmap, in the conventional GICS display order.
SECTOR_ETFS = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financials": "XLF",
    "Cons. Disc.": "XLY",
    "Industrials": "XLI",
    "Comm. Svcs": "XLC",
    "Cons. Staples": "XLP",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Materials": "XLB",
}


@tool
async def terminalq_get_portfolio() -> dict:
    """Current holdings grouped by account, with cost basis and unrealised P/L."""
    holdings = portfolio.load_portfolio()
    if not holdings:
        return {"error": "No portfolio data found", "source": "portfolio"}

    accounts: dict[str, list] = {}
    for holding in holdings:
        accounts.setdefault(holding.get("account", "Unknown"), []).append(holding)

    return {
        "as_of": portfolio.get_portfolio_as_of(),
        "accounts": accounts,
        "total_market_value": round(sum(h["market_value"] for h in holdings), 2),
        "total_cost_basis": round(sum(h["cost_basis"] for h in holdings), 2),
        "total_unrealized_gl": round(sum(h["unrealized_gl"] for h in holdings), 2),
        "unique_symbols": portfolio.get_unique_symbols(),
        "source": "portfolio",
    }


@tool
async def terminalq_get_portfolio_live() -> dict:
    """Holdings priced at the current market, with the day's move per position."""
    holdings = portfolio.load_portfolio()
    if not holdings:
        return {"error": "No portfolio data found", "source": "portfolio"}

    quotes = await finnhub.get_quotes_batch(portfolio.get_unique_symbols())
    by_symbol = {q.get("symbol"): q for q in quotes}

    priced = []
    for holding in holdings:
        quote = by_symbol.get(holding["symbol"], {})
        price = quote.get("current_price")
        live_value = round(price * holding["shares"], 2) if price else None
        priced.append({
            **holding,
            "current_price": price,
            "live_market_value": live_value,
            "day_change_pct": quote.get("percent_change"),
        })

    return {
        "as_of": portfolio.get_portfolio_as_of(),
        "holdings": priced,
        "live_total": round(sum(h["live_market_value"] or 0 for h in priced), 2),
        "source": "portfolio+finnhub",
    }


@tool
async def terminalq_get_watchlist() -> dict:
    """Watchlist symbols with live quotes."""
    items = portfolio.load_watchlist()
    if not items:
        return {"error": "No watchlist found", "source": "portfolio"}

    quotes = await finnhub.get_quotes_batch([i["symbol"] for i in items])
    by_symbol = {q.get("symbol"): q for q in quotes}
    return {
        "watchlist": [{**i, "quote": by_symbol.get(i["symbol"], {})} for i in items],
        "count": len(items),
        "source": "portfolio+finnhub",
    }


@tool
async def terminalq_get_rsu_schedule() -> dict:
    """RSU vesting schedule as recorded locally."""
    schedule = portfolio.load_rsu_schedule()
    if not schedule:
        return {"error": "No RSU schedule found", "source": "portfolio"}
    return {"vests": schedule, "count": len(schedule), "source": "portfolio"}


@tool
async def terminalq_get_rsu_tax_analysis(marginal_rate: float = 0.32, ltcg_rate: float = 0.15) -> dict:
    """Estimated tax on upcoming RSU vests and the sell-vs-hold trade-off.

    Estimates from local data at the rates given. Not tax advice.
    """
    return await rsu_tax.get_rsu_tax_analysis(marginal_rate, ltcg_rate)


@tool
async def terminalq_get_allocation() -> dict:
    """Portfolio breakdown by asset class, region and sub-class, with concentration."""
    return allocation.compute_allocation()


@tool
async def terminalq_get_risk_metrics(period: str = "1y") -> dict:
    """Portfolio risk: Sharpe, Sortino, max drawdown, VaR(95), beta vs SPY.

    All are backward-looking statistics of realised returns and say nothing
    about the risks that have not shown up in the sample yet.
    """
    return await risk.compute_portfolio_risk(period)


@tool
async def terminalq_chart_price(symbol: str, period: str = "6mo", chart_type: str = "line") -> dict:
    """Text price chart for a ticker. `chart_type`: line or candlestick."""
    data = await historical.get_historical(symbol.upper(), period=period, interval="1d")
    if "error" in data:
        return data
    bars = data["prices"]
    title = f"{symbol.upper()} — {period}"
    rendered = (
        charts.candlestick_chart(bars, title=title)
        if chart_type == "candlestick"
        else charts.line_chart(bars, title=title)
    )
    return {"symbol": symbol.upper(), "period": period, "chart": rendered, "source": "charts"}


@tool
async def terminalq_chart_comparison(symbols: str, period: str = "1y") -> dict:
    """Compare several tickers on one chart, as percent return from the start."""
    series = {}
    for ticker in csv_symbols(symbols):
        data = await historical.get_historical(ticker, period=period, interval="1d")
        if "error" not in data and data["prices"]:
            series[ticker] = data["prices"]
    if not series:
        return {"error": "No price history for any symbol given", "source": "charts"}
    return {
        "symbols": sorted(series),
        "period": period,
        "chart": charts.comparison_chart(series, title=f"Relative performance — {period}"),
        "source": "charts",
    }


@tool
async def terminalq_chart_allocation() -> dict:
    """Portfolio allocation by asset class as a proportional bar chart."""
    alloc = allocation.compute_allocation()
    if "error" in alloc:
        return alloc
    return {
        "chart": charts.allocation_pie(alloc["by_asset_class"], title="Allocation by asset class"),
        "total_value": alloc["total_value"],
        "source": "charts",
    }


@tool
async def terminalq_chart_yield_curve() -> dict:
    """US Treasury yield curve across maturities."""
    rates = await fred_ext.get_rates_dashboard()
    if "error" in rates:
        return rates
    indicators = rates.get("indicators", {})
    points = [
        (label, indicators[key]["latest_value"])
        for label, key in (("2y", "2y_yield"), ("10y", "10y_yield"), ("30y", "30y_yield"))
        if isinstance(indicators.get(key), dict) and indicators[key].get("latest_value") is not None
    ]
    if not points:
        return {"error": "No yield data available", "source": "charts"}
    return {"chart": charts.yield_curve_chart(points, title="US Treasury yield curve"),
            "points": dict(points), "source": "charts"}


@tool
async def terminalq_chart_sector_heatmap() -> dict:
    """S&P 500 sector performance relative to the index."""
    rotation = await sectors.get_sector_rotation()
    if "error" in rotation:
        return rotation
    rows = [(s["sector"], s["relative_1mo_pct"]) for s in rotation.get("sectors", [])]
    if not rows:
        return {"error": "No sector data available", "source": "charts"}
    return {"chart": charts.heatmap(rows, title="Sector vs SPY — 1 month (%)"),
            "sectors": dict(rows), "source": "charts"}

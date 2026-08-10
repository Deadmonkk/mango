"""Crypto tools: quotes, on-chain, derivatives, flows, sentiment."""

from __future__ import annotations

from mango.providers import (
    coingecko,
    crypto_analytics,
    crypto_funding,
    defillama,
    etf_flows,
    mempool,
)
from mango.server import csv_symbols, tool


@tool
async def get_crypto(symbol: str) -> dict:
    """Price and 24-hour change for one cryptocurrency."""
    return await coingecko.get_crypto_quote(symbol.upper())


@tool
async def get_crypto_batch(symbols: str) -> dict:
    """Quotes for several cryptocurrencies at once. Comma-separated."""
    return await coingecko.get_crypto_batch(csv_symbols(symbols))


@tool
async def get_crypto_market_overview() -> dict:
    """Total crypto market cap, 24-hour volume and active coin count."""
    return await coingecko.get_crypto_market_overview()


@tool
async def get_crypto_deep(symbol: str) -> dict:
    """Full profile for one asset: returns across horizons, supply, all-time high."""
    return await coingecko.get_crypto_deep(symbol.upper())


@tool
async def get_crypto_dominance() -> dict:
    """Share of total market cap by asset, and whether capital favours BTC or alts."""
    return await coingecko.get_crypto_dominance()


@tool
async def get_crypto_trending() -> dict:
    """Currently most-searched coins.

    Attention, not capital — a trending list of falling assets is speculative
    churn rather than accumulation.
    """
    return await coingecko.get_crypto_trending()


@tool
async def get_crypto_derivatives() -> dict:
    """Perpetual futures dashboard across venues."""
    return await coingecko.get_crypto_derivatives_dashboard()


@tool
async def screen_cryptos(
    category: str = "",
    min_market_cap_b: float = 0,
    max_market_cap_b: float = 0,
    sort_by: str = "market_cap_desc",
    limit: int = 20,
) -> dict:
    """Screen cryptocurrencies by category and market-cap range."""
    return await coingecko.screen_cryptos(
        category=category,
        min_market_cap_b=min_market_cap_b,
        max_market_cap_b=max_market_cap_b,
        sort_by=sort_by,
        limit=limit,
    )


@tool
async def get_crypto_technicals(symbol: str) -> dict:
    """Technical indicators for a crypto asset: RSI, moving averages, cross state."""
    return await crypto_analytics.get_crypto_technicals(symbol.upper())


@tool
async def get_crypto_correlations(symbol: str = "BTC") -> dict:
    """How one crypto asset correlates with equities, bonds, gold and the dollar."""
    return await crypto_analytics.get_crypto_correlations(symbol.upper())


@tool
async def get_btc_onchain() -> dict:
    """Bitcoin network health: hash rate, difficulty, block times, transactions."""
    return await crypto_analytics.get_btc_onchain()


@tool
async def get_btc_valuation() -> dict:
    """Bitcoin MVRV and realised price, ranked against their own history.

    Ranked by percentile rather than fixed bands, because a band map put an
    MVRV near its 21st percentile at 92 out of 100.
    """
    return await crypto_analytics.get_btc_valuation()


@tool
async def get_fear_greed(limit: int = 30) -> dict:
    """Crypto Fear and Greed index with recent history. Contrarian at extremes."""
    return await crypto_analytics.get_fear_greed(limit)


@tool
async def get_crypto_funding(symbol: str = "BTC") -> dict:
    """Perpetual funding rate, weighted by open interest and cross-checked against basis.

    Weighting matters: an unweighted mean across venues once read 38x too high,
    because tiny venues carry extreme rates and almost no open interest.
    """
    return await crypto_funding.get_btc_funding(symbol.upper())


@tool
async def get_btc_mempool() -> dict:
    """Bitcoin mempool fees and congestion — a read on real on-chain demand."""
    return await mempool.get_btc_mempool()


@tool
async def get_btc_etf_flows(days: int = 10) -> dict:
    """Daily spot Bitcoin ETF flows: institutional demand entering or leaving."""
    return await etf_flows.get_btc_etf_flows(days)


@tool
async def get_stablecoins() -> dict:
    """Total stablecoin supply and its trend — dry powder already inside crypto."""
    return await defillama.get_stablecoins_overview()


@tool
async def get_defi_overview() -> dict:
    """Total DeFi value locked and the leading chains."""
    return await defillama.get_defi_overview()

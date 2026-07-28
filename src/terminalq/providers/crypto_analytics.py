"""Crypto analytics provider — Fear & Greed, BTC on-chain, technicals, and macro correlations."""

import asyncio
import math

import httpx
from terminalq.config import (
    CACHE_TTL_CORRELATIONS,
    CACHE_TTL_CRYPTO_TECHNICALS,
    CACHE_TTL_FEAR_GREED,
    CACHE_TTL_ONCHAIN,
    FEAR_GREED_EXTREME_FEAR,
    FEAR_GREED_EXTREME_GREED,
    HALVING_INTERVAL,
)
from terminalq.logging_config import log
from terminalq.providers.coingecko import BASE_URL, _fetch, _resolve_id

from terminalq import cache
from terminalq._lazy_yfinance import yfinance
from terminalq.providers import mempool, yahoo_crypto

_BLOCKCHAIN_COM_STATS = "https://api.blockchain.info/stats"
_ALTERNATIVE_ME_URL = "https://api.alternative.me/fng/"

# Correlation comparison tickers (all available via yfinance, no API key)
_CORRELATION_TICKERS: dict[str, str] = {
    "SPY": "S&P 500",
    "GLD": "Gold",
    "DX-Y.NYB": "US Dollar (DXY)",
    "IEF": "7-10yr Treasury",
}

# NVT thresholds (trading-volume NVT proxy, not on-chain)
_NVT_OVERVALUED = 65
_NVT_UNDERVALUED = 25


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 2)


def _sma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return round(sum(closes[-period:]) / period, 4)


def _ema(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    mult = 2 / (period + 1)
    val = sum(closes[:period]) / period
    for price in closes[period:]:
        val = (price - val) * mult + val
    return round(val, 4)


def _realized_vol(closes: list[float], days: int = 30) -> float | None:
    if len(closes) < days + 1:
        return None
    recent = closes[-(days + 1) :]
    returns = [(recent[i] - recent[i - 1]) / recent[i - 1] for i in range(1, len(recent)) if recent[i - 1] != 0]
    if not returns:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return round(math.sqrt(variance) * math.sqrt(365) * 100, 2)  # annualized %


def _pearson(x: list[float], y: list[float]) -> float:
    n = min(len(x), len(y))
    if n < 10:
        return 0.0
    x, y = x[-n:], y[-n:]
    mx, my = sum(x) / n, sum(y) / n
    num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    dx = math.sqrt(sum((v - mx) ** 2 for v in x))
    dy = math.sqrt(sum((v - my) ** 2 for v in y))
    if dx == 0 or dy == 0:
        return 0.0
    return round(num / (dx * dy), 3)


def _daily_returns(closes: list[float]) -> list[float]:
    return [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1] != 0]


# ---------------------------------------------------------------------------
# Fear & Greed
# ---------------------------------------------------------------------------


async def get_fear_greed(limit: int = 30) -> dict:
    """Fear & Greed Index from Alternative.me with history and signal interpretation."""
    cache_key = f"fear_greed_{limit}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(_ALTERNATIVE_ME_URL, params={"limit": limit, "format": "json"}, timeout=10)
            resp.raise_for_status()
            raw = resp.json()
    except Exception as e:
        log.warning("Alternative.me Fear & Greed fetch failed: %s", e)
        return {"error": str(e), "source": "alternative.me"}

    entries = []
    for item in raw.get("data", []):
        val = int(item.get("value", 0))
        entries.append(
            {
                "value": val,
                "classification": item.get("value_classification"),
                "timestamp": item.get("timestamp"),
            }
        )

    if not entries:
        return {"error": "No data returned", "source": "alternative.me"}

    current = entries[0]
    val = current["value"]

    if val <= FEAR_GREED_EXTREME_FEAR:
        signal = "EXTREME FEAR — historically the best buying zone. Retail has capitulated."
    elif val <= 40:
        signal = "Fear — market nervous, potential accumulation zone."
    elif val <= 60:
        signal = "Neutral — no strong directional bias."
    elif val < FEAR_GREED_EXTREME_GREED:
        signal = "Greed — market optimistic, be cautious of chasing."
    else:
        signal = "EXTREME GREED — historically a sell zone. Retail is euphoric."

    # 7-day trend
    recent_vals = [e["value"] for e in entries[:7]]
    trend = (
        "improving"
        if recent_vals[0] > recent_vals[-1]
        else ("deteriorating" if recent_vals[0] < recent_vals[-1] else "flat")
    )

    result = {
        "current": {**current, "signal": signal},
        "7d_trend": trend,
        "7d_values": recent_vals,
        "history": entries,
        "note": f"Scale: 0=Extreme Fear, 100=Extreme Greed. Extreme Fear (<{FEAR_GREED_EXTREME_FEAR}) = historic buy. Extreme Greed (>{FEAR_GREED_EXTREME_GREED}) = historic sell.",
        "source": "alternative.me",
    }
    cache.set(cache_key, result, CACHE_TTL_FEAR_GREED)
    return result


# ---------------------------------------------------------------------------
# BTC On-Chain (Blockchain.com)
# ---------------------------------------------------------------------------


def _satoshi_to_btc(value: float | None) -> float | None:
    """Convert satoshis to BTC; None for missing or impossible (<=0) values.

    blockchain.info's /stats endpoint sometimes returns negative fee totals —
    an impossible number must never be reported as fact.
    """
    if not value or value <= 0:
        return None
    return round(value / 1e8, 6)


def _build_btc_onchain(
    *,
    hash_rate: float | None,
    difficulty: float | None,
    n_blocks: int | None,
    avg_minutes: float | None,
    n_tx: int | None,
    btc_sent_satoshi: float | None,
    tx_volume_usd: float | None,
    total_fees_satoshi: float | None,
    market_price_usd: float | None,
    source: str,
) -> dict:
    """Assemble the on-chain result from primitive values, shared by both the
    primary (blockchain.com) and fallback (mempool.space) paths.

    Fields the fallback source cannot supply are passed as ``None`` and surface
    as ``None`` — never as a fabricated zero — so degraded data is unambiguous.
    """
    avg_minutes = avg_minutes or 10.0  # BTC protocol target when unknown
    n_blocks = n_blocks or 0

    # Halving countdown
    halving_number = n_blocks // HALVING_INTERVAL
    next_halving_block = (halving_number + 1) * HALVING_INTERVAL
    blocks_to_halving = next_halving_block - n_blocks
    days_to_halving = round(blocks_to_halving * avg_minutes / 60 / 24, 1)
    current_reward = 50 / (2**halving_number)
    next_reward = current_reward / 2

    hash_signal = None
    if hash_rate is not None:
        hash_signal = "network at all-time high security" if hash_rate > 8e11 else "normal security level"

    return {
        "network": {
            "hash_rate_gh_s": hash_rate,
            "hash_rate_signal": hash_signal,
            "difficulty": difficulty,
            "avg_block_time_minutes": round(avg_minutes, 2),
            "total_blocks_mined": n_blocks,
        },
        "transactions": {
            "count_24h": n_tx,
            "volume_btc_24h": round(btc_sent_satoshi / 1e8, 2) if btc_sent_satoshi is not None else None,
            "volume_usd_24h": tx_volume_usd,
            "total_fees_btc": _satoshi_to_btc(total_fees_satoshi),
            "market_price_usd": market_price_usd,
        },
        "halving": {
            "current_block": n_blocks,
            "next_halving_block": next_halving_block,
            "blocks_remaining": blocks_to_halving,
            "estimated_days_remaining": days_to_halving,
            "current_block_reward_btc": current_reward,
            "next_block_reward_btc": next_reward,
            "halving_number": halving_number + 1,
            "context": f"Next halving in ~{days_to_halving:.0f} days. Block reward drops from {current_reward} to {next_reward} BTC. Last 3 halvings preceded major bull runs within 12-18 months.",
        },
        "source": source,
    }


async def get_btc_onchain() -> dict:
    """Bitcoin network health: hash rate, difficulty, transactions, mempool, and halving countdown.

    Primary source is blockchain.com's ``/stats``. When it is unreachable the
    network-security fields (hash rate, difficulty, halving countdown) are
    recovered from mempool.space; the 24h transaction count, sent volume and
    spot price have no mempool.space equivalent and remain ``None`` on fallback.
    """
    cache_key = "btc_onchain"
    cached = cache.get(cache_key)
    if cached:
        return cached

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(_BLOCKCHAIN_COM_STATS, timeout=10)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        log.warning("Blockchain.com stats fetch failed: %s", e)
        fallback = await mempool.fetch_btc_network_stats()
        if fallback is None:
            return {"error": str(e), "source": "blockchain.com"}
        log.info("blockchain.com on-chain failed — served mempool.space network-stats fallback")
        result = _build_btc_onchain(
            hash_rate=fallback["hash_rate_gh_s"],
            difficulty=fallback["difficulty"],
            n_blocks=fallback["total_blocks_mined"],
            avg_minutes=None,
            n_tx=None,
            btc_sent_satoshi=None,
            tx_volume_usd=None,
            total_fees_satoshi=None,
            market_price_usd=None,
            source="mempool.space (fallback — blockchain.com unavailable)",
        )
        cache.set(cache_key, result, CACHE_TTL_ONCHAIN)
        return result

    result = _build_btc_onchain(
        hash_rate=data.get("hash_rate"),
        difficulty=data.get("difficulty"),
        n_blocks=data.get("n_blocks_total", 0),
        avg_minutes=data.get("minutes_between_blocks"),
        n_tx=data.get("n_tx"),
        btc_sent_satoshi=data.get("estimated_btc_sent"),
        tx_volume_usd=data.get("estimated_transaction_volume_usd"),
        total_fees_satoshi=data.get("total_fees_btc"),
        market_price_usd=data.get("market_price_usd"),
        source="blockchain.com",
    )
    cache.set(cache_key, result, CACHE_TTL_ONCHAIN)
    return result


# ---------------------------------------------------------------------------
# Crypto Technicals (computed from CoinGecko price history, Yahoo fallback)
# ---------------------------------------------------------------------------


async def get_crypto_technicals(symbol: str) -> dict:
    """Technical indicators for a crypto asset: RSI, SMA, EMA, MACD, realized volatility, and NVT proxy.

    Primary source is CoinGecko's ``market_chart``; when CoinGecko is unreachable
    the price history is recovered from Yahoo Finance (``<SYMBOL>-USD``). Yahoo has
    no market-cap series, so the NVT proxy is unavailable on the fallback path.
    """
    coin_id = _resolve_id(symbol)
    cache_key = f"crypto_technicals_{coin_id}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    async with httpx.AsyncClient() as client:
        data = await _fetch(
            client,
            f"{BASE_URL}/coins/{coin_id}/market_chart",
            {"vs_currency": "usd", "days": "200"},
        )

    closes: list[float] = []
    volumes: list[float] = []
    mcs: list[float] = []
    source = "coingecko (computed)"

    if isinstance(data, dict) and "_error" in data:
        closes, volumes = await yahoo_crypto.fetch_crypto_ohlcv(symbol, days=200)
        source = "yahoo_finance (computed, fallback — CoinGecko unavailable)"
        if closes:
            log.info("CoinGecko technicals failed for %s — served Yahoo fallback", symbol.upper())
    else:
        closes = [p[1] for p in data.get("prices", [])]
        volumes = [v[1] for v in data.get("total_volumes", [])]
        mcs = [m[1] for m in data.get("market_caps", [])]

    if len(closes) < 50:
        both_down = source.startswith("yahoo")
        return {
            "symbol": symbol.upper(),
            "error": "Insufficient price history (CoinGecko and Yahoo both unavailable)"
            if both_down
            else "Insufficient price history",
            "source": "coingecko",
        }

    result = _compute_technicals(symbol, closes, volumes, mcs, source)
    cache.set(cache_key, result, CACHE_TTL_CRYPTO_TECHNICALS)
    return result


def _compute_technicals(
    symbol: str,
    closes: list[float],
    volumes: list[float],
    mcs: list[float],
    source: str,
) -> dict:
    """Compute the technical-indicator payload from price/volume/market-cap series.

    Shared by the CoinGecko and Yahoo-fallback paths. ``mcs`` may be empty (Yahoo
    has no market-cap series), in which case the NVT proxy is reported as ``None``.
    """
    current_price = closes[-1]

    # Moving averages
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, min(200, len(closes)))
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)

    # Distance from 200-day MA
    dist_200 = round((current_price / sma200 - 1) * 100, 2) if sma200 else None
    dist_signal = None
    if dist_200 is not None:
        if dist_200 > 50:
            dist_signal = "far above 200d MA — historically mean-reverts downward"
        elif dist_200 > 0:
            dist_signal = "above 200d MA — uptrend"
        elif dist_200 > -30:
            dist_signal = "below 200d MA — downtrend"
        else:
            dist_signal = "deep below 200d MA — historically strong long-term buy zone"

    # Golden / death cross
    cross_signal = None
    if sma50 and sma200:
        cross_signal = (
            "golden cross — bullish long-term signal" if sma50 > sma200 else "death cross — bearish long-term signal"
        )

    # RSI
    rsi = _rsi(closes)
    rsi_signal = "overbought" if (rsi or 0) > 70 else ("oversold" if (rsi or 0) < 30 else "neutral")

    # MACD
    macd_line = (ema12 - ema26) if (ema12 and ema26) else None

    # Realized volatility
    vol_30d = _realized_vol(closes, 30)

    # NVT proxy (market cap / 30d avg volume)
    recent_vols = [v for v in volumes[-30:] if v]
    avg_vol_30d = sum(recent_vols) / len(recent_vols) if recent_vols else None
    latest_mc = mcs[-1] if mcs else None
    ntv_proxy = round(latest_mc / avg_vol_30d, 2) if (latest_mc and avg_vol_30d) else None
    ntv_signal = None
    if ntv_proxy is not None:
        if ntv_proxy > _NVT_OVERVALUED:
            ntv_signal = f"elevated ({ntv_proxy:.1f}) — market cap high relative to trading activity"
        elif ntv_proxy < _NVT_UNDERVALUED:
            ntv_signal = f"low ({ntv_proxy:.1f}) — market cap low relative to trading activity, potentially undervalued"
        else:
            ntv_signal = f"normal ({ntv_proxy:.1f})"

    result = {
        "symbol": symbol.upper(),
        "price_usd": round(current_price, 4),
        "moving_averages": {
            "sma_20": sma20,
            "sma_50": sma50,
            "sma_200": sma200,
            "ema_12": ema12,
            "ema_26": ema26,
            "distance_from_200d_ma_pct": dist_200,
            "distance_signal": dist_signal,
            "cross_signal": cross_signal,
        },
        "momentum": {
            "rsi_14": rsi,
            "rsi_signal": rsi_signal,
            "macd_line": round(macd_line, 4) if macd_line else None,
        },
        "volatility": {
            "realized_vol_30d_annualized_pct": vol_30d,
            "note": "Annualized. >100% = high volatility. <50% = relatively calm for crypto.",
        },
        "ntv_proxy": {
            "value": ntv_proxy,
            "signal": ntv_signal,
            "note": "NVT proxy = market cap / 30d avg trading volume. Proxy for 'P/E ratio' of the network.",
        },
        "data_points": len(closes),
        "source": source,
    }
    return result


# ---------------------------------------------------------------------------
# Macro Correlations
# ---------------------------------------------------------------------------


async def get_crypto_correlations(symbol: str = "BTC") -> dict:
    """90-day rolling correlation of a crypto asset vs S&P 500, Gold, US Dollar, and Treasuries."""
    cache_key = f"crypto_correlations_{symbol.upper()}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    crypto_ticker = f"{symbol.upper()}-USD"
    all_tickers = [crypto_ticker] + list(_CORRELATION_TICKERS.keys())

    async def _fetch_closes(ticker: str) -> list[float]:
        try:
            t = yfinance.Ticker(ticker)
            hist = await asyncio.to_thread(t.history, period="6mo", interval="1d")
            if hist.empty:
                return []
            return [float(c) for c in hist["Close"].dropna().tolist()]
        except Exception as e:
            log.warning("yfinance fetch failed for %s: %s", ticker, e)
            return []

    all_closes = await asyncio.gather(*[_fetch_closes(t) for t in all_tickers], return_exceptions=True)

    crypto_closes = all_closes[0] if not isinstance(all_closes[0], BaseException) else []
    if len(crypto_closes) < 30:
        return {"symbol": symbol.upper(), "error": "Insufficient crypto price data", "source": "yahoo_finance"}

    crypto_returns = _daily_returns(crypto_closes[-90:])

    correlations: dict[str, dict] = {}
    for (comp_ticker, name), comp_closes in zip(_CORRELATION_TICKERS.items(), all_closes[1:]):
        if isinstance(comp_closes, BaseException) or len(comp_closes) < 30:
            correlations[comp_ticker] = {"name": name, "correlation_90d": None, "signal": "data unavailable"}
            continue

        comp_returns = _daily_returns(comp_closes[-90:])
        corr = _pearson(crypto_returns, comp_returns)

        if corr > 0.7:
            signal = "strongly correlated — moves together"
        elif corr > 0.4:
            signal = "moderately correlated"
        elif corr > 0.1:
            signal = "weak positive correlation"
        elif corr > -0.1:
            signal = "uncorrelated — independent asset"
        elif corr > -0.4:
            signal = "weak negative correlation"
        elif corr > -0.7:
            signal = "moderately inverse"
        else:
            signal = "strongly inverse — moves opposite"

        correlations[comp_ticker] = {"name": name, "correlation_90d": corr, "signal": signal}

    # Macro regime interpretation
    dxy_corr = correlations.get("DX-Y.NYB", {}).get("correlation_90d")
    spy_corr = correlations.get("SPY", {}).get("correlation_90d")
    regime_notes = []
    if dxy_corr is not None and dxy_corr < -0.4:
        regime_notes.append("strong dollar headwind — DXY and crypto moving opposite, dollar strength hurts")
    if spy_corr is not None and spy_corr > 0.6:
        regime_notes.append("trading as a risk asset — moving with stocks, macro sentiment dominant")
    elif spy_corr is not None and spy_corr < 0.2:
        regime_notes.append("decoupled from equities — acting as independent asset class")

    result = {
        "symbol": symbol.upper(),
        "period": "90 days",
        "correlations": correlations,
        "macro_regime_notes": regime_notes,
        "note": "Pearson correlation: 1.0 = perfect sync, -1.0 = perfect inverse, 0 = no relationship.",
        "source": "yahoo_finance (computed)",
    }
    cache.set(cache_key, result, CACHE_TTL_CORRELATIONS)
    return result

"""Prediction-market odds from Polymarket (Gamma public-search, free, no key).

Prediction markets price real-money probabilities for future events — a useful
cross-check on model-implied reads. When the TerminalQ Fed-path tool says one
thing and Polymarket's "rate cut by X" market says another, that divergence is
itself a signal: someone is wrong, and it is worth knowing who.
"""

import json

import httpx

from terminalq import cache
from terminalq.config import CACHE_TTL_PREDICTION_MARKETS, PREDICTION_MARKETS_LIMIT
from terminalq.logging_config import log

SEARCH_URL = "https://gamma-api.polymarket.com/public-search"


def _parse_yes_probability(market: dict) -> float | None:
    """Implied probability of the 'Yes' outcome as a percent, or None.

    Polymarket encodes ``outcomes`` and ``outcomePrices`` as JSON-string lists,
    e.g. ``'["Yes","No"]'`` and ``'["0.7885","0.2115"]'``. The first price is the
    probability of the first outcome (conventionally "Yes").
    """
    raw_prices = market.get("outcomePrices")
    if not raw_prices:
        return None
    try:
        prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
        return round(float(prices[0]) * 100, 1)
    except (json.JSONDecodeError, ValueError, IndexError, TypeError):
        return None


def _signal(markets: list[dict], topic: str) -> str:
    if not markets:
        return f"No active Polymarket markets found for '{topic}'."
    top = markets[0]
    return (
        f"Top '{topic}' market — \"{top['question']}\" — prices the 'Yes' outcome at "
        f"{top['implied_probability_pct']}% (${top['volume_usd']:,.0f} traded). "
        "Compare against the model-implied read; large gaps flag a mispricing on one side."
    )


async def get_prediction_markets(topic: str = "Fed rate") -> dict:
    """Get real-money event probabilities from Polymarket for a topic.

    Args:
        topic: Free-text query, e.g. "Fed rate", "recession 2026", "CPI".

    Returns:
        Dict with the top matching markets (question, implied Yes probability,
        volume, end date), a plain-English signal, or an error dict if the
        Polymarket Gamma API is unreachable.
    """
    cache_key = f"prediction_markets_{topic.lower().strip()}"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                SEARCH_URL,
                params={"q": topic, "limit_per_type": PREDICTION_MARKETS_LIMIT},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        log.warning("Polymarket timeout")
        return {"error": "Request timed out", "source": "Polymarket"}
    except httpx.HTTPStatusError as e:
        log.warning("Polymarket HTTP %d", e.response.status_code)
        return {"error": f"HTTP {e.response.status_code}", "source": "Polymarket"}
    except httpx.HTTPError as e:
        log.error("Polymarket connection failed: %s", e)
        return {"error": "Connection failed", "source": "Polymarket"}

    markets: list[dict] = []
    for event in data.get("events", []):
        for market in event.get("markets", []):
            prob = _parse_yes_probability(market)
            if prob is None:
                continue
            try:
                volume = float(market.get("volume") or event.get("volume") or 0)
            except (ValueError, TypeError):
                volume = 0.0
            markets.append(
                {
                    "question": market.get("question") or event.get("title") or "?",
                    "implied_probability_pct": prob,
                    "volume_usd": round(volume, 0),
                    "ends": market.get("endDate") or event.get("endDate"),
                }
            )

    markets.sort(key=lambda m: m["volume_usd"], reverse=True)
    markets = markets[:PREDICTION_MARKETS_LIMIT]

    result = {
        "topic": topic,
        "markets": markets,
        "signal": _signal(markets, topic),
        "note": (
            "Implied probability = the market's price for the 'Yes' outcome (0-100%). "
            "These are real-money bets, not forecasts — liquid markets (higher volume) "
            "are more trustworthy. Use as a cross-check on model-implied probabilities."
        ),
        "source": "Polymarket (Gamma public-search)",
    }
    cache.set(cache_key, result, CACHE_TTL_PREDICTION_MARKETS)
    return result

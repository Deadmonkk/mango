"""Hyperliquid derivatives fallback — funding rates and open interest when
CoinGecko's ``/derivatives`` aggregate is unavailable.

CoinGecko aggregates perpetual funding and open interest across many centralized
exchanges. The keyless, US-reachable alternatives are single-venue, and
Hyperliquid is the deepest of them. Its public ``info`` endpoint
(``metaAndAssetCtxs``) returns, for every listed perp, the current funding rate
and open interest with no API key.

The trade-off is fidelity: this is **one venue**, not a market-wide average, so
the caller tags the source accordingly. Funding on Hyperliquid is an *hourly*
rate expressed as a fraction (e.g. ``0.0000125`` = 0.00125%/hr); we convert to
the project's ``%/8h`` convention so the same signal thresholds apply.

Provider contract: never raises — returns ``None`` on any failure so the caller
can fall through to its existing CoinGecko error.
"""

import httpx
from terminalq.logging_config import log

INFO_URL = "https://api.hyperliquid.xyz/info"

# Hyperliquid funding is hourly; the project reports funding as percent per 8h.
_HOURS_PER_FUNDING_WINDOW = 8
_FRACTION_TO_PCT = 100


def _to_float(value: object) -> float | None:
    """Parse a Hyperliquid numeric string/number to float; ``None`` if unusable."""
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _normalize(meta: dict, asset_ctxs: list, focus: set[str]) -> dict[str, dict]:
    """Shape Hyperliquid's parallel (universe, contexts) arrays into the same
    intermediate structure CoinGecko's parser produces, so downstream
    aggregation and signal logic are shared:

        ``{"BTC": {"funding_rates": [pct_8h], "open_interests": [usd]}, ...}``
    """
    universe = meta.get("universe", []) if isinstance(meta, dict) else []
    out: dict[str, dict] = {}
    for i, asset in enumerate(universe):
        name = (asset.get("name") or "").upper() if isinstance(asset, dict) else ""
        if name not in focus or i >= len(asset_ctxs):
            continue
        ctx = asset_ctxs[i] or {}
        funding = _to_float(ctx.get("funding"))
        oi = _to_float(ctx.get("openInterest"))
        mark = _to_float(ctx.get("markPx"))
        if funding is None and oi is None:
            continue
        entry = out.setdefault(name, {"funding_rates": [], "open_interests": []})
        if funding is not None:
            entry["funding_rates"].append(round(funding * _HOURS_PER_FUNDING_WINDOW * _FRACTION_TO_PCT, 6))
        if oi is not None and mark is not None:
            entry["open_interests"].append(round(oi * mark, 0))  # coin units → USD
    return out


async def fetch_derivatives(focus: set[str]) -> dict[str, dict] | None:
    """Per-coin funding (%/8h) and open interest (USD) for the ``focus`` symbols
    from Hyperliquid, or ``None`` when Hyperliquid is unreachable / returns an
    unexpected shape.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(INFO_URL, json={"type": "metaAndAssetCtxs"}, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as e:  # provider contract: never raise
        log.warning("Hyperliquid derivatives fallback failed: %s", e)
        return None

    # metaAndAssetCtxs returns a 2-element array: [meta, [assetCtx, ...]].
    if not (isinstance(payload, list) and len(payload) == 2 and isinstance(payload[1], list)):
        log.warning("Hyperliquid returned unexpected payload shape")
        return None

    normalized = _normalize(payload[0], payload[1], focus)
    return normalized or None

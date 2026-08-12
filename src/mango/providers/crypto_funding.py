"""Open-interest-weighted perpetual funding rates, with a basis cross-check.

WHY THIS EXISTS
---------------
The previous funding input took an UNWEIGHTED MEAN across every perpetual
contract CoinGecko lists (~195 for BTC, 147+ venues). Open interest is extremely
concentrated, so that mean is dominated by dust: on 2026-08-05 a venue called
Ostium reported +9.5192%/8h on $412K of open interest — roughly 25x more extreme
than the most extreme ALTCOIN perp on any major exchange — and dragged the BTC
average to +0.0875%/8h (+95.76% annualised).

Verified against four independent robust methods that same day:
    Coinglass OI-weighted            +0.0023%/8h   (+2.52%/yr)
    Coinglass volume-weighted        +0.0031%/8h   (+3.40%/yr)
    CoinGecko median of 195          +0.0000%/8h   ( 0.00%/yr)
    CoinGecko OI-weighted >$1B OI    -0.0006%/8h   (-0.60%/yr)
    ---- vs ----
    CoinGecko UNWEIGHTED MEAN        +0.0875%/8h   (+95.76%/yr)  <- the bug

The distortion mattered: funding feeds 35% of the Crypto Regime Score (stress 20%
+ liquidation 15%), and the bogus reading pinned the liquidation leg at 0/100 and
produced a persistent "crowded long / squeeze risk" watch-item that did not exist.

METHOD
------
1. OI-weight across contracts, after excluding sub-threshold and out-of-band ones.
2. Cross-check against the observed perp-vs-spot PREMIUM. Funding is the mechanism
   that tethers a perp to spot, so a large funding rate REQUIRES a large sustained
   premium. If weighted funding and observed basis disagree, flag rather than score.

AVAILABILITY (added 2026-08-12)
-------------------------------
CoinGecko's ``/derivatives`` returned 429 on 2026-08-12 and the whole funding read
failed, dropping the liquidation leg (15% of the Crypto Regime Score) out of the
score and forcing a renormalisation. Two causes, both fixed:

* this module called CoinGecko with raw ``httpx``, bypassing ``core.coingecko._fetch``
  and therefore its rate limiter, 429 retry/backoff, and shared cache — so the BTC
  and ETH calls each hit the API cold and doubled the rate-limit pressure;
* there was no fallback, even though ``providers.hyperliquid`` is keyless,
  US-reachable, and was already being called in the same request for the basis check.

Hyperliquid is ONE venue, not a market-wide aggregate, so the fallback degrades
loudly: it tags ``funding_source``, carries the CoinGecko error, and verifies its
number against an INDEPENDENT venue's basis (Deribit) — never against Hyperliquid's
own premium, which would be the same venue checking itself.
"""
from __future__ import annotations

import asyncio

import httpx

from mango.core import cache
from mango.core.coingecko import _fetch
from mango.core.logging import log
from mango.providers import hyperliquid

# --- thresholds (named, not inline) --------------------------------------
MIN_CONTRACT_OI_USD = 1_000_000_000.0   # only venues with >$1B OI carry weight
MIN_SINGLE_VENUE_OI_USD = 100_000_000.0 # the $1B floor removes dust from a ~195-
                                        # contract aggregate; applied to the one
                                        # venue in the fallback it would reject the
                                        # only source available. Still a floor —
                                        # below this the venue is too thin to score.
OUTLIER_ABS_PCT_8H = 0.5                # beyond this per 8h a BTC quote is bad data,
                                        # not a market (worst major-venue ALTCOIN
                                        # funding observed was ~0.38%/8h)
PREMIUM_DISAGREEMENT_PP = 0.05          # weighted funding vs observed basis, per 8h
PERIODS_PER_YEAR_8H = 3 * 365
CACHE_TTL_FUNDING = 900                 # 15 min

_COINGECKO_DERIVATIVES = "https://api.coingecko.com/api/v3/derivatives"
_DERIBIT_TICKER = "https://www.deribit.com/api/v2/public/ticker?instrument_name=BTC-PERPETUAL"
_HYPERLIQUID_INFO = "https://api.hyperliquid.xyz/info"


def annualize_8h(pct_per_8h: float) -> float:
    """Simple (non-compounded) annualisation, matching industry convention."""
    return round(pct_per_8h * PERIODS_PER_YEAR_8H, 2)


def _weighted_funding(contracts: list[dict], min_oi_usd: float = MIN_CONTRACT_OI_USD) -> dict:
    """OI-weight funding across contracts, excluding dust and out-of-band quotes."""
    kept, dropped_small, dropped_outlier = [], 0, []
    for c in contracts:
        rate, oi = c.get("funding_rate"), c.get("open_interest")
        if rate is None or oi is None:
            continue
        try:
            rate, oi = float(rate), float(oi)
        except (TypeError, ValueError):
            continue
        if abs(rate) > OUTLIER_ABS_PCT_8H:
            dropped_outlier.append((c.get("market", "?"), rate, oi))
            continue
        if oi < min_oi_usd:
            dropped_small += 1
            continue
        kept.append((rate, oi))

    if not kept:
        return {"error": "no contracts above the open-interest threshold"}

    total_oi = sum(oi for _, oi in kept)
    weighted = sum(r * oi for r, oi in kept) / total_oi
    rates = sorted(r for r, _ in kept)
    median = rates[len(rates) // 2]
    return {
        "funding_8h_pct": round(weighted, 6),
        "funding_annualized_pct": annualize_8h(weighted),
        "median_8h_pct": round(median, 6),
        "venues_weighted": len(kept),
        "total_open_interest_usd": round(total_oi),
        "excluded_below_oi_threshold": dropped_small,
        "excluded_as_outliers": [
            {"market": m, "funding_8h_pct": r, "open_interest_usd": oi}
            for m, r, oi in dropped_outlier
        ],
    }


async def _observed_premium(client: httpx.AsyncClient) -> dict:
    """Perp-vs-spot basis from venues reachable without an API key.

    Binance and Bybit hold most global OI but geo-block US IPs, so they are not
    used. The premium check is venue-INTERNAL (mark vs that venue's own index),
    so it does not need global coverage to be valid.
    """
    out: list[dict] = []

    try:
        r = (await client.get(_DERIBIT_TICKER, timeout=15)).json()["result"]
        mark, index = float(r["mark_price"]), float(r["index_price"])
        out.append({"venue": "deribit", "premium_pct": round((mark - index) / index * 100, 5)})
    except Exception as e:
        log.warning("Deribit premium check failed: %s", e)

    try:
        resp = await client.post(_HYPERLIQUID_INFO, json={"type": "metaAndAssetCtxs"}, timeout=15)
        d = resp.json()
        i = next(i for i, a in enumerate(d[0]["universe"]) if a["name"] == "BTC")
        c = d[1][i]
        mark, oracle = float(c["markPx"]), float(c["oraclePx"])
        out.append({"venue": "hyperliquid", "premium_pct": round((mark - oracle) / oracle * 100, 5)})
    except Exception as e:
        log.warning("Hyperliquid premium check failed: %s", e)

    if not out:
        return {"error": "no premium source reachable"}
    prems = [v["premium_pct"] for v in out]
    return {"venues": out, "mean_premium_pct": round(sum(prems) / len(prems), 5)}


async def _coingecko_contracts(client: httpx.AsyncClient, symbol: str) -> tuple[list[dict], str]:
    """This asset's perpetual contracts from CoinGecko, or ``([], reason)``.

    Goes through the shared ``core.coingecko._fetch`` so it inherits the rate
    limiter, 429 retry/backoff, and cache. The raw ``httpx.get`` this replaced had
    none of those, so one 429 killed the read and the BTC and ETH calls each paid
    full price against the same rate limit.
    """
    raw = await _fetch(client, _COINGECKO_DERIVATIVES, {})
    if isinstance(raw, dict) and "_error" in raw:
        return [], str(raw["_error"])
    if not isinstance(raw, list):
        return [], "malformed /derivatives response"

    contracts = [c for c in raw if c.get("index_id") == symbol]
    if not contracts:
        return [], f"no {symbol} contracts in /derivatives response"
    return contracts, ""


async def _hyperliquid_contracts(symbol: str) -> list[dict]:
    """The same contract shape ``_weighted_funding`` consumes, from one venue.

    Hyperliquid is keyless and US-reachable, which the deepest CEX perp venues
    (Binance, Bybit) are not. One venue is not a market-wide average — the caller
    tags the result accordingly rather than passing it off as the aggregate.
    """
    data = await hyperliquid.fetch_derivatives({symbol})
    entry = (data or {}).get(symbol) or {}
    rates, ois = entry.get("funding_rates") or [], entry.get("open_interests") or []
    if not rates or not ois:
        return []
    return [{"market": "Hyperliquid", "funding_rate": rates[0], "open_interest": ois[0]}]


def _independent_premium(premium: dict, funding_source: str) -> float | None:
    """Mean observed basis from venues OTHER than the one that supplied funding.

    A venue's own premium cannot verify its own funding rate — that is the same
    measurement twice, not a cross-check. Returns ``None`` when no independent
    venue reported, so the caller can decline to claim verification.
    """
    others = [
        v.get("premium_pct")
        for v in premium.get("venues", [])
        if v.get("venue") != funding_source and isinstance(v.get("premium_pct"), (int, float))
    ]
    if not others:
        return None
    return sum(others) / len(others)


def _apply_cross_check(result: dict, premium: dict, funding_source: str) -> None:
    """Attach the basis cross-check to ``result``, in place.

    Funding is the mechanism tethering a perp to spot: a large rate requires a
    sustained premium. Disagreement means one of the two is unreliable — say so
    rather than scoring a number the basis cannot support.
    """
    mp = _independent_premium(premium, funding_source)
    if mp is None:
        result["basis_consistent"] = None
        result["cross_check"] = (
            f"unavailable — funding came from {funding_source} and no independent venue "
            f"reported a basis, so this rate is not independently verified"
        )
        return

    gap = abs(result["funding_8h_pct"] - mp)
    venues = ", ".join(
        str(v.get("venue")) for v in premium.get("venues", []) if v.get("venue") != funding_source
    )
    result["basis_consistent"] = gap <= PREMIUM_DISAGREEMENT_PP
    result["cross_check"] = (
        f"ok — OI-weighted funding {result['funding_8h_pct']:+.4f}%/8h vs observed "
        f"premium {mp:+.4f}% from {venues} ({gap:.4f}pp apart)"
        if result["basis_consistent"]
        else f"WARNING — funding {result['funding_8h_pct']:+.4f}%/8h is not supported by "
             f"the observed premium {mp:+.4f}% from {venues} ({gap:.4f}pp apart); "
             f"treat as unreliable"
    )


_AGGREGATE_NOTE = (
    "Open-interest-weighted across venues above the OI threshold; dust and "
    "out-of-band quotes excluded. Historical average is ~11%/yr annualised "
    "(~0.01%/8h). An UNWEIGHTED mean over all venues overstates this by "
    "an order of magnitude — see module docstring."
)
_FALLBACK_NOTE = (
    "FALLBACK: single-venue funding from Hyperliquid because the CoinGecko "
    "aggregate was unavailable. One venue is not a market-wide average — the "
    "level is directionally usable but do not read small differences from prior "
    "aggregate readings as market moves. Historical average is ~11%/yr annualised."
)


async def get_btc_funding(symbol: str = "BTC") -> dict:
    """OI-weighted perpetual funding for one asset, cross-checked against basis.

    Falls back to single-venue Hyperliquid data when the CoinGecko aggregate is
    unavailable, so a provider outage degrades the reading's fidelity instead of
    removing the funding input from the regime score entirely.
    """
    cache_key = f"crypto_funding_{symbol}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    async with httpx.AsyncClient() as client:
        (contracts, cg_error), premium = await asyncio.gather(
            _coingecko_contracts(client, symbol), _observed_premium(client)
        )

    funding_source = "coingecko"
    result = (
        _weighted_funding(contracts) if contracts else {"error": cg_error or "no contracts"}
    )

    if "error" in result:
        log.warning("CoinGecko funding unavailable (%s); trying Hyperliquid", cg_error or result["error"])
        single_venue = await _hyperliquid_contracts(symbol)
        fallback = (
            _weighted_funding(single_venue, min_oi_usd=MIN_SINGLE_VENUE_OI_USD)
            if single_venue
            else {"error": "hyperliquid returned no usable funding"}
        )
        if "error" not in fallback:
            result, funding_source = fallback, "hyperliquid"
        else:
            return {
                "error": f"coingecko: {cg_error or result['error']}; hyperliquid: {fallback['error']}",
                "source": "coingecko+venues",
            }

    result["symbol"] = symbol
    result["contracts_seen"] = len(contracts)
    result["funding_source"] = funding_source
    result["premium_check"] = premium
    _apply_cross_check(result, premium, funding_source)

    result["signal"] = _funding_signal(result["funding_annualized_pct"])
    if funding_source == "hyperliquid":
        result["note"] = _FALLBACK_NOTE
        result["coingecko_error"] = cg_error
        result["source"] = "hyperliquid /info (single venue) + deribit basis"
    else:
        result["note"] = _AGGREGATE_NOTE
        result["source"] = "coingecko /derivatives (OI-weighted) + deribit/hyperliquid basis"

    cache.set(cache_key, result, CACHE_TTL_FUNDING)
    return result


HISTORICAL_AVG_ANNUALIZED_PCT = 11.0
CROWDED_LONG_ANNUALIZED_PCT = 30.0
CAPITULATION_ANNUALIZED_PCT = -10.0


def _funding_signal(ann: float) -> str:
    if ann <= CAPITULATION_ANNUALIZED_PCT:
        return f"{ann:+.1f}%/yr — shorts paying longs: capitulation/washed-out positioning"
    if ann >= CROWDED_LONG_ANNUALIZED_PCT:
        return f"{ann:+.1f}%/yr — crowded long, well above the ~11%/yr norm: squeeze risk"
    if ann >= HISTORICAL_AVG_ANNUALIZED_PCT:
        return f"{ann:+.1f}%/yr — modestly above the ~11%/yr historical norm"
    return f"{ann:+.1f}%/yr — at or below the ~11%/yr historical norm: positioning not crowded"

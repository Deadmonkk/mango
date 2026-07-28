"""Dealer-gamma & options positioning from Yahoo option chains (free, no key).

Where price data shows *what* the market did, options positioning hints at *why*
it may be pinned or about to break. We approximate dealer gamma exposure (GEX)
from open interest and Black-Scholes gamma, and surface the practical outputs
traders actually use: the call wall (likely resistance), the put wall (likely
support), and the put/call positioning skew.

Yahoo's options endpoint is free but rate-limited and occasionally empty; this
provider degrades gracefully and labels data quality rather than failing hard.
The numbers are estimates, not a paid GEX feed — they are directional, not exact.
"""

import asyncio
import math
from datetime import datetime, timezone

from terminalq.logging_config import log

from terminalq import cache
from terminalq._lazy_yfinance import yfinance
from terminalq.ext_settings import CACHE_TTL_OPTIONS_GAMMA, OPTIONS_GAMMA_EXPIRIES

_SQRT_2PI = math.sqrt(2 * math.pi)


def _finite(value, cast):
    """Coerce a value to `cast`, mapping None/NaN/inf/garbage to cast(0).

    Yahoo option chains frequently return NaN for openInterest/impliedVolatility;
    ``int(float('nan'))`` raises ``ValueError``, which previously crashed the whole
    tool (providers must never raise). This keeps the conversion total.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return cast(0)
    if math.isnan(f) or math.isinf(f):
        return cast(0)
    return cast(f)


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _bs_gamma(spot: float, strike: float, t_years: float, iv: float) -> float:
    """Black-Scholes gamma (rate≈0). Returns 0 on degenerate inputs."""
    if spot <= 0 or strike <= 0 or t_years <= 0 or iv <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + 0.5 * iv * iv * t_years) / (iv * math.sqrt(t_years))
    return _norm_pdf(d1) / (spot * iv * math.sqrt(t_years))


def _years_to_expiry(expiry: str) -> float:
    try:
        exp = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        days = (exp - datetime.now(timezone.utc)).total_seconds() / 86400
        return max(days, 0.5) / 365.0  # floor at half a day to avoid div-by-zero
    except (ValueError, TypeError):
        return 0.0


def _gamma_signal(net_gex: float, spot: float, call_wall: float | None, put_wall: float | None) -> str:
    if net_gex > 0:
        regime = (
            "POSITIVE net dealer gamma — dealers hedge against the trend (buy dips, sell rips), "
            "which dampens volatility and pins price toward the call wall"
        )
    else:
        regime = (
            "NEGATIVE net dealer gamma — dealers hedge with the trend (sell dips, buy rips), "
            "which amplifies moves; air pockets and sharp swings are more likely"
        )
    walls = []
    if call_wall:
        walls.append(f"call wall ~${call_wall:,.0f} (resistance)")
    if put_wall:
        walls.append(f"put wall ~${put_wall:,.0f} (support)")
    wall_txt = "; ".join(walls)
    return f"{regime}. Spot ${spot:,.2f}. {wall_txt}." if wall_txt else f"{regime}. Spot ${spot:,.2f}."


def _fetch_positioning(symbol: str) -> dict:
    """Blocking yfinance work; run via asyncio.to_thread. Returns result/error dict."""
    t = yfinance.Ticker(symbol)
    try:
        hist = t.history(period="1d", interval="1d")
        if hist.empty:
            return {"error": "No price data", "source": "yahoo_finance (options)"}
        spot = float(hist["Close"].dropna().iloc[-1])
    except Exception as e:  # provider contract: never raise
        log.warning("options spot fetch failed for %s: %s", symbol, e)
        return {"error": "Spot price unavailable", "source": "yahoo_finance (options)"}

    try:
        expiries = list(t.options)[:OPTIONS_GAMMA_EXPIRIES]
    except Exception as e:
        log.warning("options expiries failed for %s: %s", symbol, e)
        return {"error": "Options chain unavailable (Yahoo rate limit?)", "source": "yahoo_finance (options)"}
    if not expiries:
        return {"error": "No option expiries listed", "source": "yahoo_finance (options)"}

    call_oi: dict[float, int] = {}
    put_oi: dict[float, int] = {}
    net_gex = 0.0
    contracts_seen = 0

    for expiry in expiries:
        try:
            chain = t.option_chain(expiry)
        except Exception as e:
            log.warning("option_chain failed for %s %s: %s", symbol, expiry, e)
            continue
        t_years = _years_to_expiry(expiry)
        for df, sign, oi_map in ((chain.calls, 1.0, call_oi), (chain.puts, -1.0, put_oi)):
            for row in df.itertuples(index=False):
                strike = _finite(getattr(row, "strike", 0), float)
                oi = _finite(getattr(row, "openInterest", 0), int)
                iv = _finite(getattr(row, "impliedVolatility", 0), float)
                if strike <= 0 or oi <= 0:
                    continue
                oi_map[strike] = oi_map.get(strike, 0) + oi
                contracts_seen += 1
                # Dealer-gamma sign convention: long calls (+), short puts (−).
                gamma = _bs_gamma(spot, strike, t_years, iv)
                net_gex += sign * gamma * oi * 100 * spot

    if contracts_seen == 0:
        return {"error": "Option chains returned no open interest", "source": "yahoo_finance (options)"}

    call_wall = max(call_oi, key=call_oi.get) if call_oi else None
    put_wall = max(put_oi, key=put_oi.get) if put_oi else None
    total_call_oi = sum(call_oi.values())
    total_put_oi = sum(put_oi.values())
    pc_ratio = round(total_put_oi / total_call_oi, 2) if total_call_oi else None

    return {
        "symbol": symbol.upper(),
        "spot": round(spot, 2),
        "expiries_analyzed": expiries,
        "net_dealer_gamma": round(net_gex, 0),
        "net_gamma_regime": "positive" if net_gex > 0 else "negative",
        "call_wall": call_wall,
        "put_wall": put_wall,
        "put_call_oi_ratio": pc_ratio,
        "signal": _gamma_signal(net_gex, spot, call_wall, put_wall),
        "note": (
            "Estimates from free Yahoo option chains, not a paid GEX feed — directional, "
            "not exact. Call wall = strike with most call open interest (acts as resistance); "
            "put wall = most put OI (support). Net dealer gamma uses Black-Scholes gamma × OI "
            "with a long-call/short-put dealer convention; positive = volatility-dampening, "
            "negative = volatility-amplifying. P/C OI ratio >1 = heavier downside hedging."
        ),
        "source": "yahoo_finance (options, computed)",
    }


async def get_dealer_gamma(symbol: str = "SPY") -> dict:
    """Get dealer-gamma positioning (call/put walls, net GEX sign) for a symbol.

    Args:
        symbol: Ticker with a liquid options market (e.g. SPY, QQQ, SPX-proxy).

    Returns:
        Dict with spot, net dealer-gamma sign, call/put walls, put-call OI ratio,
        and a plain-English signal — or an error dict if Yahoo's options endpoint
        is rate-limited or empty.
    """
    cache_key = f"dealer_gamma_{symbol.lower()}"
    cached = cache.get(cache_key)
    if cached:
        log.debug("Cache hit: %s", cache_key)
        return cached

    try:
        result = await asyncio.to_thread(_fetch_positioning, symbol)
    except Exception as e:  # provider contract: never raise, always return a dict
        log.warning("dealer_gamma failed for %s: %s", symbol, e)
        return {
            "error": f"Options positioning unavailable ({type(e).__name__})",
            "symbol": symbol.upper(),
            "source": "yahoo_finance (options)",
        }
    if "error" not in result:
        cache.set(cache_key, result, CACHE_TTL_OPTIONS_GAMMA)
    return result

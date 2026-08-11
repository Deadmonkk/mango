"""EOD-specific table renderers.

FR's Field/Section spec covers flat "one metric per row" tables. EOD's core
content is different in shape — ranked lists of quotes, an asset-class grid, and
a volatility-derived expected range — so those get purpose-built renderers here
rather than being forced into the Field abstraction.

Everything is computed from provider results. In particular the movers list is
ranked in code from `percent_change`, not chosen by the model: "top gainers"
should be a sort, not a judgement.
"""
from __future__ import annotations

from typing import Any

from fr_render import FAIL, is_num

# A day's move is only a "leader"/"laggard" against the rest of the set; these
# thresholds label the tails without pretending to a statistical claim.
BIG_MOVE_PCT = 2.0
MOVERS_SHOWN = 8


def _pct(v: Any) -> str:
    return f"{float(v):+.2f}%" if is_num(v) else "—"


def _price(v: Any) -> str:
    return f"{float(v):,.2f}" if is_num(v) else "—"


def _quote_rows(quotes: list) -> list[dict]:
    return [q for q in quotes if isinstance(q, dict) and is_num(q.get("percent_change"))]


def _move_read(pct: float, rank: int, total: int) -> str:
    if rank == 0:
        return f"day's leader{' — outsized move' if abs(pct) >= BIG_MOVE_PCT else ''}"
    if rank == total - 1:
        return f"day's laggard{' — outsized move' if abs(pct) >= BIG_MOVE_PCT else ''}"
    if abs(pct) >= BIG_MOVE_PCT:
        return "outsized move — check for name-specific news"
    return ""


def render_scoreboard(quotes: Any, label: str = "Ticker") -> str:
    """All quotes ranked by today's percent change, best first."""
    rows = _quote_rows(quotes if isinstance(quotes, list) else [])
    if not rows:
        return f"{label} scoreboard: {FAIL}"
    ranked = sorted(rows, key=lambda q: float(q["percent_change"]), reverse=True)
    out = [f"| {label} | Last | Today % | Read |", "|---|---|---|---|"]
    for i, q in enumerate(ranked):
        pct = float(q["percent_change"])
        out.append(
            f"| {q.get('symbol', '?')} | {_price(q.get('current_price'))} | "
            f"{_pct(pct)} | {_move_read(pct, i, len(ranked))} |"
        )
    return "\n".join(out)


def render_movers(quotes: Any, shown: int = MOVERS_SHOWN) -> str:
    """Top gainers and losers, ranked in code rather than picked by the model."""
    rows = _quote_rows(quotes if isinstance(quotes, list) else [])
    if not rows:
        return f"Movers: {FAIL}"
    ranked = sorted(rows, key=lambda q: float(q["percent_change"]), reverse=True)
    # A universe smaller than 2*shown would otherwise list the same name as both
    # a gainer and a loser; split it down the middle instead. Below two names
    # there is nothing to split, so the sign of the move decides the label.
    if len(ranked) < 2:
        gainers = [q for q in ranked if float(q["percent_change"]) >= 0]
        losers = [q for q in ranked if float(q["percent_change"]) < 0]
    else:
        half = min(shown, len(ranked) // 2)
        gainers, losers = ranked[:half], ranked[len(ranked) - half:][::-1]
    out = ["| | Ticker | Last | Today % |", "|---|---|---|---|"]
    for q in gainers:
        out.append(f"| gainer | {q.get('symbol', '?')} | {_price(q.get('current_price'))} | "
                   f"{_pct(q.get('percent_change'))} |")
    for q in losers:
        out.append(f"| loser | {q.get('symbol', '?')} | {_price(q.get('current_price'))} | "
                   f"{_pct(q.get('percent_change'))} |")
    return "\n".join(out)


def render_asset_classes(payload: Any) -> str:
    """Cross-asset returns grid, ordered by the 1-month column."""
    classes = (payload or {}).get("asset_classes") if isinstance(payload, dict) else None
    if not isinstance(classes, dict) or not classes:
        return f"Asset-class returns: {FAIL}"
    # One malformed entry must not abort the whole report, so drop non-dicts
    # rather than letting the sort key raise on them.
    usable = {k: v for k, v in classes.items() if isinstance(v, dict)}
    if not usable:
        return f"Asset-class returns: {FAIL}"
    items = sorted(
        usable.items(),
        key=lambda kv: kv[1].get("1mo") if is_num(kv[1].get("1mo")) else -999,
        reverse=True,
    )
    out = ["| Asset class | Last | 1mo | 3mo | YTD | 1y |", "|---|---|---|---|---|---|"]
    for sym, d in items:
        out.append(
            f"| {d.get('name', sym)} ({sym}) | {_price(d.get('current'))} | "
            f"{_pct(d.get('1mo'))} | {_pct(d.get('3mo'))} | {_pct(d.get('ytd'))} | {_pct(d.get('1y'))} |"
        )
    return "\n".join(out)


def render_crypto_movers(payload: Any) -> str:
    """Tracked coins ranked by 24h move."""
    rows = [c for c in (payload or []) if isinstance(c, dict) and is_num(c.get("price_change_pct_24h"))]
    if not rows:
        return f"Crypto movers: {FAIL}"
    ranked = sorted(rows, key=lambda c: float(c["price_change_pct_24h"]), reverse=True)
    out = ["| Coin | Price | 24h | 7d | 30d |", "|---|---|---|---|---|"]
    for c in ranked:
        out.append(
            f"| {c.get('symbol', '?')} | {_price(c.get('current_price'))} | "
            f"{_pct(c.get('price_change_pct_24h'))} | {_pct(c.get('price_change_pct_7d'))} | "
            f"{_pct(c.get('price_change_pct_30d'))} |"
        )
    return "\n".join(out)


# Indices whose expected range is scaled from SPY's ATR. SPY is the only symbol
# the collector pulls full technicals for, so the others are explicitly derived.
RANGE_INDICES = (("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq"), ("^RUT", "Russell 2000"), ("^DJI", "Dow"))


def _level(markets: dict, symbol: str) -> Any:
    """Last level for a symbol, tolerating a malformed entry rather than raising."""
    entry = markets.get(symbol)
    return entry.get("current") if isinstance(entry, dict) else None


def render_expected_ranges(technicals: Any, market_overview: Any) -> str:
    """Tomorrow's expected range from ATR — a volatility band, not a forecast.

    ATR(14) is the average true range of the last 14 sessions. Treating it as a
    one-day band says "a move this size is ordinary", which is the honest
    version of a next-day expectation. Only SPY has measured technicals; the
    other indices scale SPY's ATR-as-percent and are labelled as derived.
    """
    atr = (technicals or {}).get("atr", {}).get("atr") if isinstance(technicals, dict) else None
    spy_price = (technicals or {}).get("price") if isinstance(technicals, dict) else None
    if not is_num(atr):
        return f"Expected ranges: {FAIL}"
    markets = (market_overview or {}).get("markets", {}) if isinstance(market_overview, dict) else {}
    if not isinstance(markets, dict):
        markets = {}
    spy_level = spy_price if is_num(spy_price) else _level(markets, "SPY")
    if not is_num(spy_level) or float(spy_level) == 0:
        return f"Expected ranges: {FAIL} (no SPY level to scale ATR against)"
    atr_pct = float(atr) / float(spy_level) * 100

    out = [f"*Bands are ±1 ATR(14) = ±{atr_pct:.2f}% of price — an ordinary day's range, "
           "explicitly NOT a directional forecast. Only SPY's ATR is measured; index bands "
           "are scaled from it.*",
           "",
           "| Index | Close | Expected low | Expected high | Basis |",
           "|---|---|---|---|---|"]
    for sym, name in RANGE_INDICES:
        level = _level(markets, sym)
        if not is_num(level):
            out.append(f"| {name} | — | — | — | {FAIL} |")
            continue
        band = float(level) * atr_pct / 100
        out.append(
            f"| {name} | {_price(level)} | {_price(float(level) - band)} | "
            f"{_price(float(level) + band)} | scaled from SPY ATR |"
        )
    out.append(
        f"| SPY | {_price(spy_level)} | {_price(float(spy_level) - float(atr))} | "
        f"{_price(float(spy_level) + float(atr))} | measured ATR(14) = {float(atr):.2f} |"
    )
    return "\n".join(out)

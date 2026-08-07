#!/usr/bin/env python3
"""
adv.py — average-daily-volume helper for sizing flow against liquidity.

WHY THIS EXISTS
---------------
A claim about a large forced flow (an index rebalance, a buyback, a convertible
hedge) only becomes a finding once it is sized against ADV (average daily
volume). Third-party aggregator ADV figures are frequently wrong — in testing,
one aggregator understated a mid-cap's ADV by 68% ($31M reported vs $52M
computed from the tape), which makes every flow look far scarier relative to
liquidity than it actually is.

This computes ADV from raw OHLCV via the same provider the MCP tools use, so
the result is derived from primary data rather than a secondary aggregator. It
also runs outside the model context: pulling 3 months of daily bars for five
tickers through MCP costs ~25k tokens of JSON; this returns a table instead.

WHY MEDIAN, NOT JUST MEAN
-------------------------
Index-rebalance days and earnings days produce volume prints many multiples of
a normal day — a name with a ~1.3M-share median can print 8M+ shares on a
single rebalance day. Those days drag the mean above what the stock actually
absorbs on an ordinary day, so a flow sized against the mean understates how
disruptive it would be. Both are reported, plus a spikiness ratio (max/median)
that says how much the distinction matters for that name. Prefer the median.

USAGE
-----
    uv run --directory /path/to/terminalq python scripts/adv.py AAPL MSFT NVDA
    uv run --directory /path/to/terminalq python scripts/adv.py NVDA --period 6mo
    uv run --directory /path/to/terminalq python scripts/adv.py MSFT --flow 1.0e9
    uv run --directory /path/to/terminalq python scripts/adv.py --watchlist
    uv run --directory /path/to/terminalq python scripts/adv.py AAPL --json

--flow sizes a dollar flow against each ticker's ADV, reporting both the
multiple and the days-of-volume, computed on the MEDIAN (the conservative read).

CAVEATS THE OUTPUT REPEATS (do not strip them)
----------------------------------------------
  * Yahoo consolidated-tape volume, including off-exchange prints — good for
    order-of-magnitude flow work, not a substitute for a filing.
  * ADV is backward-looking. On the day a forced flow actually hits, the tape
    is not the calm tape this average was computed from, so days-of-volume
    understates real disruption on spiky names.
  * On a source failure the row reads "data unavailable (source failed)" —
    never a stale or interpolated number: a missing figure is reported as
    missing rather than filled in.
"""

import argparse
import asyncio
import json
import os
import statistics
import sys

from mango.core.historical import get_historical

# Tickers used by --watchlist. Set the ADV_WATCHLIST env var (comma-separated)
# to your own list, e.g. ADV_WATCHLIST="AAPL,MSFT,NVDA". The default below is
# only an illustrative example.
DEFAULT_WATCHLIST = ["AAPL", "MSFT", "NVDA", "JPM", "XOM"]
WATCHLIST = [t.strip().upper() for t in os.getenv("ADV_WATCHLIST", ",".join(DEFAULT_WATCHLIST)).split(",") if t.strip()]

DEFAULT_PERIOD = "3mo"
RECENT_WINDOW = 20  # trading days ≈ 1 month, for the trend check
UNAVAILABLE = "data unavailable (source failed)"


def _summarize(symbol: str, payload: dict) -> dict:
    """Reduce a provider payload to ADV statistics. Never invents a value."""
    if not payload or payload.get("error"):
        return {"symbol": symbol, "error": payload.get("error", UNAVAILABLE) if payload else UNAVAILABLE}

    prices = payload.get("prices") or []
    bars = [p for p in prices if p.get("volume") and p.get("close")]
    if not bars:
        return {"symbol": symbol, "error": UNAVAILABLE}

    volumes = [b["volume"] for b in bars]
    dollars = [b["volume"] * b["close"] for b in bars]
    recent = dollars[-RECENT_WINDOW:]

    median_volume = statistics.median(volumes)
    return {
        "symbol": symbol,
        "days": len(bars),
        "start": bars[0]["date"],
        "end": bars[-1]["date"],
        "dollar_adv_mean": statistics.mean(dollars),
        "dollar_adv_median": statistics.median(dollars),
        "dollar_adv_recent": statistics.mean(recent),
        "share_adv_mean": statistics.mean(volumes),
        "share_adv_median": median_volume,
        "spikiness": max(volumes) / median_volume if median_volume else None,
        "source": payload.get("source", "unknown"),
    }


async def collect(symbols: list[str], period: str, interval: str) -> list[dict]:
    """Fetch every symbol concurrently; a single failure never kills the run."""
    tasks = [get_historical(symbol, period=period, interval=interval) for symbol in symbols]
    payloads = await asyncio.gather(*tasks, return_exceptions=True)

    rows = []
    for symbol, payload in zip(symbols, payloads):
        if isinstance(payload, Exception):
            rows.append({"symbol": symbol, "error": f"{UNAVAILABLE}: {payload}"})
            continue
        rows.append(_summarize(symbol, payload))
    return rows


def _millions(value: float) -> str:
    return f"${value / 1e6:,.2f}M"


def render_table(rows: list[dict], period: str, flow: float | None) -> str:
    ok = [r for r in rows if "error" not in r]
    failed = [r for r in rows if "error" in r]

    lines = []
    header = f"{'TKR':<6} {'$ADV mean':>12} {'$ADV MEDIAN':>13} {'$ADV 20d':>12} {'sh ADV med':>12} {'spiky':>7}"
    lines.append(header)
    lines.append("-" * len(header))
    for r in ok:
        lines.append(
            f"{r['symbol']:<6} {_millions(r['dollar_adv_mean']):>12} "
            f"{_millions(r['dollar_adv_median']):>13} {_millions(r['dollar_adv_recent']):>12} "
            f"{r['share_adv_median']:>12,.0f} {r['spikiness']:>6.1f}x"
        )
    for r in failed:
        lines.append(f"{r['symbol']:<6} {r['error']}")

    if ok:
        sample = ok[0]
        lines.append("")
        lines.append(f"window: {period} — {sample['days']} bars, {sample['start']} to {sample['end']}")

    if flow and ok:
        lines.append("")
        lines.append(f"Sizing ${flow / 1e6:,.0f}M of flow against MEDIAN $ADV (the conservative read):")
        for r in ok:
            median = r["dollar_adv_median"]
            lines.append(f"  {r['symbol']:<6} {flow / median:>7.1f}x ADV  =  {flow / median:>6.1f} days of volume")

    lines.append("")
    lines.append("Prefer the MEDIAN for sizing — the mean is inflated by rebalance/earnings prints.")
    lines.append("Spiky names (>3x) disrupt more than days-of-volume implies: the flow day is not this average day.")
    lines.append("Yahoo consolidated tape (incl. off-exchange prints); order-of-magnitude, not a filing.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute ADV from OHLCV for forced-flow sizing.")
    parser.add_argument("symbols", nargs="*", help="Ticker symbols (omit with --watchlist)")
    parser.add_argument(
        "--watchlist", action="store_true", help=f"Use the configured watchlist: {', '.join(WATCHLIST)}"
    )
    parser.add_argument(
        "--period", default=DEFAULT_PERIOD, help="Lookback: 1mo, 3mo, 6mo, 1y, 2y, 5y, max (default 3mo)"
    )
    parser.add_argument("--interval", default="1d", help="Bar interval (default 1d)")
    parser.add_argument("--flow", type=float, help="Dollar flow to size against ADV, e.g. 1.0e9")
    parser.add_argument("--json", action="store_true", help="Emit raw JSON instead of the table")
    args = parser.parse_args()

    symbols = [s.upper() for s in args.symbols]
    if args.watchlist:
        symbols = WATCHLIST + [s for s in symbols if s not in WATCHLIST]
    if not symbols:
        parser.error("give at least one ticker, or --watchlist")

    rows = asyncio.run(collect(symbols, args.period, args.interval))

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(render_table(rows, args.period, args.flow))

    return 0 if any("error" not in r for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())

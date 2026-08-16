"""Verify a run's headline figures against their PRIMARY sources, independently.

WHY THIS EXISTS
---------------
``fr_verify_prose.py`` proves the prose matches the tables. Nothing proved the
*tables* match reality. A report can be perfectly self-consistent and still be
wrong everywhere, because every figure in it came through one code path: the
mango provider layer, its resolution rules, its caching and its local archive.
A bug or a stale cache anywhere in that path is invisible to an internal check —
the report would agree with itself all the way down.

So this re-fetches a sample of headline figures over a DIFFERENT path: raw HTTP
to the FRED API and to Yahoo's chart endpoint, with series IDs written here, its
own parsing, and no mango provider code in the call stack. Then it diffs against
``fr_values_<date>.json``. Agreement across two independent paths is real
evidence; agreement within one path is not.

FAIL vs WARNING
---------------
A genuine MISMATCH is a FAIL — the pipeline and the primary source disagree
about a number, and the report should not be published.

An unreachable check is a WARNING. Yahoo rate-limits aggressively and FRED has
outages; treating "I could not verify" as "this is wrong" would block reports on
the verifier's own connectivity, which is exactly the false alarm that trains a
reader to ignore the check.

USAGE
-----
    uv run python scripts/fr_verify_sources.py --values <fr_values_DATE.json>
    uv run python scripts/fr_verify_sources.py --values ... --json

Exits non-zero only on a genuine mismatch, so it can gate a workflow.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

from mango.core import env

env.load_env()

import os  # noqa: E402  - after load_env so the dotfile is in os.environ

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
TIMEOUT = 20.0

# Relative tolerance for "these agree". Generous enough to absorb a one-session
# difference in which observation each path considers latest, tight enough that
# a genuinely different number cannot slip through.
REL_TOLERANCE = 0.01

# Only figures with a DIRECT series mapping are checked. Derived values (CPI m/m
# as a percent, "SPY vs 200d" as a gap, the regime scores) are deliberately out
# of scope: re-deriving them here would reimplement the pipeline's arithmetic and
# then test this file's copy of it, which proves nothing about the data.
FRED_CHECKS: dict[str, str] = {
    "Unemployment": "UNRATE",
    "Initial claims": "ICSA",
    "10y Treasury": "DGS10",
    "2y Treasury": "DGS2",
    "HY spread": "BAMLH0A0HYM2",
    "IG spread": "BAMLC0A0CM",
    "CPI (index, SA)": "CPIAUCSL",
    "Core CPI (index, SA)": "CPILFESL",
    "Real GDP": "GDPC1",
    "Consumer sentiment": "UMCSENT",
    "Personal saving rate": "PSAVERT",
    "Term premium (Kim-Wright)": "THREEFYTP10",
    "Total nonfarm employment (level, 000s)": "PAYEMS",
    "Federal debt / GDP": "GFDEGDQ188S",
}

YAHOO_CHECKS: dict[str, str] = {
    "S&P 500": "^GSPC",
    "VIX": "^VIX",
}


class Unreachable(Exception):
    """The primary source could not be read — a warning, never a failure."""


def _fred_latest(series_id: str) -> tuple[float, str]:
    """Latest non-missing observation for a series, straight from the FRED API."""
    key = os.environ.get("FRED_API_KEY", "")
    if not key:
        raise Unreachable("FRED_API_KEY not configured")
    params = {
        "series_id": series_id,
        "api_key": key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 12,
    }
    try:
        response = httpx.get(FRED_URL, params=params, timeout=TIMEOUT)
        response.raise_for_status()
        observations = response.json().get("observations", [])
    except Exception as exc:  # noqa: BLE001 - any transport error is "unreachable"
        raise Unreachable(f"{type(exc).__name__}: {exc}") from exc
    for observation in observations:
        value = observation.get("value", ".")
        if value not in (".", "", None):
            return float(value), observation.get("date", "")
    raise Unreachable("no non-missing observations returned")


def _yahoo_latest(symbol: str) -> tuple[float, str]:
    """Latest close from Yahoo's chart endpoint — keyless, and rate-limited."""
    try:
        response = httpx.get(
            YAHOO_URL.format(symbol=symbol),
            params={"interval": "1d", "range": "5d"},
            timeout=TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        meta = response.json()["chart"]["result"][0]["meta"]
    except Exception as exc:  # noqa: BLE001
        raise Unreachable(f"{type(exc).__name__}: {exc}") from exc
    price = meta.get("regularMarketPrice")
    if price is None:
        raise Unreachable("no regularMarketPrice in response")
    return float(price), "latest"


def _agree(reported: float, primary: float) -> bool:
    scale = max(abs(reported), abs(primary), 1e-9)
    return abs(reported - primary) <= scale * REL_TOLERANCE


def check(values: dict) -> dict:
    """Diff reported figures against primary sources. Returns a result record."""
    mismatches: list[dict] = []
    unreachable: list[dict] = []
    checked = 0
    skipped: list[str] = []

    for label, fetch, ident in [
        *((lbl, _fred_latest, sid) for lbl, sid in FRED_CHECKS.items()),
        *((lbl, _yahoo_latest, sym) for lbl, sym in YAHOO_CHECKS.items()),
    ]:
        reported = values.get(label)
        if reported is None or not isinstance(reported, (int, float)):
            # The figure was not produced this run (source failed upstream, or
            # the label changed). Not this checker's failure to report.
            skipped.append(f"{label} (not in this run's values)")
            continue
        try:
            primary, as_of = fetch(ident)
        except Unreachable as exc:
            unreachable.append({"metric": label, "source": ident, "reason": str(exc)})
            continue
        checked += 1
        if not _agree(float(reported), primary):
            mismatches.append(
                {
                    "metric": label,
                    "source": ident,
                    "reported": float(reported),
                    "primary": primary,
                    "primary_as_of": as_of,
                    "rel_diff": abs(float(reported) - primary) / max(abs(primary), 1e-9),
                }
            )

    return {
        "status": "FAILED" if mismatches else "PASS",
        "checked": checked,
        "mismatches": mismatches,
        "unreachable": unreachable,
        "skipped": skipped,
    }


def render(result: dict) -> None:
    if result["mismatches"]:
        print(f"SOURCE MISMATCHES ({len(result['mismatches'])}) — pipeline disagrees with primary:")
        for m in result["mismatches"]:
            print(
                f"   {m['metric']} [{m['source']}]: report {m['reported']:,.4g} "
                f"vs primary {m['primary']:,.4g} (as of {m['primary_as_of']}, "
                f"{m['rel_diff'] * 100:.2f}% apart)"
            )
    if result["unreachable"]:
        print(f"\nWARNING — could not verify ({len(result['unreachable'])}):")
        for u in result["unreachable"]:
            print(f"   {u['metric']} [{u['source']}]: {u['reason']}")
    if result["skipped"]:
        print(f"\nWARNING — not produced this run ({len(result['skipped'])}):")
        for s in result["skipped"]:
            print(f"   {s}")
    if not result["mismatches"]:
        print(
            f"source check: {result['checked']} figure(s) agree with primary sources "
            f"(FRED/Yahoo, independent path)."
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--values", required=True, type=Path, help="path to fr_values_<date>.json")
    ap.add_argument("--json", action="store_true", help="emit the result record as JSON")
    args = ap.parse_args()

    if not args.values.exists():
        print(f"no such values file: {args.values}")
        return 1

    result = check(json.loads(args.values.read_text()))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        render(result)
    return 1 if result["mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())

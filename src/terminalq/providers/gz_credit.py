"""Gilchrist-Zakrajsek credit spread + excess bond premium (Federal Reserve Board).

Extracted into its own module so it does not live inside upstream TerminalQ files.
Keyless, free, monthly since 1973.
"""
from __future__ import annotations

import csv
import io

import httpx

from terminalq import cache
from terminalq.ext_settings import CACHE_TTL_ONCHAIN
from terminalq.logging_config import log


# ---------------------------------------------------------------------------
# Gilchrist-Zakrajsek credit spread + excess bond premium (Federal Reserve Board)
#
# WHY: ICE Data Indices restricted every BAML* series on FRED to a rolling
# 3-year window in April 2026, so a CCC or BB percentile now ranks against ~3
# years and cannot support a claim about historical extremes. The GZ spread is
# built from bond-level data by Fed economists, published free and keyless with
# monthly history back to 1973 — four recessions of context, no ICE licence.
#
# gz_spread = the credit spread itself.
# ebp       = excess bond premium: the part NOT explained by expected default
#             risk, i.e. the compensation investors demand above fundamentals.
#             Positive and rising = credit conditions tightening beyond what
#             defaults justify, historically a leading recession signal.
# ---------------------------------------------------------------------------
_EBP_CSV_URL = "https://www.federalreserve.gov/econres/notes/feds-notes/ebp_csv.csv"


async def get_gz_credit_spread() -> dict:
    """GZ credit spread + excess bond premium, with percentile vs full history."""
    cache_key = "gz_credit_spread"
    cached = cache.get(cache_key)
    if cached:
        return cached

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(_EBP_CSV_URL, timeout=30)
            resp.raise_for_status()
            rows = list(csv.DictReader(io.StringIO(resp.text)))
    except Exception as e:
        log.warning("GZ/EBP fetch failed: %s", e)
        return {"error": str(e), "source": "federalreserve.gov"}

    def series(field: str) -> list[tuple[str, float]]:
        out = []
        for r in rows:
            v = r.get(field)
            if v not in (None, "", "NA"):
                try:
                    out.append((r["date"], float(v)))
                except ValueError:
                    continue
        return out

    gz, ebp = series("gz_spread"), series("ebp")
    if not gz:
        return {"error": "no gz_spread values parsed", "source": "federalreserve.gov"}

    def rank(vals: list[tuple[str, float]]) -> dict:
        nums = [v for _, v in vals]
        cur = nums[-1]
        pct = round(100 * sum(1 for v in nums if v < cur) / len(nums), 1)
        return {
            "latest": round(cur, 4),
            "latest_date": vals[-1][0],
            "percentile_since_start": pct,
            "observations": len(nums),
            "history_start": vals[0][0],
            "min": round(min(nums), 4),
            "max": round(max(nums), 4),
        }

    result = {"gz_spread": rank(gz)}
    if ebp:
        result["excess_bond_premium"] = rank(ebp)
        e = result["excess_bond_premium"]
        result["ebp_signal"] = (
            f"EBP {e['latest']} at {e['percentile_since_start']}th percentile of "
            f"{e['observations']} months since {e['history_start'][:4]} — "
            + ("elevated: investors demanding more than default risk alone justifies"
               if e["percentile_since_start"] >= 75
               else "subdued: credit priced at or below fundamentals"
               if e["percentile_since_start"] <= 25 else "mid-range")
        )
    result["note"] = ("Gilchrist-Zakrajsek credit spread and excess bond premium (the portion "
                      "not explained by expected default risk). Monthly since 1973. Free, keyless, "
                      "and NOT ICE-licensed — this is the long-history credit reference the "
                      "3-year BAML series can no longer provide.")
    result["source"] = "federalreserve.gov (FEDS Notes)"
    cache.set(cache_key, result, CACHE_TTL_ONCHAIN)
    return result

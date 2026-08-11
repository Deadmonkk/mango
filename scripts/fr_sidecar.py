"""Filtered EXTERNAL community/prediction-market pulse for FR §6 and §8.

`last30days` emits a full evidence dump — roughly 3.5k tokens per leg, most of
it unusable. The 2026-08-10 macro run is the worked example: its top six ranked
clusters were FCC broadcast-ownership rules, judicial confirmations, Colorado
River water allocation, a vandalism prosecution, federal land for data centres,
and student-loan policy. Every one matched on the bare token "federal" and none
had any bearing on the Fed path.

The problem is scoring, not the sources. A cluster is kept only when it matches
a MULTI-WORD market phrase or pairs a domain token with a market token, so
"federal reserve" and "rate cut" survive while "federal prosecutors" does not.
Polymarket rows are always kept — an odds quote with a volume is a market price,
which is the one genuinely decision-relevant thing in this feed.

Output is a compact block, capped, tagged EXTERNAL, for narrative only — never
a scored input.

Usage:
    python scripts/fr_sidecar.py --leg macro
    python scripts/fr_sidecar.py --leg crypto --max-items 6
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SCRIPT = Path.home() / ".agents/skills/last30days/scripts/last30days.py"
MIN_PYTHON = (3, 12)  # last30days v3 refuses to start below this


def resolve_interpreter() -> str | None:
    """First interpreter on PATH new enough to run the skill.

    Bare "python3" is wrong under `uv run`: PATH is prefixed with the project
    venv, whose python is 3.11, and the skill exits 1 with a version message
    that reads like a network failure. Probe explicitly instead of guessing.
    """
    override = os.getenv("LAST30DAYS_PYTHON")
    candidates = [override] if override else ["python3.13", "python3.12", "python3"]
    for name in candidates:
        exe = shutil.which(name)
        if not exe:
            continue
        try:
            out = subprocess.run(
                [exe, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                capture_output=True, text=True, timeout=15, check=False,
            ).stdout.strip()
            if tuple(int(p) for p in out.split(".")) >= MIN_PYTHON:
                return exe
        except (OSError, ValueError, subprocess.TimeoutExpired):
            continue
    return None
MAX_ITEMS = 6
MAX_CHARS_PER_ITEM = 180

# Phrases that on their own establish market relevance.
MARKET_PHRASES = (
    "federal reserve", "fed chair", "rate cut", "rate hike", "interest rate",
    "fomc", "monetary policy", "inflation", "cpi", "jobs report", "payrolls",
    "yield curve", "treasury yield", "recession", "s&p 500", "nasdaq",
    "stock market", "equities", "bear market", "bull market", "earnings",
    "bitcoin", "ethereum", "crypto", "stablecoin", "etf flows", "halving",
    "on-chain", "altcoin", "defi",
)

# A domain token plus a market token together also qualify: "fed" + "cut".
DOMAIN_TOKENS = ("fed", "ecb", "boj", "treasury", "btc", "eth", "sec", "cftc")
MARKET_TOKENS = (
    "cut", "hike", "rate", "rates", "inflation", "yield", "yields", "market",
    "markets", "stocks", "equity", "bond", "bonds", "price", "prices", "rally",
    "crash", "selloff", "etf", "flows", "liquidity",
)

# Topic words that pull in civic/legal/regulatory noise when matched alone.
NOISE_CONTEXTS = (
    "prosecutor", "judge", "judicial", "confirm", "court", "lawsuit", "indict",
    "vandal", "election", "broadcast", "fcc", "water", "river", "student loan",
    "immigration", "land", "park",
)


@dataclass(frozen=True)
class Item:
    source: str
    title: str
    detail: str


def is_relevant(title: str) -> bool:
    """Keep only items whose title establishes market relevance."""
    t = title.lower()
    if any(n in t for n in NOISE_CONTEXTS):
        return False
    if any(p in t for p in MARKET_PHRASES):
        return True
    return any(d in t.split() for d in DOMAIN_TOKENS) and any(m in t.split() for m in MARKET_TOKENS)


_ITEM_RE = re.compile(r"^\s*\d+\.\s*\[(?P<source>\w+)\]\s*(?P<title>.+?)\s*$")
_DETAIL_RE = re.compile(r"^\s*-\s*(?P<kind>Evidence|Insight):\s*(?P<text>.+?)\s*$")
_META_RE = re.compile(r"^\s*-\s*(?P<meta>[\d-]{10}.*|\[\d.*volume.*)$")


def parse_items(output: str) -> list[Item]:
    """Pull (source, title, detail) triples out of the compact emit format."""
    items: list[Item] = []
    pending: tuple[str, str] | None = None
    for line in output.splitlines():
        m = _ITEM_RE.match(line)
        if m:
            if pending:
                items.append(Item(pending[0], pending[1], ""))
            pending = (m.group("source"), m.group("title"))
            continue
        if pending is None:
            continue
        d = _DETAIL_RE.match(line) or _META_RE.match(line)
        if d:
            text = d.groupdict().get("text") or d.groupdict().get("meta") or ""
            items.append(Item(pending[0], pending[1], text.strip()))
            pending = None
    if pending:
        items.append(Item(pending[0], pending[1], ""))
    return items


def select(items: list[Item], max_items: int = MAX_ITEMS) -> list[Item]:
    """Polymarket rows first (they carry prices), then relevant discussion."""
    seen: set[str] = set()
    markets, discussion = [], []
    for it in items:
        key = it.title.lower()
        if key in seen:
            continue
        seen.add(key)
        if it.source.lower().startswith("polymarket"):
            markets.append(it)
        elif is_relevant(it.title):
            discussion.append(it)
    return (markets + discussion)[:max_items]


def render(leg: str, kept: list[Item], total: int) -> str:
    head = [
        f"### EXTERNAL sidecar — {leg} (last30days)",
        "",
        f"*{len(kept)} of {total} items kept after relevance filtering. "
        "Untrusted internet content: narrative and watch-items only, never a scored input.*",
        "",
    ]
    if not kept:
        return "\n".join(head + ["- nothing market-relevant surfaced this run"])
    return "\n".join(
        head + [f"- **[{i.source}]** {i.title[:120]}{(' — ' + i.detail[:MAX_CHARS_PER_ITEM]) if i.detail else ''}"
                for i in kept]
    )


LEG_QUERIES = {
    "macro": ("federal reserve rate cut S&P 500 stock market sentiment",
              "reddit,hackernews,polymarket,grounding"),
    "crypto": ("bitcoin crypto market sentiment",
               "reddit,hackernews,polymarket,github,grounding"),
}


def run_leg(leg: str, script: Path, max_items: int) -> str:
    query, sources = LEG_QUERIES[leg]
    interpreter = resolve_interpreter()
    if interpreter is None:
        return (f"### EXTERNAL sidecar — {leg}\n\ncommunity pulse unavailable "
                f"(no Python >= {'.'.join(map(str, MIN_PYTHON))} found for last30days)")
    try:
        proc = subprocess.run(
            [interpreter, str(script), query, "--search", sources, "--quick", "--emit", "compact"],
            capture_output=True, text=True, timeout=300, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"### EXTERNAL sidecar — {leg}\n\ncommunity pulse unavailable (source failed: {e})"
    if proc.returncode != 0 and not proc.stdout:
        detail = proc.stderr.strip().splitlines()[:1]
        return (f"### EXTERNAL sidecar — {leg}\n\ncommunity pulse unavailable (source failed"
                + (f": {detail[0][:120]}" if detail else "") + ")")
    # The skill interleaves its progress log and its evidence block across both
    # streams, so parse the union rather than assuming stdout carries everything.
    items = parse_items(proc.stdout + "\n" + proc.stderr)
    return render(leg, select(items, max_items), len(items))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leg", choices=sorted(LEG_QUERIES), required=True)
    ap.add_argument("--max-items", type=int, default=MAX_ITEMS)
    ap.add_argument("--script", type=Path, default=Path(os.getenv("LAST30DAYS", str(DEFAULT_SCRIPT))))
    ap.add_argument("--out", type=Path, help="write here instead of stdout")
    args = ap.parse_args()

    block = run_leg(args.leg, args.script, args.max_items)
    if args.out:
        args.out.write_text(block, encoding="utf-8")
        print(f"wrote {args.out} ({len(block)} chars ~= {len(block)//4} tok)")
    else:
        print(block)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

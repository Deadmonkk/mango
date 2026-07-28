"""Best-effort backfill of the snapshot store from existing FR markdown reports.

The report format has drifted over time (older reports put regime scores in prose,
newer ones in a table), so this is deliberately tolerant: it records whatever it
can confidently extract and silently skips the rest. Going forward the FR run
records clean structured snapshots directly — this only seeds the back-history.

Run once:  python -m terminalq.backfill "/path/to/reports"

The reports directory may also be set via the TERMINALQ_REPORTS_DIR env var.
"""

import os
import re
import sys
from pathlib import Path

from terminalq import history

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_SCORE_RE = {
    "equity_regime": re.compile(r"Equity Regime\D*?(\d+)\s*/\s*100", re.I),
    "crypto_regime": re.compile(r"Crypto Regime\D*?~?\s*(\d+)\s*/\s*100", re.I),
}
# label in a table row -> snapshot key. Value taken from the bolded "this run" cell.
_ROW_LABELS = {
    "spx": r"S&P 500",
    "vix": r"VIX",
    "gold": r"Gold",
    "wti": r"WTI",
    "hy_spread": r"HY spread",
    "stablecoin_supply_b": r"Stablecoin supply",
}


def _to_float(text: str) -> float | None:
    cleaned = text.replace("**", "").replace("$", "").replace(",", "").replace("~", "").replace("%", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    return float(m.group()) if m else None


def _bolded_cell(text: str, label: str) -> str | None:
    """The first **bolded** value in a table row whose first cell matches `label`."""
    row = re.search(rf"\|\s*\*?\*?{label}[^|]*\|[^|]*\|\s*\*\*([^*|]+)\*\*", text, re.I)
    return row.group(1) if row else None


def parse_report(path: Path) -> dict:
    """Extract a best-effort snapshot dict from one report file."""
    text = path.read_text(encoding="utf-8")
    date_match = _DATE_RE.search(path.name)
    snapshot: dict = {"snapshot_date": date_match.group(1) if date_match else None}

    for key, pattern in _SCORE_RE.items():
        m = pattern.search(text)
        if m:
            snapshot[key] = int(m.group(1))

    for key, label in _ROW_LABELS.items():
        cell = _bolded_cell(text, label)
        if cell:
            val = _to_float(cell)
            if val is not None:
                snapshot[key] = val

    # BTC / ETH combined row, e.g. "**$62,700 / $1,646 ...**"
    be = _bolded_cell(text, r"BTC ?/ ?ETH")
    if be and "/" in be:
        left, right = be.split("/", 1)
        snapshot["btc"], snapshot["eth"] = _to_float(left), _to_float(right)
    else:
        btc_cell = _bolded_cell(text, r"BTC")
        if btc_cell:
            snapshot["btc"] = _to_float(btc_cell)

    # Fear & Greed: table cell first, then prose fallback ("Fear & Greed 9").
    fg = _bolded_cell(text, r"(?:Crypto )?Fear")
    if not fg:
        prose = re.search(r"Fear\s*&\s*Greed[:\s]+(\d+)", text, re.I)
        fg = prose.group(1) if prose else None
    if fg:
        snapshot["fear_greed"] = _to_float(fg)

    return {k: v for k, v in snapshot.items() if v is not None or k == "snapshot_date"}


def backfill(reports_dir: Path) -> list[dict]:
    """Parse every *-fr.md report in the directory and record snapshots."""
    recorded = []
    for path in sorted(reports_dir.glob("*-fr.md")):
        parsed = parse_report(path)
        metric_count = len([k for k in parsed if k not in ("snapshot_date",)])
        if metric_count == 0:
            continue
        record = history.record_snapshot(**parsed)
        recorded.append(record)
    return recorded


if __name__ == "__main__":
    default_dir = Path(os.getenv("TERMINALQ_REPORTS_DIR", str(Path.home() / "market-reports")))
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else default_dir
    rows = backfill(directory)
    print(f"Backfilled {len(rows)} report(s) into the snapshot store.")
    for r in rows:
        metrics = ", ".join(f"{k}={v}" for k, v in r.items() if k not in ("date", "recorded_at"))
        print(f"  {r['date']}: {metrics}")

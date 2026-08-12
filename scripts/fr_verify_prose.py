"""Verify a finished report's PROSE against its own deterministic tables.

WHY THIS EXISTS
---------------
The collector writes the tables; the model writes only the prose slots. That
split makes the tables trustworthy and leaves exactly one way for a wrong number
to reach the report: prose that has drifted from the run it describes. On
2026-08-12 that happened three times in one day, each time because prose was
carried across a collector re-run instead of rewritten:

  * a fed-path figure stayed at 36bp after the value moved to 38bp;
  * a credit-card delinquency rate was quoted from the previous run after the
    source failed in this one;
  * and then, after the source RECOVERED, prose still claimed it had failed.

The last one is why this checks failure claims as well as numbers. "Source
failed" is an assertion about this run and is checkable: the table either shows
the failure sentinel or it does not.

USAGE
-----
    uv run python scripts/fr_verify_prose.py --report <path-to-report.md>

Exits non-zero when anything is unverified, so it can gate a workflow.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from fr_render import FAIL

PROSE_BLOCK = re.compile(r"<!-- PROSE:(\w+) -->(.*?)<!-- /PROSE:\1 -->", re.S)
NUMBER = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?")
# URLs carry digits that are not figures — an artifact UUID reads as four
# "unverified numbers" and buries the real ones.
URL = re.compile(r"https?://\S+")

# Relative tolerance for matching a prose figure to a table figure. Prose
# legitimately rounds ("−9.87%" -> "9.9%") and rescales ($306,401,459,887 ->
# "$306.4bn"), so an exact-string test would be all false positives.
REL_TOLERANCE = 0.002
ABS_TOLERANCE = 0.011

# Scale factors a writer legitimately applies when quoting a table figure.
SCALES = (1, 1e3, 1e6, 1e9, 1e12)

# Figures that are not drawn from this run's tables and are allowed on sight:
# small integers used as counts and prose ("five", "three of six"), calendar
# years, and the EY oil-scenario ladder, which the playbook fixes as a labelled
# external anchor rather than a live figure.
ALLOWED = set(range(0, 31)) | {
    74.0, 88.0, 100.0, 150.0,          # EY Q4 Brent scenario nodes (labelled external)
    45.0, 50.0, 65.0, 80.0,            # regime band boundaries, defined in the playbook
    403.0, 404.0, 429.0, 500.0, 503.0,  # HTTP statuses named when a source fails
}
ALLOWED_YEAR_RANGE = range(1800, 2101)

# Phrases asserting that a source failed in THIS run.
FAILURE_CLAIM = re.compile(
    r"(source(?:s)? (?:failed|are failing|still failing|offline)"
    r"|failed (?:this|again this) run"
    r"|unavailable this run"
    r"|(?:timed|connect-timed) out"
    r"|did not load|failed to load)",
    re.I,
)


def split_report(text: str) -> tuple[str, str]:
    """Return (prose, tables) — the model's words and the collector's output."""
    prose = URL.sub(" ", "\n".join(m.group(2) for m in PROSE_BLOCK.finditer(text)))
    tables = PROSE_BLOCK.sub("", text)
    return prose, tables


def _floats(text: str) -> list[float]:
    out = []
    for token in NUMBER.findall(text):
        try:
            out.append(float(token.replace(",", "")))
        except ValueError:
            continue
    return out


def _is_allowed(value: float) -> bool:
    return value in ALLOWED or (value.is_integer() and int(value) in ALLOWED_YEAR_RANGE)


def _half_ulp(token: str) -> float:
    """Half the last place of a written figure — what its rounding could hide.

    "9.9" stands for anything in [9.85, 9.95), so matching it against a table's
    -9.87 needs a 0.05 window. A fixed tolerance either rejects honest rounding
    or accepts genuinely different numbers; the written precision says which.
    """
    _, _, decimals = token.partition(".")
    return 0.5 * (10 ** -len(decimals)) if decimals else 0.5


def unverified_numbers(prose: str, tables: str) -> list[str]:
    """Prose figures with no counterpart in the tables, at any sane scale."""
    table_values = _floats(tables)
    missing = []
    # Stripped here rather than only in split_report so the check is correct for
    # any caller, not just the one that happens to pre-clean its input.
    for token in sorted(set(NUMBER.findall(URL.sub(" ", prose)))):
        try:
            value = float(token.replace(",", ""))
        except ValueError:
            continue
        if _is_allowed(abs(value)):
            continue
        window = max(_half_ulp(token), ABS_TOLERANCE)
        if any(
            abs(abs(value) * scale - abs(t)) <= max(window * scale, abs(t) * REL_TOLERANCE)
            for t in table_values
            for scale in SCALES
        ):
            continue
        missing.append(token)
    return missing


def unsupported_failure_claims(prose: str, tables: str) -> list[str]:
    """Sentences claiming a source failed when no table shows the sentinel.

    A recovered source is the dangerous case: the prose keeps asserting an
    outage that is no longer happening, which reads as caution but is false.
    """
    if FAIL in tables:
        return []
    return [
        sentence.strip()[:140]
        for sentence in re.split(r"(?<=[.!?])\s+", prose)
        if FAILURE_CLAIM.search(sentence)
    ]


def verify(path: Path) -> int:
    text = path.read_text()
    prose, tables = split_report(text)
    if not prose.strip():
        print(f"{path.name}: no prose slots filled — nothing to verify.")
        return 1

    numbers = unverified_numbers(prose, tables)
    claims = unsupported_failure_claims(prose, tables)

    if numbers:
        print(f"UNVERIFIED NUMBERS ({len(numbers)}) — in prose, not in this run's tables:")
        for token in numbers:
            print(f"   {token}")
        print("   Each must trace to a table figure (rounding and unit rescaling are fine),")
        print("   or be a labelled external anchor. Otherwise it is drift — fix the prose.")
    if claims:
        print(f"\nUNSUPPORTED FAILURE CLAIMS ({len(claims)}) — no table shows the sentinel:")
        for sentence in claims:
            print(f"   {sentence}")
        print("   The source recovered, or never failed. Do not assert an outage that is over.")
    if not numbers and not claims:
        print(f"{path.name}: prose verified against its own tables — no drift found.")
        return 0
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", required=True, type=Path, help="path to the finished .md report")
    args = ap.parse_args()
    if not args.report.exists():
        print(f"no such report: {args.report}")
        return 1
    return verify(args.report)


if __name__ == "__main__":
    sys.exit(main())

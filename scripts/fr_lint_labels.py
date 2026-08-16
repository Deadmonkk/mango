"""Lint field LABELS against the value paths they actually pull.

WHY THIS EXISTS
---------------
There are three ways a wrong number reaches a report, and until 2026-08-16 only
two were checked:

  * prose drifting from the tables      -> fr_verify_prose.py
  * a table disagreeing with reality    -> fr_verify_sources.py
  * a label not describing its value    -> NOTHING

The third is the quietest. ``Field("SPY MACD", ..., "macd.histogram")`` pulled
the MACD *histogram* and called it the MACD; the MACD line is roughly four times
larger. Every internal check passed, because the value was real, correctly
fetched, correctly rendered and correctly quoted in the prose. It was simply not
the thing its name claimed. An external reviewer caught it by comparing against
a charting package — which is not a check this pipeline can run on itself.

So this compares each field's label against the discriminating tokens in its
value path. When a path says ``histogram`` and the label does not, that is the
exact signature of the bug above.

IT RANKS, IT DOES NOT GATE
--------------------------
Whether a label adequately describes a value is a judgement, and a hard
threshold here would either block good runs or train you to pass a flag you
stopped reading. This prints a ranked suspect list for a human or a scoped
external review to adjudicate. It is a lint, not a verifier, and it deliberately
exits 0 even when it finds suspects.

Only the VALUE path is linted. ``read_path`` legitimately differs — it feeds the
Read column's verdict (``macd.signal`` yields "bullish"), not the number.

USAGE
-----
    uv run python scripts/fr_lint_labels.py
    uv run python scripts/fr_lint_labels.py --all     # include low-confidence
"""
from __future__ import annotations

import argparse
import re
import sys

import fr_sections

# Path tokens that carry no meaning about WHAT the number is — structural
# plumbing, not a discriminator. Their absence from a label is never a defect.
NOISE = {
    "indicators", "latest", "value", "latest_value", "current", "data", "result",
    "results", "series", "observations", "info", "summary", "overview", "stats",
    "metrics", "0", "1", "quote", "quotes", "detail", "details", "raw", "meta",
}

# Tokens that DO change what the number is. Mapping: path token -> the strings
# any honest label could use to convey it. A label missing all of them is a
# suspect, because a reader would take the number for something else.
DISCRIMINATORS: dict[str, tuple[str, ...]] = {
    "histogram": ("histogram", "hist"),
    # "cross" is how a cross_signal is named in every report that carries one;
    # a label reading "BTC cross" is not hiding anything.
    "signal": ("signal", "cross"),
    "macd_line": ("macd line", "line"),
    "upper": ("upper",),
    "lower": ("lower",),
    "middle": ("middle", "mid"),
    "sma": ("sma", "moving average", " ma", "-day", "d ma"),
    "ema": ("ema", "exponential"),
    "percentile": ("percentile", "pct", "%ile"),
    "median": ("median",),
    "mean": ("mean", "average", "avg"),
    # A ratio is conveyed by its notation as often as by the word: "put/call",
    # "% of DPI", "10y−2y". Requiring the literal word flags correct labels.
    "ratio": ("ratio", "/", "% of", "share of", "per "),
    "change": ("change", "chg", "delta", "Δ", "m/m", "y/y", "yoy", "wow", "vs"),
    "pct_change": ("change", "%", "percent", "pct"),
    "yoy": ("yoy", "y/y", "year"),
    "mom": ("mom", "m/m", "month"),
    "prior": ("prior", "previous", "last"),
    "trend": ("trend",),
    "net": ("net",),
    "gross": ("gross",),
    "total": ("total",),
    "count": ("count", "number", "n "),
    "window": ("window",),
    "annualized": ("ann", "annual"),
    "notional": ("notional",),
}

SPLIT = re.compile(r"[.\[\]_\-]+")


def _tokens(path: str) -> list[str]:
    return [t for t in SPLIT.split(path.lower()) if t and t not in NOISE]


def _label_conveys(label: str, token: str) -> bool:
    lowered = label.lower()
    return any(alias in lowered for alias in DISCRIMINATORS[token])


def _iter_fields():
    for group_name in ("SECTIONS", "EOD_SECTIONS"):
        for section in getattr(fr_sections, group_name, ()):
            for field in getattr(section, "fields", ()) or ():
                label = getattr(field, "label", None)
                path = getattr(field, "path", None)
                if label and path:
                    yield group_name, getattr(section, "title", "?"), label, path


def lint(include_low: bool = False) -> list[dict]:
    suspects: list[dict] = []
    for group, section, label, path in _iter_fields():
        # A compound path token like "macd_line" is split by SPLIT, so check the
        # raw path for multiword discriminators too.
        found = [t for t in _tokens(path) if t in DISCRIMINATORS]
        if "macd_line" in path.lower():
            found.append("macd_line")
        missing = [t for t in dict.fromkeys(found) if not _label_conveys(label, t)]
        if not missing:
            continue
        # Confidence: a path whose LEAF is the undescribed discriminator is the
        # dangerous shape (the value IS that thing). A discriminator buried
        # mid-path is usually a container name and mostly noise.
        leaf = _tokens(path)[-1] if _tokens(path) else ""
        high = any(t == leaf or t == "macd_line" for t in missing)
        if not high and not include_low:
            continue
        suspects.append(
            {
                "group": group,
                "section": section,
                "label": label,
                "path": path,
                "missing": missing,
                "confidence": "HIGH" if high else "low",
            }
        )
    return sorted(suspects, key=lambda s: (s["confidence"] != "HIGH", s["label"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all", action="store_true", help="include low-confidence suspects")
    args = ap.parse_args()

    suspects = lint(include_low=args.all)
    total = sum(1 for _ in _iter_fields())

    if not suspects:
        print(f"label lint: {total} fields checked — no label/value mismatches found.")
        return 0

    print(f"LABEL SUSPECTS ({len(suspects)} of {total} fields) — name may not match the value:")
    for s in suspects:
        print(f"  [{s['confidence']}] {s['label']}")
        print(f"          path: {s['path']}")
        print(f"          label omits: {', '.join(s['missing'])}")
    print("\n  A lint, not a verdict — adjudicate each before changing anything.")
    print("  Fix by renaming the label OR repointing the path, whichever the report means.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

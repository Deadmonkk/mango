"""Deterministic FR report generation — the collector owns everything but prose.

The old workflow sent every table through the model so the model could copy it
back into a file. That cost ~15k tokens per run in pure transport and made the
"never rebuild the tables" rule a matter of trust. Here the collector writes the
finished report itself and leaves explicitly delimited prose slots:

    <!-- PROSE:s3 -->
    (model writes here, via fr_prose.py)
    <!-- /PROSE:s3 -->

Everything outside those slots is generated from provider results and is never
transported through the conversation.

The §0 delta table is deterministic too. Each run writes `fr_values_<date>.json`
— a flat {label: value} snapshot of every rendered field — so the next run diffs
against it in code rather than having the model re-read yesterday's report.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fr_render import (
    MISSING,
    crypto_components,
    equity_components,
    field_value,
    fmt_value,
    is_num,
    render_anomalies,
    render_region_table,
    render_score_block,
    render_table,
    score,
)
from fr_sections import REPORT_SCHEMA_VERSION, SECTIONS, _resolve

VALUES_PREFIX = "fr_values_"

# Report section titles. The digest's Section objects supply the tables; these
# are the headings the saved report has always used, kept verbatim so archived
# reports stay comparable.
REPORT_TITLES: dict[str, str] = {
    "1": "Macro Snapshot",
    "2": "Cycle Position & Recession Risk",
    "3": "Credit, Consumer & Fiscal",
    "4": "Liquidity, Rates & Fed Path",
    "5": "Valuation",
    "6": "Equities, Sectors & Sentiment",
    "7": "Commodities, Dollar & Climate Risk",
    "8": "Crypto Pulse",
    "9": "BTC & ETH Deep Dive",
    "10": "Crypto Flows",
    "11": "Global Markets & Calendar",
}

# Prose slots the model fills. Ordered; the key is the marker name.
PROSE_SLOTS: tuple[tuple[str, str], ...] = (
    ("delta", "§0 — what changed, and the follow-up on last run's watch-items"),
    ("s1", "§1 interpretation"),
    ("s2", "§2 interpretation"),
    ("s3", "§3 interpretation"),
    ("s4", "§4 interpretation"),
    ("s5", "§5 interpretation"),
    ("s6", "§6 interpretation"),
    ("s7", "§7 interpretation"),
    ("s8", "§8 interpretation"),
    ("s9", "§9 interpretation"),
    ("s10", "§10 interpretation"),
    ("s11", "§11 interpretation + per-event implication lines"),
    ("synthesis", "§12 the story (≤400 words)"),
    ("crowd", "§12 crowd-vs-quant divergence (EXTERNAL)"),
    ("pillars", "§12 three A-pillars fragility lens"),
    ("selfgrade", "§12 self-grading & calibration readout"),
)

PROSE_PLACEHOLDER = "*(prose pending)*"


def marker_open(key: str) -> str:
    return f"<!-- PROSE:{key} -->"


def marker_close(key: str) -> str:
    return f"<!-- /PROSE:{key} -->"


def _slot(key: str) -> str:
    return f"{marker_open(key)}\n{PROSE_PLACEHOLDER}\n{marker_close(key)}"


# ---------------------------------------------------------------------------
# Structured values — the source of truth for the delta table
# ---------------------------------------------------------------------------
def extract_values(raw: dict, derived: dict) -> dict[str, Any]:
    """Flat {label: value} for every rendered field, plus both regime scores.

    Only JSON-serialisable scalars are kept. A field that failed or resolved to
    null is stored as None so the next run's delta can say "was unavailable"
    rather than inventing a prior.
    """
    src = _resolve(raw, derived)
    values: dict[str, Any] = {}
    for sec in SECTIONS:
        for f in sec.fields:
            val, status = field_value(src.get(f.source, {}), f)
            if status != "ok" or val is MISSING:
                values[f.label] = None
            elif isinstance(val, (int, float, str, bool)):
                values[f.label] = val
            else:
                values[f.label] = None
    values["Equity Regime Score"] = score(equity_components(raw, derived))[0]
    values["Crypto Regime Score"] = score(crypto_components(raw, derived))[0]
    return values


# Label changes that are PURE RENAMES: the figure still means what it meant, so
# the prior run's value carries over and the delta survives the rename.
VALUE_LABEL_RENAMES: dict[str, str] = {
    "CPI (index)": "CPI (index, SA)",
    "Core CPI (index)": "Core CPI (index, SA)",
    "Nonfarm payrolls": "Total nonfarm employment (level, 000s)",
    "WTI crude": "WTI crude (Cushing spot)",
    "Gold": "Gold (COMEX front month)",
    "Dollar index": "Dollar index (FRED broad, Jan 2006=100 — NOT ICE DXY)",
}

# Labels whose UNITS changed in schema v3: the CPI m/m rows carry percent where
# they used to carry index points. Diffing across that break would invent a move
# (0.245 index points vs 0.074%, reported as a 70% drop), so the prior is dropped
# and the row reads "—" for exactly one run.
UNIT_BREAK_LABELS: frozenset[str] = frozenset({
    "CPI m/m change",
    "Core CPI m/m change",
    "Energy m/m change",
    "Shelter m/m change",
    # Was the 200d SMA level (701.74) under a "vs" label; now the percent gap.
    "SPY vs 200d SMA",
})


def migrate_prior_values(prior: dict[str, Any]) -> dict[str, Any]:
    """Bring a prior snapshot onto the current label/unit contract.

    Without this a schema change silently degrades the delta: renamed rows lose
    their history and re-based rows report a move that never happened.
    """
    if not prior:
        return {}
    migrated = {VALUE_LABEL_RENAMES.get(k, k): v for k, v in prior.items()}
    for label in UNIT_BREAK_LABELS:
        migrated.pop(label, None)
    return migrated


def load_prior_values(brief_dir: Path, today: str) -> tuple[dict[str, Any], str]:
    """Most recent values snapshot strictly before ``today``.

    Returns ``({}, "")`` when none exists — the first run establishes the
    baseline and the delta section is skipped, exactly as the playbook says.
    """
    candidates = sorted(
        p for p in brief_dir.glob(f"{VALUES_PREFIX}*.json")
        if p.stem[len(VALUES_PREFIX):] < today
    )
    if not candidates:
        return {}, ""
    newest = candidates[-1]
    try:
        raw_prior = json.loads(newest.read_text())
    except (json.JSONDecodeError, OSError):
        return {}, ""
    return migrate_prior_values(raw_prior), newest.stem[len(VALUES_PREFIX):]


@dataclass(frozen=True)
class DeltaRow:
    label: str
    prior: Any
    now: Any
    change: str


def _change_str(prior: Any, now: Any) -> str:
    if prior is None or now is None:
        return "—"
    if is_num(prior) and is_num(now):
        diff = float(now) - float(prior)
        if abs(diff) < 1e-9:
            return "unchanged"
        pct = f" ({diff / abs(float(prior)) * 100:+.2f}%)" if prior else ""
        return f"{diff:+,.4g}{pct}"
    return "unchanged" if str(prior) == str(now) else "changed"


def delta_rows(current: dict, prior: dict) -> list[DeltaRow]:
    """One row per metric present in BOTH runs, in the report's field order.

    Metrics new this run are included with a "—" prior rather than dropped, so a
    newly-wired source is visible instead of silently appearing.
    """
    rows = []
    for label, now in current.items():
        if label not in prior and now is None:
            continue
        rows.append(DeltaRow(label, prior.get(label), now, _change_str(prior.get(label), now)))
    return rows


def render_delta_table(current: dict, prior: dict, prior_date: str) -> str:
    """Metrics that MOVED, with the unchanged ones counted rather than listed.

    Listing all ~150 fields buries the handful that actually moved. Unchanged
    metrics are still accounted for in the footnote, so nothing is silently
    dropped — the reader can see the denominator.
    """
    if not prior:
        return "*First run — no prior report to compare against. Baseline established.*"
    rows = delta_rows(current, prior)
    moved = [r for r in rows if r.change not in ("unchanged",)]
    unchanged = [r for r in rows if r.change == "unchanged"]
    head = [f"| Metric | {prior_date} | Now | Change |", "|---|---|---|---|"]
    body = [
        f"| {r.label} | {_cell(r.prior, r.label)} | {_cell(r.now, r.label)} | {r.change} |"
        for r in moved
    ]
    foot = [
        "",
        f"*{len(moved)} of {len(rows)} tracked metrics moved since {prior_date}; "
        f"{len(unchanged)} unchanged"
        + (f" ({', '.join(r.label for r in unchanged[:12])}"
           + (", …" if len(unchanged) > 12 else "") + ")" if unchanged else "")
        + ".*",
    ]
    return "\n".join(head + body + foot)


def _field_formats() -> dict[str, tuple[str, int]]:
    """{label: (unit, decimals)} taken from the section specs themselves.

    The delta used to render every value as a bare 4-decimal float, so a CPI row
    showing "0.07%" in §1 appeared as "0.0737" in the delta and read as a
    different figure entirely.
    """
    return {f.label: (f.unit, f.decimals) for sec in SECTIONS for f in sec.fields}


_FIELD_FORMATS = _field_formats()

# Deltas need more precision than a section table: two spreads both rendering as
# "2.72pp" must still show a move. One extra place, floored at the field's own.
_DELTA_EXTRA_DECIMALS = 2


def _cell(v: Any, label: str = "") -> str:
    """One delta cell, formatted the way its own section table formats it."""
    if v is None:
        return "—"
    unit, decimals = _FIELD_FORMATS.get(label, ("", 2))
    return fmt_value(v, unit, decimals + _DELTA_EXTRA_DECIMALS)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def build_report(
    raw: dict,
    derived: dict,
    date: str,
    prior_values: dict | None = None,
    prior_date: str = "",
) -> str:
    """The complete FR report with every deterministic block populated."""
    src = _resolve(raw, derived)
    current = extract_values(raw, derived)
    prior_values = prior_values or {}

    out: list[str] = [
        f"# Full Report — {date}",
        "",
        f"*Report schema v{REPORT_SCHEMA_VERSION}. Tables generated by "
        "`fr_collect.py --emit-report`; prose written into the marked slots.*",
        "",
        "## 0. What Changed Since Last Run",
        "",
        render_delta_table(current, prior_values, prior_date or "prior"),
        "",
        _slot("delta"),
        "",
        "---",
        "",
    ]

    by_number = {s.number: s for s in SECTIONS}
    for number, title in REPORT_TITLES.items():
        sec = by_number.get(number)
        out += [f"## {number}. {title}", ""]
        if sec is not None and sec.fields:
            out += [render_table(src, sec), ""]
        if number == "7":
            out += [
                "### ESG & Climate Production-Risk Watch",
                "",
                render_region_table(raw.get("climate_risk", {})),
                "",
            ]
        out += [_slot(f"s{number}"), ""]

    out += [
        "---",
        "",
        "## 12. Synthesis — The Big Picture",
        "",
        render_score_block("Equity Regime Score", equity_components(raw, derived)),
        "",
        render_score_block("Crypto Regime Score", crypto_components(raw, derived)),
        "",
        "### The story",
        "",
        _slot("synthesis"),
        "",
        "### Crowd-vs-quant divergence (EXTERNAL — last30days)",
        "",
        _slot("crowd"),
        "",
        '### "Three A-pillars" fragility lens (EY framing)',
        "",
        _slot("pillars"),
        "",
        "### Self-Grading & Calibration readout",
        "",
        render_table(src, by_number["12"]) if by_number.get("12") else "",
        "",
        _slot("selfgrade"),
        "",
        render_anomalies(raw),
        "",
    ]
    return "\n".join(out)


def report_path(reports_dir: Path, date: str) -> Path:
    return reports_dir / f"{date}-fr.md"

"""Deterministic rendering layer for the FR/EOD data brief.

WHY THIS EXISTS
---------------
`fr_collect.py` gathers every FR source out-of-context and used to hand the model
a raw JSON dump (~13.6k tokens) from which it then wrote ~17.8k tokens of report.
Most of that work is not judgment: building tables, placing numbers, turning a
percentile into "rich vs cheap", and averaging the regime-score components are all
deterministic. Anything deterministic belongs here, in Python, where it is free,
instant, and cannot hallucinate a number.

DIVISION OF LABOUR
------------------
  Python (this module)  finished markdown tables, rule-based Read verdicts,
                        both regime scores, the delta vs the prior snapshot
  Model                 interpretation prose only — 2-4 sentences per section
                        plus the synthesis, written around finished tables

Verdicts prefer the provider's OWN signal/interpretation string when one exists
(providers already emit these), and fall back to a threshold rule. Neither path
invents anything: a missing value renders as the FAIL sentinel and stays missing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

FAIL = "data unavailable (source failed)"

# --- scoring thresholds (no magic numbers inline) ------------------------
VIX_CALM = 15.0                 # below this = complacent, scores low
VIX_PANIC = 40.0                # at/above = panic, scores high (bottom-like)
RSI_OVERSOLD = 30.0
RSI_OVERBOUGHT = 70.0
AAII_MAX_PESSIMISM = -30.0      # bull-bear spread at deep pessimism
AAII_MAX_EUPHORIA = 30.0
PUTCALL_FEAR = 1.20
PUTCALL_GREED = 0.50
CCC_BB_CALM_PP = 5.0            # CCC-BB gap: calm
CCC_BB_STRESS_PP = 12.0         # CCC-BB gap: hidden stress in the low-quality tail
# Funding bands, recalibrated 2026-08-05. The previous values (-20 / +100) were
# set against an UNWEIGHTED cross-venue mean that overstated funding ~38x. With
# OI-weighted data the historical average is ~11%/yr, so +100 was unreachable and
# pinned the liquidation leg at 0. Verified against Coinglass OI-weighted.
FUNDING_CAPITULATION = -10.0    # annualised %, shorts paying longs = washed out
FUNDING_CROWDED = 30.0          # annualised %, well above the ~11%/yr norm
STABLE_GROWTH_STRONG_PCT = 3.0  # 30d stablecoin supply growth = dry powder
PCT_MAX = 100.0


def dig(obj: Any, path: str, default: Any = None) -> Any:
    """Extract by dotted/indexed path. Returns default on any miss — never guesses."""
    cur = obj
    try:
        for key in path.split("."):
            cur = cur[int(key)] if key.lstrip("-").isdigit() else cur[key]
        return default if cur is None else cur
    except (KeyError, IndexError, TypeError, ValueError):
        return default


# A provider that RESOLVES a field to null is making a statement — "this figure
# is not meaningful here" — which is not the same as the source failing. Folding
# both into FAIL cost us a real read on 2026-08-10: NASA POWER suppresses the
# precipitation *percentage* where the climatological base is under ~10mm (a
# percent change off 0.6mm is noise), and the digest reported three regions as
# "source failed" when the underlying rainfall had in fact been returned.
NOT_MEANINGFUL = "n/a (provider returned null — not a failure)"

MISSING = object()  # path did not resolve at all


def resolve(obj: Any, path: str) -> tuple[Any, str]:
    """Extract by path, distinguishing an explicit null from an unresolvable path.

    Returns:
        ``(value, status)`` where status is ``"ok"``, ``"null"`` (the path
        resolved to JSON null) or ``"missing"`` (the path did not resolve).
    """
    cur = obj
    try:
        for key in path.split("."):
            cur = cur[int(key)] if key.lstrip("-").isdigit() else cur[key]
    except (KeyError, IndexError, TypeError, ValueError):
        return MISSING, "missing"
    return (None, "null") if cur is None else (cur, "ok")


def is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def field_value(payload: Any, f: "Field") -> tuple[Any, str]:
    """Resolve one Field against its source payload.

    Both the rendered table and the §0 delta snapshot go through here, so a
    computed field cannot show one number in the table and a different one in
    the diff that the next run compares against.
    """
    if f.value_fn is None:
        return resolve(payload, f.path)
    try:
        v = f.value_fn(payload)
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError):
        return MISSING, "missing"
    return (None, "null") if v is None else (v, "ok")


def pct_change(prefix: str) -> Callable[[Any], float | None]:
    """Percent change of an index series from its own previous observation.

    FRED index series carry `latest_value`/`previous_value` in index POINTS. A
    row labelled "m/m change" has to show percent: printing the raw index-point
    delta invites reading +0.724 as +0.72% when the actual BLS print is +0.2%,
    a 3.6x magnification that a reader has no way to detect from the table.
    """
    def fn(payload: Any) -> float | None:
        node = dig(payload, prefix)
        if not isinstance(node, dict):
            raise KeyError(prefix)  # path absent -> "missing", not "null"
        latest, previous = node.get("latest_value"), node.get("previous_value")
        if not (is_num(latest) and is_num(previous)) or float(previous) == 0.0:
            return None
        return (float(latest) - float(previous)) / float(previous) * 100.0
    return fn


def level_change(prefix: str) -> Callable[[Any], float | None]:
    """Absolute change of a level series from its own previous observation.

    "Nonfarm payrolls" means the monthly CHANGE to every reader of a macro
    report. The provider stores the employment LEVEL under `latest_value`, so
    rendering that path prints 158,858 for a month in which payrolls FELL 23k.
    """
    def fn(payload: Any) -> float | None:
        node = dig(payload, prefix)
        if not isinstance(node, dict):
            raise KeyError(prefix)  # path absent -> "missing", not "null"
        if is_num(node.get("change")):
            return float(node["change"])
        latest, previous = node.get("latest_value"), node.get("previous_value")
        if not (is_num(latest) and is_num(previous)):
            return None
        return float(latest) - float(previous)
    return fn


def pct_distance(price_path: str, level_path: str) -> Callable[[Any], float | None]:
    """Percent distance of a price from a moving average.

    A row labelled "vs 200d SMA" has to answer "by how much". Rendering the SMA
    level under it printed 701.74 next to a price of 773.24 — the denominator
    where the reader expects the gap. The crypto section already reports this as
    `distance_from_200d_ma_pct`; this brings the equity section into line.
    """
    def fn(payload: Any) -> float | None:
        price = dig(payload, price_path)
        level = dig(payload, level_path)
        if not (is_num(price) and is_num(level)) or float(level) == 0.0:
            return None
        return (float(price) - float(level)) / float(level) * 100.0
    return fn


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def fmt_asof(value: Any) -> str:
    """An ISO observation date as a compact 'as of' label, or '' if unusable."""
    if not isinstance(value, str):
        return ""
    parts = value.split("-")
    if len(parts) < 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        return value.strip()[:20]
    month = int(parts[1])
    if not 1 <= month <= 12:
        return value.strip()[:20]
    # FRED dates monthly and quarterly observations to the 1st, so a day of "01"
    # is an artefact rather than information: "Jun 2026", not "1 Jun 2026". A
    # daily series that genuinely lands on the 1st loses only the day, and its
    # month is still stated.
    day = ""
    if len(parts) > 2 and parts[2].isdigit() and int(parts[2]) != 1:
        day = f"{int(parts[2])} "
    return f"{day}{_MONTHS[month - 1]} {parts[0]}"


def clamp(v: float, lo: float = 0.0, hi: float = PCT_MAX) -> float:
    """Bound a component score to 0-100.

    Every `100 - percentile` component MUST pass through this. Providers are
    expected to return percentiles in [0, 100], but an out-of-range value would
    otherwise yield a score like 105 or -40 and silently corrupt the weighted
    average — and a corrupted average can land outside every band label.
    """
    return max(lo, min(hi, v))


def lerp_score(value: float, at_zero: float, at_hundred: float) -> float:
    """Map value onto 0-100, where `at_zero` scores 0 and `at_hundred` scores 100."""
    if at_hundred == at_zero:
        return 50.0
    return clamp(PCT_MAX * (value - at_zero) / (at_hundred - at_zero))


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Field:
    """One row of a report table."""

    label: str
    source: str          # TOOL_MAP label, e.g. "credit_spreads"
    path: str            # dotted path within that payload
    unit: str = ""
    read_path: str = ""  # provider's own signal/interpretation, if any
    read_fn: Callable[[Any], str] | None = None
    decimals: int = 2
    # Derive the value from the whole source payload instead of one path. Used
    # where the provider's stored number is not the number the label promises
    # (an index-point delta under an "m/m change" header, an employment level
    # under a "payrolls" header). When set, `path` is ignored for the value.
    value_fn: Callable[[Any], Any] | None = None
    # Path to this figure's own observation date. Rendered into the Read column
    # so a stale series cannot masquerade as current.
    asof_path: str = ""


@dataclass(frozen=True)
class Section:
    """One numbered report section."""

    number: str
    title: str
    fields: tuple[Field, ...] = field(default_factory=tuple)


def fmt_value(v: Any, unit: str, decimals: int) -> str:
    if v is None:
        return FAIL
    if is_num(v):
        r = round(float(v), decimals)
        # Render whole numbers as integers: "6" not "6.0", "197,000" not "197,000.0".
        return f"{int(r):,}{unit}" if r == int(r) else f"{r:,}{unit}"
    return str(v)[:90]


def render_read(raw: dict, f: Field, value: Any, status: str = "ok") -> str:
    """Verdict for the Read column: provider signal first, then a rule, else blank."""
    if status == "missing" or value is MISSING:
        return "source failed"
    if status == "null":
        return "provider returned null — field not meaningful here, NOT a failure"
    if value is None:
        return "source failed"
    verdict = ""
    if f.read_path:
        sig = dig(raw.get(f.source, {}), f.read_path)
        if isinstance(sig, str) and sig.strip():
            verdict = sig.strip()[:110]
    if not verdict and f.read_fn and is_num(value):
        verdict = f.read_fn(float(value))

    # The observation date leads, so a figure a month stale cannot read as
    # current just because the row sits in today's report.
    asof = fmt_asof(dig(raw.get(f.source, {}), f.asof_path)) if f.asof_path else ""
    if not asof:
        return verdict
    return f"as of {asof} — {verdict}" if verdict else f"as of {asof}"


def render_table(raw: dict, sec: Section) -> str:
    """Finished markdown table for one section. The model never rebuilds this."""
    if not sec.fields:
        return ""
    rows = ["| Measure | Value | Read |", "|---|---|---|"]
    for f in sec.fields:
        val, status = field_value(raw.get(f.source, {}), f)
        if status == "missing":
            shown = FAIL
        elif status == "null":
            shown = NOT_MEANINGFUL
        else:
            shown = fmt_value(val, f.unit, f.decimals)
        rows.append(f"| {f.label} | {shown} | {render_read(raw, f, val, status)} |")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Regime scores — a weighted average is arithmetic, not judgment.
# Components returning None are dropped and the remaining weights renormalised,
# exactly as the FR playbook requires ("computed on available components").
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Component:
    name: str
    weight: float
    score: float | None
    detail: str


def _score_or_none(fn: Callable[[], float | None]) -> float | None:
    try:
        return fn()
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def equity_components(raw: dict, derived: dict) -> list[Component]:
    out: list[Component] = []

    cape_pct = dig(raw.get("market_valuation", {}), "cape.percentile")
    out.append(Component(
        "Valuation", 0.30,
        clamp(PCT_MAX - float(cape_pct)) if is_num(cape_pct) else None,
        f"CAPE {dig(raw.get('market_valuation', {}), 'cape.latest', FAIL)} at {cape_pct}th pct",
    ))

    hy_pct = dig(raw.get("mc_hy_spread", {}), "percentile_since_start")
    gap = derived.get("ccc_minus_bb_pp")
    credit: float | None = None
    # A tight index spread is bottom-like ONLY if the low-quality tail is calm
    # too, so BOTH legs are required. With the gap missing we cannot verify the
    # tail, and scoring on the index alone would render an outage as the most
    # bullish possible credit reading — drop the component and renormalise.
    if is_num(hy_pct) and is_num(gap):
        credit = clamp(PCT_MAX - float(hy_pct))
        credit -= lerp_score(float(gap), CCC_BB_CALM_PP, CCC_BB_STRESS_PP) * 0.5
        credit = clamp(credit)
    out.append(Component("Credit stress & quality", 0.20, credit,
                         f"HY {hy_pct}th pct, CCC−BB {gap}pp"))

    spread = dig(raw.get("retail_sentiment", {}), "aaii_survey.bull_bear_spread")
    pc = dig(raw.get("retail_sentiment", {}), "spy_put_call.ratio")
    legs = [lerp_score(float(spread), AAII_MAX_EUPHORIA, AAII_MAX_PESSIMISM)] if is_num(spread) else []
    if is_num(pc):
        legs.append(lerp_score(float(pc), PUTCALL_GREED, PUTCALL_FEAR))
    out.append(Component("Pessimism", 0.20,
                         sum(legs) / len(legs) if legs else None,
                         f"AAII spread {spread}, put/call {pc}"))

    vix = dig(raw.get("equity_sentiment", {}), "vix_term_structure.vix")
    out.append(Component("Panic-vol regime", 0.15,
                         lerp_score(float(vix), VIX_CALM, VIX_PANIC) if is_num(vix) else None,
                         f"VIX {vix}"))

    active = dig(raw.get("cycle_position", {}), "signals_active")
    avail = dig(raw.get("cycle_position", {}), "signals_available")
    cyc: float | None = None
    if is_num(active) and is_num(avail) and avail:
        cyc = PCT_MAX * (1 - float(active) / float(avail))
        save_pct = dig(raw.get("mc_PSAVERT", {}), "percentile_since_start")
        if is_num(save_pct):  # consumer squeeze docks this leg
            cyc = clamp(cyc - (PCT_MAX - float(save_pct)) * 0.3)
    out.append(Component("Cycle stabilizing", 0.05, cyc,
                         f"{active}/{avail} recession signals active"))

    # technicals_SPY uses a flat schema (rsi.rsi / sma.sma_200), unlike the
    # crypto technicals payload's momentum.rsi_14 — do not unify these blindly.
    rsi = dig(raw.get("technicals_SPY", {}), "rsi.rsi")
    price = dig(raw.get("technicals_SPY", {}), "price")
    sma200 = dig(raw.get("technicals_SPY", {}), "sma.sma_200")
    legs = [lerp_score(float(rsi), RSI_OVERBOUGHT, RSI_OVERSOLD)] if is_num(rsi) else []
    if is_num(price) and is_num(sma200) and sma200:
        legs.append(lerp_score(PCT_MAX * (float(price) / float(sma200) - 1), 20.0, -20.0))
    out.append(Component("Trend transition", 0.10,
                         sum(legs) / len(legs) if legs else None,
                         f"SPY RSI {rsi}, price {price} vs 200d {sma200}"))
    return out


def crypto_components(raw: dict, derived: dict) -> list[Component]:
    out: list[Component] = []

    # Scored by MVRV's percentile vs its own full history, mirroring how the
    # equity leg scores CAPE. There is deliberately NO fallback to a fixed band
    # map: that map scored MVRV 1.20 at 92/100 when its true rank was the 21st
    # percentile, so running it when history is missing would produce a
    # confident wrong number. Better to drop the leg and renormalise.
    mvrv = dig(raw.get("btc_valuation", {}), "mvrv")
    mvrv_pct = dig(raw.get("btc_valuation", {}), "mvrv_percentile")
    if is_num(mvrv_pct):
        onchain = clamp(PCT_MAX - float(mvrv_pct))
        detail = f"MVRV {mvrv} at {mvrv_pct}th pct of history"
    else:
        onchain = None
        detail = f"MVRV {mvrv} but no history to rank it" if is_num(mvrv) else "MVRV unavailable"
    out.append(Component("On-chain valuation", 0.30, onchain, detail))

    growth = dig(raw.get("stablecoins", {}), "supply_change_30d_pct")
    funding = dig(raw.get("crypto_funding", {}), "funding_annualized_pct")
    # The funding provider falls back to one venue when the CoinGecko aggregate is
    # down. The number still scores, but the driver line must not call a
    # single-venue reading an OI-weighted, basis-checked aggregate.
    funding_src = dig(raw.get("crypto_funding", {}), "funding_source")
    funding_qual = (
        "single-venue fallback" if funding_src == "hyperliquid" else "OI-weighted"
    )
    legs = [lerp_score(float(growth), -STABLE_GROWTH_STRONG_PCT, STABLE_GROWTH_STRONG_PCT)] if is_num(growth) else []
    if is_num(funding):
        legs.append(lerp_score(float(funding), FUNDING_CROWDED, FUNDING_CAPITULATION))
    out.append(Component("Stress & dry powder", 0.20,
                         sum(legs) / len(legs) if legs else None,
                         f"stables 30d {growth}%, BTC funding {funding}%/yr ({funding_qual})"))

    fg = dig(raw.get("fear_greed", {}), "current.value")
    out.append(Component("Pessimism", 0.20,
                         clamp(PCT_MAX - float(fg)) if is_num(fg) else None,
                         f"Fear & Greed {fg}"))

    out.append(Component("Liquidation/vol regime", 0.15,
                         lerp_score(float(funding), FUNDING_CROWDED, FUNDING_CAPITULATION) if is_num(funding) else None,
                         f"BTC funding {funding}%/yr ({funding_qual}, basis-checked)"))

    rsi = dig(raw.get("crypto_technicals_BTC", {}), "momentum.rsi_14")
    dist = dig(raw.get("crypto_technicals_BTC", {}), "moving_averages.distance_from_200d_ma_pct")
    legs = [lerp_score(float(rsi), RSI_OVERBOUGHT, RSI_OVERSOLD)] if is_num(rsi) else []
    if is_num(dist):
        legs.append(lerp_score(float(dist), 20.0, -20.0))
    out.append(Component("Trend transition", 0.10,
                         sum(legs) / len(legs) if legs else None,
                         f"BTC RSI {rsi}, {dist}% vs 200d"))

    flow = dig(raw.get("btc_etf_flows", {}), "window_net_flow_usd_m")
    out.append(Component("Flows stabilizing", 0.05,
                         lerp_score(float(flow), -1000.0, 1000.0) if is_num(flow) else None,
                         f"ETF net flow ${flow}M"))
    return out


def band(score: float) -> str:
    if score < 25:
        return "Euphoric / expensive — poor risk-reward"
    if score < 45:
        return "Mid-cycle"
    if score < 65:
        return "Neutral / transitional"
    if score < 80:
        return "Bottom-forming — improving risk-reward"
    return "Deep-value capitulation — historically strong forward returns"


def score(components: list[Component]) -> tuple[float | None, str, bool]:
    """Weighted average over available components; renormalises when some failed."""
    live = [c for c in components if c.score is not None]
    total_w = sum(c.weight for c in live)
    if not live or total_w == 0:
        return None, FAIL, True
    value = round(sum(c.score * c.weight for c in live) / total_w, 1)
    return value, band(value), len(live) < len(components)


def render_score_block(title: str, components: list[Component]) -> str:
    value, label, partial = score(components)
    head = f"**{title}: {value}/100 — {label}**"
    if partial:
        head += "  _(computed on available components; failed ones renormalised out)_"
    rows = ["", head, "", "| Component | Weight | Score | Driver |", "|---|---|---|---|"]
    for c in components:
        s = "—" if c.score is None else f"{round(c.score, 1)}"
        rows.append(f"| {c.name} | {int(c.weight * 100)}% | {s} | {c.detail} |")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Anomaly detection — SURPRISING data, as distinct from MALFORMED data.
#
# `clamp`/`is_num` above guard against malformed input (a percentile of 140, a
# string where a number belongs). This is the other layer: values that are
# perfectly well-formed but sit at a historical extreme and deserve a human read.
#
# Detection is by PERCENTILE vs each series' own history, never by "X% above
# average". A fixed percentage threshold is not portable across series: a 20%
# daily move in VIX is unremarkable, the same move in the 10y yield is a
# once-in-a-decade event. Percentile is unit-free and volatility-aware.
#
# CRITICAL: a flag NEVER suppresses, filters or alters a value. Extreme readings
# are real exactly when the report matters most — a rule that discarded them
# would have thrown out March 2020. Flag for attention; always show the number.
# ---------------------------------------------------------------------------
EXTREME_HIGH_PERCENTILE = 99.0
EXTREME_LOW_PERCENTILE = 1.0
NOTABLE_HIGH_PERCENTILE = 95.0
NOTABLE_LOW_PERCENTILE = 5.0
# A single-period move worth more than this share of a series' ENTIRE historical
# range is a step change, not drift.
LARGE_MOVE_SHARE_OF_RANGE_PCT = 5.0


# A percentile computed over a few years is not a historical extreme. Series
# whose history was truncated (e.g. the ICE BofA credit spreads lost everything
# before 2023-08-07 to a license change) must never be described as "record".
SHORT_HISTORY_YEARS = 10


def history_window(payload: dict) -> str:
    """Human-readable window a percentile was measured over, e.g. '(7,727 obs since 1996-12-31)'."""
    n = payload.get("observations")
    start = payload.get("history_start")
    if is_num(n) and start:
        return f"({int(n):,} obs since {start})"
    if start:
        return f"(since {start})"
    return "(window unknown)"


def is_short_history(payload: dict) -> bool:
    """True when the history behind a percentile is too short to call it a record."""
    start = payload.get("history_start")
    end = payload.get("latest_date") or payload.get("history_end")
    if not (isinstance(start, str) and len(start) >= 4):
        return True
    try:
        end_year = int(str(end)[:4]) if end and str(end)[:4].isdigit() else 2026
        return (end_year - int(start[:4])) < SHORT_HISTORY_YEARS
    except ValueError:
        return True


def detect_anomalies(raw: dict) -> list[str]:
    """Values at a historical extreme, or moving unusually far in one period."""
    flags: list[str] = []

    for label, payload in sorted(raw.items()):
        if not isinstance(payload, dict):
            continue

        pct = payload.get("percentile_since_start")
        name = label.removeprefix("mc_")
        if is_num(pct):
            latest = payload.get("latest")
            window = history_window(payload)
            # Decide NOTABILITY first. A short window qualifies how a flag is
            # worded; it must never turn an unremarkable mid-range value INTO a
            # flag, or every series with thin metadata would spam the list.
            if pct >= NOTABLE_HIGH_PERCENTILE:
                direction, extreme = "HIGH", pct >= EXTREME_HIGH_PERCENTILE
            elif pct <= NOTABLE_LOW_PERCENTILE:
                direction, extreme = "LOW", pct <= EXTREME_LOW_PERCENTILE
            else:
                continue

            if is_short_history(payload):
                # Rank within a limited period is not a historical extreme.
                flags.append(
                    f"{name} at {latest} — {pct:.1f}th percentile {window}. "
                    f"SHORT WINDOW: a rank within a limited period, NOT a historical "
                    f"extreme — do not call it a record."
                )
            elif extreme:
                flags.append(f"**{name}** at {latest} — {pct:.1f}th percentile {window}, "
                             f"a HISTORICAL {direction}")
            else:
                side = "top" if direction == "HIGH" else "bottom"
                flags.append(f"{name} at {latest} — {pct:.1f}th percentile {window} ({side} 5%)")

        # Step-change detection where the payload carries its own prior value.
        for key, ind in (payload.get("indicators") or {}).items():
            if not isinstance(ind, dict):
                continue
            cur, prev = ind.get("latest_value"), ind.get("previous_value")
            if not (is_num(cur) and is_num(prev)):
                continue
            ctx = raw.get(f"mc_{key}") or {}
            lo, hi = ctx.get("min"), ctx.get("max")
            if is_num(lo) and is_num(hi) and hi > lo:
                share = 100 * abs(cur - prev) / (hi - lo)
                if share >= LARGE_MOVE_SHARE_OF_RANGE_PCT:
                    flags.append(
                        f"{key} moved {prev} -> {cur} in one period "
                        f"({share:.1f}% of its entire historical range)"
                    )
    return flags


def render_anomalies(raw: dict) -> str:
    flags = detect_anomalies(raw)
    if not flags:
        return "## Anomaly watch\n\nNo metric at a historical extreme this run."
    return ("## Anomaly watch — values at historical extremes\n\n"
            "These are flagged for interpretation, NOT filtered: each number below is real "
            "and appears in the tables above. Address the bolded ones in the report.\n\n"
            + "\n".join(f"- {f}" for f in flags))


# ---------------------------------------------------------------------------
# Climate region table — the climate provider returns a dict of variable-keyed
# regions (12 named production regions), a shape the flat Field/Section model
# above cannot express. This renders it as its own fixed-column markdown table.
# ---------------------------------------------------------------------------
REGION_EXPOSURE_CAP = 4          # tickers shown per region before truncating to "…"
# Providers annotate tickers with prose ("XOM (ExxonMobil — largest Permian
# producer after its 2024 Pioneer acquisition)"). The symbol is the payload; the
# prose is not, and 12 regions of it cost ~250 tokens of a ~2.6k digest. Cap each
# entry so short forms like "corn (ZC)" survive intact and long ones lose only
# the description.
REGION_EXPOSURE_ENTRY_MAX_CHARS = 24
REGION_STATUS_MAX_CHARS = 60     # provider's own signal string, truncated
REGION_TEMP_DECIMALS = 2
REGION_PRECIP_DECIMALS = 1
# Fixed iteration order over `watch` sub-keys so the exposure list is
# deterministic regardless of the input dict's own key order.
WATCH_CATEGORY_ORDER = ("commodities", "upstream", "midstream", "downstream", "other_assets")
NO_EXPOSURE_LINKED = "none linked"


def _fmt_signed(v: float, decimals: int, unit: str) -> str:
    """Signed numeric string with trailing zeros trimmed to one minimum decimal.

    round(-3.0, 2) must render "-3.0°C", not "-3.00°C"; round(1.76, 2) must keep
    both digits: "+1.76°C". `fmt_value` above targets an unsigned/thousands style
    that doesn't fit an anomaly reading, so this is a small sibling, not a reuse.
    """
    r = round(float(v), decimals)
    s = f"{r:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0")
        if s.endswith("."):
            s += "0"
    sign = "+" if r >= 0 else ""  # negative numbers already carry "-" from `s`
    return f"{sign}{s}{unit}"


def _region_exposure(watch: Any) -> str:
    """Flatten `watch`'s ticker lists into a compact, capped, comma-joined string."""
    if not isinstance(watch, dict):
        return NO_EXPOSURE_LINKED
    tickers: list[str] = []
    for category in WATCH_CATEGORY_ORDER:
        entries = watch.get(category)
        if isinstance(entries, list):
            tickers.extend(str(e) for e in entries if e)
    if not tickers:
        return NO_EXPOSURE_LINKED
    shown = [_trim_entry(t) for t in tickers[:REGION_EXPOSURE_CAP]]
    joined = ", ".join(shown)
    # Only mark "more follow" when the last entry did not already end in an
    # ellipsis of its own — otherwise the two collide as "Corteva……".
    if len(tickers) > REGION_EXPOSURE_CAP and not joined.endswith("…"):
        joined += "…"
    return joined


def _trim_entry(entry: str) -> str:
    """Drop a ticker's trailing prose once it exceeds the per-entry budget.

    Trims on a word boundary where one exists inside the budget, so the result
    reads as a truncated name rather than a severed word.
    """
    entry = entry.strip()
    if len(entry) <= REGION_EXPOSURE_ENTRY_MAX_CHARS:
        return entry
    cut = entry[:REGION_EXPOSURE_ENTRY_MAX_CHARS]
    spaced = cut.rsplit(" ", 1)[0] if " " in cut else cut
    return spaced.rstrip(" ,—-(") + "…"


def _region_row(region: dict) -> str:
    label = region.get("label") or "—"

    temp = region.get("temp_anomaly_c")
    temp_str = _fmt_signed(temp, REGION_TEMP_DECIMALS, "°C") if is_num(temp) else FAIL

    # The provider nulls the percentage where the climatological base is tiny —
    # a percent change off 0.6mm is noise, not signal. That is a deliberate
    # suppression, so fall back to the absolute millimetres it DID return rather
    # than reporting a failure that did not happen (see NOT_MEANINGFUL).
    precip = region.get("precip_anomaly_pct")
    if is_num(precip):
        precip_str = _fmt_signed(precip, REGION_PRECIP_DECIMALS, "%")
    else:
        actual, normal = region.get("total_precip_mm"), region.get("normal_precip_mm")
        precip_str = (
            f"{round(float(actual), 1)}mm vs {round(float(normal), 1)}mm normal (% n/a, low base)"
            if is_num(actual) and is_num(normal)
            else FAIL
        )

    signal = region.get("signal")
    status = str(signal).strip()[:REGION_STATUS_MAX_CHARS] if signal else FAIL

    exposure = _region_exposure(region.get("watch"))

    return f"| {label} | {temp_str} | {precip_str} | {status} | {exposure} |"


def render_region_table(climate: dict) -> str:
    """Finished markdown table for the climate provider's per-region payload.

    Flagged regions (per `flagged_regions`) sort first; order is otherwise the
    stable insertion order of `regions`, so identical input always renders an
    identical table.
    """
    if not isinstance(climate, dict) or climate.get("error"):
        return f"Climate risk watch: {FAIL}"
    regions = climate.get("regions")
    if not isinstance(regions, dict) or not regions:
        return f"Climate risk watch: {FAIL}"

    # `flagged_regions` holds each region's LABEL, not its dict key (verified
    # against the live payload), so it cannot be matched against `regions.keys()`
    # directly. Prefer each region's own `signal` string — it lives inside the
    # record and can't drift out of sync with a separate list — and accept a
    # label match against `flagged_regions` as a second, independent path so
    # either source is sufficient.
    flagged_labels = set(climate.get("flagged_regions") or [])
    keys_in_order = list(regions.keys())

    def _is_flagged(key: str) -> bool:
        region = regions.get(key) or {}
        signal = str(region.get("signal") or "")
        if signal.startswith("FLAGGED"):
            return True
        return region.get("label") in flagged_labels

    ordered_keys = sorted(
        keys_in_order,
        key=lambda k: (0 if _is_flagged(k) else 1, keys_in_order.index(k)),
    )

    rows = ["| Region | Temp anomaly | Precip anomaly | Status | Linked exposure |",
            "|---|---|---|---|---|"]
    for key in ordered_keys:
        region = regions.get(key)
        if isinstance(region, dict):
            rows.append(_region_row(region))
    return "\n".join(rows)

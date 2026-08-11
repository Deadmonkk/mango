"""Deterministic updater for the standing climate-risk-map artifact.

Every FR run used to update this map by hand — roughly twenty individual string
replacements, ~15k tokens of model output, and on 2026-08-10 it propagated a
mislabelled "source failed" into the published page. The data in it is entirely
derived from `get_climate_risk_watch`, so it belongs to code.

Only the blocks between sentinel comments are regenerated:

    <!-- CLIMATE:CHIPS:START --> ... <!-- CLIMATE:CHIPS:END -->
    <!-- CLIMATE:MARKERS:START --> ... <!-- CLIMATE:MARKERS:END -->
    <!-- CLIMATE:SIDEBAR:START --> ... <!-- CLIMATE:SIDEBAR:END -->
    <!-- CLIMATE:TABLE:START --> ... <!-- CLIMATE:TABLE:END -->

Everything outside them — the design, the CSS, the ticker/comps/deep-dive
sections — is preserved byte for byte. A missing or duplicated sentinel is a
hard error: the map is published to a stable URL, so a partial update is worse
than no update.

Usage:
    python scripts/climate_map.py --html MAP.html --raw fr_raw_<date>.json --date 2026-08-10
    python scripts/climate_map.py --html MAP.html --raw ... --check   # no write
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TEMP_FLAG_C = 2.0       # provider's own flag thresholds, mirrored for labelling
PRECIP_FLAG_PCT = 60.0
EXTREME_PRECIP_PCT = 150.0

BLOCKS = ("CHIPS", "MARKERS", "SIDEBAR", "TABLE")


class MapStructureError(RuntimeError):
    """The HTML is not shaped the way a deterministic update requires."""


@dataclass(frozen=True)
class Placement:
    """Static map geometry for one region — position and tooltip anchoring."""

    x: float
    y: float
    tip_x: int
    tip_y: int
    below: bool = False


# Region key -> (map position, tooltip offset). Geometry is presentation, fixed
# per region; only the VALUES change run to run.
PLACEMENTS: dict[str, Placement] = {
    "us_corn_belt": Placement(230.7, 128.0, -115, -108),
    "brazil_mato_grosso": Placement(330.9, 273.6, -105, -92),
    "argentina_pampas": Placement(312.0, 332.3, -105, 14, below=True),
    "australia_wheat_belt": Placement(793.9, 325.1, -190, 14, below=True),
    "indonesia_palm": Placement(750.4, 238.7, -100, -108),
    "ivory_coast_cocoa": Placement(465.3, 220.0, -100, -108),
    "vietnam_coffee": Placement(768.3, 206.1, -100, -112),
    "brazil_sugar_belt": Placement(352.3, 298.7, -100, 14, below=True),
    "chile_atacama_copper_lithium": Placement(294.7, 302.9, -100, 14, below=True),
    "australia_pilbara_iron_ore": Placement(796.3, 300.0, -190, 14, below=True),
    "us_permian_basin": Placement(206.7, 154.9, -100, -92),
    "us_gulf_coast": Placement(237.3, 160.0, 10, -30),
}

# Short exposure strings for the tooltip/table, keyed by region.
EXPOSURE: dict[str, str] = {
    "us_corn_belt": "Corn (ZC), Soybeans (ZS) · DE, CTVA, ADM, BG, TSN",
    "brazil_mato_grosso": "Soybeans, Corn · ADM, BG, EWZ",
    "argentina_pampas": "Soybeans, Wheat, Corn · BG, AGRO",
    "australia_wheat_belt": "Wheat (ZW) · GNC.AX, EWA",
    "indonesia_palm": "Palm oil (FCPO) · WLMIY",
    "ivory_coast_cocoa": "Cocoa (CC) · HSY, MDLZ",
    "vietnam_coffee": "Robusta/Arabica coffee · NSRGY",
    "brazil_sugar_belt": "Sugar (SB), ethanol · CZZ, AGRO, EWZ",
    "chile_atacama_copper_lithium": "Copper (HG), lithium · BHP, RIO, FCX, SQM, ALB",
    "australia_pilbara_iron_ore": "Iron ore · BHP, RIO, FMG.AX",
    "us_permian_basin": "WTI (CL), Nat gas (NG) · XOM, CVX, FANG",
    "us_gulf_coast": "Natural gas (NG) · LNG, XOM",
}


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _signed(v: float, unit: str, dp: int = 1) -> str:
    return f"{v:+.{dp}f}{unit}"


def precip_text(region: dict) -> str:
    """Percentage where meaningful, millimetres where the base is too small.

    NASA POWER nulls the percentage below roughly a 10mm normal because a
    percent change off 0.6mm is noise. That is a suppression, not a failure —
    conflating the two is exactly the 2026-08-10 bug.
    """
    pct = region.get("precip_anomaly_pct")
    if _is_num(pct):
        return _signed(float(pct), "%")
    actual, normal = region.get("total_precip_mm"), region.get("normal_precip_mm")
    if _is_num(actual) and _is_num(normal):
        return f"{float(actual):.1f}mm vs {float(normal):.1f}mm normal (% n/a, low base)"
    return "data unavailable (source failed)"


def is_flagged(region: dict) -> bool:
    return str(region.get("signal") or "").startswith("FLAGGED")


def severity(region: dict) -> str:
    """extreme | high | normal — drives marker colour and pill class."""
    if not is_flagged(region):
        return "normal"
    pct = region.get("precip_anomaly_pct")
    if _is_num(pct) and abs(float(pct)) >= EXTREME_PRECIP_PCT:
        return "extreme"
    return "high"


def is_temp_flag(region: dict) -> bool:
    """A flag driven by temperature rather than precipitation (triangle marker)."""
    temp = region.get("temp_anomaly_c")
    return is_flagged(region) and _is_num(temp) and abs(float(temp)) >= TEMP_FLAG_C


def _ordered(regions: dict) -> list[str]:
    keys = list(regions)
    return sorted(keys, key=lambda k: (0 if is_flagged(regions[k]) else 1, keys.index(k)))


# ---------------------------------------------------------------------------
# Block renderers
# ---------------------------------------------------------------------------
def render_chips(regions: dict, date_label: str) -> str:
    flagged = [k for k in regions if is_flagged(regions[k])]
    temp_flags = [k for k in flagged if is_temp_flag(regions[k])]
    biggest, biggest_val = "", 0.0
    for key, r in regions.items():
        pct = r.get("precip_anomaly_pct")
        if _is_num(pct) and abs(float(pct)) > abs(biggest_val):
            biggest, biggest_val = r.get("label", key), float(pct)
    chips = [
        ("", str(len(regions)), "regions tracked"),
        ("flagged", str(len(flagged)), f"flagged on {date_label}"),
        ("extreme", _signed(biggest_val, "%") if biggest else "—",
         f"largest precip anomaly ({html.escape(biggest)})" if biggest else "no precip anomaly"),
        ("", str(len(temp_flags)), "temperature-driven flags"),
    ]
    return "\n".join(
        f'    <div class="stat-chip{" " + cls if cls else ""}">'
        f'<span class="n mono">{html.escape(n)}</span>'
        f'<span class="l">{html.escape(label)}</span></div>'
        for cls, n, label in chips
    )


def render_markers(regions: dict) -> str:
    out = []
    for key in PLACEMENTS:
        region = regions.get(key)
        if region is None:
            raise MapStructureError(f"region {key!r} missing from the climate payload")
        p = PLACEMENTS[key]
        sev = severity(region)
        colour = {"extreme": "var(--flag-extreme)", "high": "var(--flag-high)"}.get(sev, "var(--normal)")
        radius, stroke = (17, 2.2) if sev == "extreme" else ((15, 2) if sev == "high" else (11, 1.5))
        shape = (
            '<path d="M0,-8 L7,5 L-7,5 Z" fill="var(--temp-marker)"></path>'
            if is_temp_flag(region)
            else f'<circle r="{6.5 if sev != "normal" else 4.5}" fill="{colour}"></circle>'
        )
        temp = region.get("temp_anomaly_c")
        temp_str = _signed(float(temp), "&deg;C") if _is_num(temp) else "n/a"
        sig_colour = colour if sev != "normal" else "var(--text-dim)"
        style = f"left:{abs(p.tip_x)}px;"
        if p.below:
            style += " top:auto; bottom:-100px; transform:translate(-50%,0);"
        out.append(
            f'        <g class="marker marker-fo" data-region="{key}" '
            f'transform="translate({p.x},{p.y})">\n'
            f'          <circle class="pulse" r="{radius}" stroke="{colour}" '
            f'stroke-width="{stroke}"></circle>\n'
            f"          {shape}\n"
            f'          <foreignObject x="{p.tip_x}" y="{p.tip_y}" width="220" height="98" '
            f'overflow="visible">\n'
            f'            <div xmlns="http://www.w3.org/1999/xhtml" class="tooltip-box" '
            f'style="{style}">\n'
            f'              <div class="tt-title">{html.escape(str(region.get("label", key)))}</div>\n'
            f"              <div>Temp {temp_str} · Precip {html.escape(precip_text(region))}</div>\n"
            f'              <div class="tt-sig" style="color:{sig_colour}">'
            f'{html.escape(str(region.get("signal", "")))[:90]}</div>\n'
            f'              <div class="tt-tick">{html.escape(EXPOSURE.get(key, ""))}</div>\n'
            f"            </div>\n"
            f"          </foreignObject>\n"
            f"        </g>"
        )
    return "\n".join(out)


def render_sidebar(regions: dict) -> str:
    flagged = [k for k in _ordered(regions) if is_flagged(regions[k])]
    if not flagged:
        return '      <div class="flag-item"><div class="fi-detail">No regions flagged this run.</div></div>'
    out = []
    for key in flagged:
        r = regions[key]
        temp = r.get("temp_anomaly_c")
        pct = r.get("precip_anomaly_pct")
        pill = (
            _signed(float(pct), "% precip")
            if _is_num(pct) and abs(float(pct)) >= PRECIP_FLAG_PCT
            else (_signed(float(temp), "&deg;C") if _is_num(temp) else "flagged")
        )
        actual, normal = r.get("total_precip_mm"), r.get("normal_precip_mm")
        detail = (
            f"{float(actual):.1f}mm against a {float(normal):.1f}mm normal"
            if _is_num(actual) and _is_num(normal)
            else "rainfall not reported"
        )
        if _is_num(temp):
            detail += f", running {_signed(float(temp), '&deg;C')} vs normal"
        out.append(
            f'      <div class="flag-item" data-region="{key}">\n'
            f'        <div class="fi-top">\n'
            f'          <span class="fi-name">{html.escape(str(r.get("label", key)))}</span>\n'
            f'          <span class="fi-pill pill-{severity(r)}">{pill}</span>\n'
            f"        </div>\n"
            f'        <div class="fi-detail">{html.escape(str(r.get("signal", "")))}. {detail}.</div>\n'
            f'        <div class="fi-tickers">{html.escape(EXPOSURE.get(key, ""))}</div>\n'
            f"      </div>"
        )
    return "\n".join(out)


def render_table(regions: dict) -> str:
    out = []
    for key in _ordered(regions):
        r = regions[key]
        temp = r.get("temp_anomaly_c")
        flagged = is_flagged(r)
        temp_cell = _signed(float(temp), "&deg;C") if _is_num(temp) else "n/a"
        if flagged and _is_num(temp) and abs(float(temp)) >= TEMP_FLAG_C:
            temp_cell = f'<span style="color:var(--flag-high); font-weight:700;">{temp_cell}</span>'
        status = (
            f'<span class="fi-pill pill-{severity(r)}">FLAGGED</span>'
            if flagged
            else '<span class="sig-pill sig-normal">normal</span>'
        )
        row_cls = ' class="highlight"' if flagged else ""
        out.append(
            f"        <tr{row_cls} data-region=\"{key}\">\n"
            f'          <td class="region-name">{html.escape(str(r.get("label", key)))}</td>\n'
            f'          <td class="num">{temp_cell}</td>\n'
            f'          <td class="num">{html.escape(precip_text(r))}</td>\n'
            f"          <td>{status}</td>\n"
            f'          <td class="tickers">{html.escape(EXPOSURE.get(key, ""))}</td>\n'
            f"        </tr>"
        )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Sentinel replacement
# ---------------------------------------------------------------------------
def _sentinels(name: str) -> tuple[str, str]:
    return f"<!-- CLIMATE:{name}:START -->", f"<!-- CLIMATE:{name}:END -->"


def replace_block(doc: str, name: str, body: str) -> str:
    """Swap one sentinel-delimited block. Loud on absence or duplication."""
    start, end = _sentinels(name)
    if doc.count(start) != 1 or doc.count(end) != 1:
        raise MapStructureError(
            f"expected exactly one {start} / {end} pair, found "
            f"{doc.count(start)}/{doc.count(end)} — refusing to guess where the block is"
        )
    pattern = re.compile(rf"{re.escape(start)}.*?{re.escape(end)}", re.DOTALL)
    return pattern.sub(f"{start}\n{body}\n{end}", doc, count=1)


def update_map(doc: str, climate: dict, date_label: str) -> str:
    """Regenerate all four data blocks from a `get_climate_risk_watch` payload."""
    regions = climate.get("regions")
    if not isinstance(regions, dict) or not regions:
        raise MapStructureError("climate payload has no regions — refusing to blank the map")
    missing = set(PLACEMENTS) - set(regions)
    if missing:
        raise MapStructureError(f"payload missing placed regions: {sorted(missing)}")
    doc = replace_block(doc, "CHIPS", render_chips(regions, date_label))
    doc = replace_block(doc, "MARKERS", render_markers(regions))
    doc = replace_block(doc, "SIDEBAR", render_sidebar(regions))
    doc = replace_block(doc, "TABLE", render_table(regions))
    return doc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", required=True, type=Path)
    ap.add_argument("--raw", required=True, type=Path, help="fr_raw_<date>.json")
    ap.add_argument("--date", default="", help="label for the header chip")
    ap.add_argument("--check", action="store_true", help="validate and report, do not write")
    args = ap.parse_args()

    try:
        doc = args.html.read_text(encoding="utf-8")
        raw = json.loads(args.raw.read_text(encoding="utf-8"))
        climate = raw.get("climate_risk") or {}
        updated = update_map(doc, climate, args.date or "this run")
    except (MapStructureError, json.JSONDecodeError, OSError) as e:
        print(f"climate map update failed: {e}", file=sys.stderr)
        return 1

    flagged = sum(1 for r in climate["regions"].values() if is_flagged(r))
    if args.check:
        print(f"OK — {len(climate['regions'])} regions, {flagged} flagged, "
              f"{'no change' if updated == doc else 'would change'}")
        return 0
    args.html.write_text(updated, encoding="utf-8")
    print(f"updated {args.html} — {len(climate['regions'])} regions, {flagged} flagged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Local FRED series archive — insurance against future vendor-license
restrictions narrowing how much history a series serves via the API.

BACKGROUND (2026-07-06): FRED's ICE BofA credit-spread series
(BAMLC0A0CM, BAMLH0A0HYM2, BAMLH0A1HYBB, BAMLH0A2HYB, BAMLH0A3HYC) were
found to now serve only ~3 years of observations via both the API and the
direct CSV export, per an ICE Data license note ("Starting in April 2026,
this series will only include 3 years of observations"), confirmed with a
live query. A one-time recovery effort found a pre-restriction archive.org
Wayback Machine snapshot of BAMLH0A0HYM2's full 1996-2025 history (cached
2025-11-04) — but the other 4 series' earliest available Wayback snapshot
(2026-04-22) was already post-restriction, so their pre-2023 history
appears genuinely unrecoverable from any free source checked so far.

Checked ALL 78 FRED series TerminalQ tracks on 2026-07-06: only these 5
(all ICE-licensed) were restricted; the other 73 (BLS/BEA/Federal
Reserve/Treasury-published series — CPI, GDP, unemployment, claims,
yields, Fed balance sheet, forex, etc.) have full native history with no
similar vendor-license exposure today. That could change for any series
in the future, so this module now runs for every series `get_series_history`
touches, not just the 5 known-restricted ones: it's cheap insurance.

MECHANISM: every time fred.get_series_history() runs, this module merges
the live-fetched values into a permanent local file under
~/.terminalq/history/fred_archive/<series_id>.json (deduped by date,
archive wins ties) and returns the union. So:
  - A one-time seed file (`<series_id>_seed.json`) can hold recovered
    pre-restriction history (only BAMLH0A0HYM2 has one today).
  - Every live call afterward extends the archive one more day forward,
    permanently, regardless of what the live API's window later becomes.
  - If any currently-safe series gets license-restricted in the future,
    whatever history we've already banked locally survives that change.
"""

from __future__ import annotations

import json
from pathlib import Path

from terminalq.logging_config import log

from terminalq.ext_settings import PORTFOLIO_DIR

ARCHIVE_DIR = PORTFOLIO_DIR / "history" / "fred_archive"

# Documented for transparency — NOT used to gate behavior (every series is
# archived), just to explain in output why some series' archives run deeper
# than others.
KNOWN_RESTRICTED_SERIES = {
    "BAMLC0A0CM": "IG spread — ICE license, API now starts 2023-07-07, no earlier free source found",
    "BAMLH0A0HYM2": "HY spread — ICE license, API now starts 2023-07-07; full 1996-2025 history recovered via Wayback Machine seed",
    "BAMLH0A1HYBB": "BB spread — ICE license, API now starts 2023-07-07, no earlier free source found",
    "BAMLH0A2HYB": "B spread — ICE license, API now starts 2023-07-07, no earlier free source found",
    "BAMLH0A3HYC": "CCC spread — ICE license, API now starts 2023-07-07, no earlier free source found",
}


def _archive_path(series_id: str) -> Path:
    return ARCHIVE_DIR / f"{series_id}.json"


def _seed_path(series_id: str) -> Path:
    return ARCHIVE_DIR / f"{series_id}_seed.json"


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        log.warning("fred_archive: corrupt or unreadable file %s", path)
        return None


def merge_and_persist(series_id: str, dates: list[str], values: list[float]) -> tuple[list[str], list[float]]:
    """Merge freshly-fetched live (dates, values) with the local archive for
    this series (a one-time recovered seed, if one exists, plus every prior
    day already banked), persist the union back to disk, and return the
    full merged (dates, values) sorted ascending — what callers should use
    for percentile ranking instead of the live values alone.

    Never raises: a disk read/write failure degrades to returning just the
    live values, since the archive is an enhancement, not a dependency.
    """
    by_date: dict[str, float] = {}

    seed = _load_json(_seed_path(series_id))
    if seed:
        for row in seed.get("values", []):
            by_date[row["date"]] = row["value"]

    archive = _load_json(_archive_path(series_id))
    if archive:
        for row in archive.get("values", []):
            by_date[row["date"]] = row["value"]

    for d, v in zip(dates, values):
        by_date[d] = v

    merged_dates = sorted(by_date)
    merged_values = [by_date[d] for d in merged_dates]

    try:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        _archive_path(series_id).write_text(
            json.dumps(
                {
                    "series_id": series_id,
                    "values": [{"date": d, "value": v} for d, v in zip(merged_dates, merged_values)],
                    "note": KNOWN_RESTRICTED_SERIES.get(series_id, "not currently known to be license-restricted"),
                },
                indent=2,
            )
        )
    except OSError as e:
        log.warning("fred_archive: failed to persist archive for %s: %s", series_id, e)

    return merged_dates, merged_values

"""Loader for the saved Full Report archive — feeds the weekly digest.

The weekly digest needs the narrative arc across recent reports, but loading whole
files is wasteful. This pulls just the sections that carry the story — the regime
scores, "what changed", and the synthesis — plus the structured snapshot trend from
the history store, so the digest can narrate how the regime evolved without reading
every word of every report.
"""

import re

from terminalq.logging_config import log

from terminalq.ext_settings import REPORTS_DIR
from terminalq.history import latest_snapshot_per_day

# Headers whose sections carry the narrative (matched case-insensitively, loosely).
_WANTED_SECTIONS = ("regime", "what changed", "synthesis")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _extract_sections(text: str) -> dict[str, str]:
    """Map wanted section keyword -> that section's body (header to next header)."""
    sections: dict[str, str] = {}
    # Split on level-1/2/3 ATX headers, keeping the header text.
    parts = re.split(r"\n(?=#{1,3}\s)", text)
    for part in parts:
        header_match = re.match(r"#{1,3}\s+(.*)", part)
        if not header_match:
            continue
        header = header_match.group(1).lower()
        for keyword in _WANTED_SECTIONS:
            if keyword in header and keyword not in sections:
                sections[keyword] = part.strip()
    return sections


async def load_recent_reports(n: int = 7) -> dict:
    """Load the key sections of the last n saved FR reports plus the snapshot trend.

    Args:
        n: How many recent reports to include (most recent last).

    Returns:
        Dict with per-report extracted sections, the structured snapshot series for
        the same window, and the reports directory — or an error if none are found.
    """
    if not REPORTS_DIR.exists():
        return {
            "error": f"Reports directory not found: {REPORTS_DIR}. Set TERMINALQ_REPORTS_DIR to override.",
            "source": "reports (local)",
        }

    files = sorted(REPORTS_DIR.glob("*-fr.md"))
    if not files:
        return {"error": f"No *-fr.md reports in {REPORTS_DIR}", "source": "reports (local)"}

    recent = files[-n:]
    reports = []
    for path in recent:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("Could not read report %s: %s", path.name, e)
            continue
        date_match = _DATE_RE.search(path.name)
        reports.append(
            {
                "date": date_match.group(1) if date_match else path.stem,
                "sections": _extract_sections(text),
            }
        )

    snapshots = latest_snapshot_per_day()[-n:]

    return {
        "count": len(reports),
        "reports": reports,
        "snapshot_trend": snapshots,
        "reports_dir": str(REPORTS_DIR),
        "note": (
            "Each report carries only its regime-score, what-changed, and synthesis "
            "sections. snapshot_trend is the structured metric series for the same window "
            "(use it for the numeric arc; use the sections for narrative)."
        ),
        "source": "reports (local)",
    }

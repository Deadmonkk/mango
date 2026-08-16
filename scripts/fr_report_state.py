"""The single publication state of a report. One source of truth.

WHY THIS EXISTS
---------------
The collector writes ``YYYY-MM-DD-fr.md`` into the reports folder as its FIRST
action, with empty prose slots. So an unverified — indeed unwritten — report sits
where the site builder looks for several minutes of every run. That is a
publication race, not a token problem, and ordering alone cannot fix it: the FR
run is not the only thing that rebuilds the site. The niche, gex, crypto, credit,
cre-weather and nepal agents all call ``build_site.py`` too, and any of them
firing mid-FR would publish a half-written report.

Two independent checks ("no unfilled markers" AND "a stamp exists") would drift
apart over time. Instead there is one state, derived from the file itself:

    DRAFT             prose slots still unfilled — the collector's output
    PROSE_INJECTED    prose written, not yet verified
    VERIFIED          verified, and the stamp still matches the content
    LEGACY            predates the prose-slot mechanism entirely

Only VERIFIED and LEGACY may be published.

The stamp binds to a hash of the report body, so editing a report after
verification silently returns it to PROSE_INJECTED rather than leaving a stale
"verified" claim attached to changed content.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

STAMP = re.compile(r"<!-- REPORT-STATE: VERIFIED sha256=([0-9a-f]{64}) at (\S+) -->")
PROSE_MARKER = re.compile(r"<!-- PROSE:(\w+) -->(.*?)<!-- /PROSE:\1 -->", re.S)
PENDING = "*(prose pending)*"

DRAFT = "DRAFT"
PROSE_INJECTED = "PROSE_INJECTED"
VERIFIED = "VERIFIED"
LEGACY = "LEGACY"

PUBLISHABLE = frozenset({VERIFIED, LEGACY})


def body_hash(text: str) -> str:
    """Hash of the report with any stamp removed, so the stamp cannot hash itself."""
    return hashlib.sha256(STAMP.sub("", text).strip().encode()).hexdigest()


def state(text: str) -> str:
    """The publication state of a report's text."""
    slots = PROSE_MARKER.findall(text)
    if not slots:
        return LEGACY
    if any(not body.strip() or PENDING in body for _, body in slots):
        return DRAFT
    match = STAMP.search(text)
    if match and match.group(1) == body_hash(text):
        return VERIFIED
    return PROSE_INJECTED


def is_publishable(text: str) -> bool:
    return state(text) in PUBLISHABLE


def stamp(text: str) -> str:
    """Return the report with a fresh VERIFIED stamp. Idempotent."""
    stripped = STAMP.sub("", text).rstrip()
    digest = body_hash(stripped)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return f"{stripped}\n\n<!-- REPORT-STATE: VERIFIED sha256={digest} at {now} -->\n"

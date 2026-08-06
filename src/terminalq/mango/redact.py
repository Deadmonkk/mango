"""Strip credentials out of text before it is written to disk.

WHY THIS EXISTS
---------------
Providers return errors as strings, and HTTP clients put the failing URL into
those strings. For a keyed API that URL carries the key:

    Client error '403 Forbidden' for url
    'https://api.stlouisfed.org/fred/series/observations?series_id=PSAVERT&api_key=<secret>...'

The collector stores error strings verbatim in its audit-trail files, so on
2026-08-06 a FRED outage wrote the live FRED key into `fr_raw_*.json` and
`fr_brief_*.md` eleven times. Those files sit in a user-facing reports folder.

Redaction is by VALUE, not by pattern shape: whatever the process holds in its
environment as a secret is what gets removed, regardless of how it happens to
be formatted in the text. A shape-based rule (`api_key=[a-f0-9]+`) only catches
the formats someone thought of.
"""

from __future__ import annotations

import os
import re
from typing import Any

REDACTED = "REDACTED"

# Environment variables holding credentials. Anything listed here is scrubbed
# from persisted text wherever it appears.
SECRET_ENV_VARS: tuple[str, ...] = (
    "FRED_API_KEY",
    "FINNHUB_API_KEY",
    "BRAVE_API_KEY",
    "POLYGON_API_KEY",
    "COINGECKO_API_KEY",
)

# Backstop for keys this process does not hold in its own environment — e.g. a
# payload captured elsewhere, or a variable not in the list above.
_QUERY_PARAM_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|apikey|token|access[_-]?token|secret)=([^&\s'\"]+)"
)

# Ignore short or placeholder values; blanking a 3-character string would
# corrupt unrelated text for no security benefit.
MIN_SECRET_LEN = 8


def _live_secrets() -> list[str]:
    """Credential values currently present in the environment, longest first.

    Longest-first matters: if one secret is a substring of another, replacing
    the shorter one first would leave a fragment of the longer one behind.
    """
    values = {
        v for name in SECRET_ENV_VARS
        if (v := os.environ.get(name, "").strip()) and len(v) >= MIN_SECRET_LEN
    }
    return sorted(values, key=len, reverse=True)


def redact_text(text: str) -> str:
    """Remove known credential values and key-shaped query params from text."""
    for secret in _live_secrets():
        text = text.replace(secret, REDACTED)
    return _QUERY_PARAM_PATTERN.sub(rf"\1={REDACTED}", text)


def redact(obj: Any) -> Any:
    """Recursively redact strings in a payload, preserving its structure.

    Dict keys are redacted too — a key can be interpolated from user input.
    """
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        return {redact(k): redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(redact(v) for v in obj)
    return obj

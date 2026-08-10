"""Append-only audit log of tool calls.

WHY THIS EXISTS
---------------
When a report contains a number that looks wrong, the useful question is "what
did the tool actually return, and when". Without a record, answering it means
re-running the call and hoping the upstream data has not moved. The log makes a
past run inspectable after the fact.

Two properties matter more than completeness:

- **Append-only, one JSON object per line.** A crash mid-write costs the last
  line, not the file. A single JSON array would be unreadable after a partial
  write.
- **Introspection must not audit itself.** Reading the log is itself a tool
  call, so logging it would grow the file every time it is read and bury real
  entries. `get_audit_log` and `get_audit_summary` are never wrapped.

Results are truncated before storage. The point is a trace of what happened,
not a second copy of every payload.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from mango.core import paths
from typing import Any

from mango.core.logging import get_logger
from mango.core.redact import redact

log = get_logger("audit")

AUDIT_DIR_ENV_VAR = "MANGO_AUDIT_DIR"
DEFAULT_AUDIT_DIR = paths.home() / paths.AUDIT_SUBDIR

AUDIT_DIR: Path = paths.resolve_dir(paths.AUDIT_SUBDIR, AUDIT_DIR_ENV_VAR)

# Enough to identify a result, far short of duplicating it.
MAX_RESULT_CHARS = 2000
DEFAULT_LOG_LIMIT = 50


def _log_path() -> Path:
    return AUDIT_DIR / f"{datetime.now(timezone.utc).strftime('%Y-%m')}.jsonl"


def _summarize(result: Any) -> Any:
    """Shrink a result to something worth keeping."""
    if isinstance(result, dict):
        if "error" in result:
            return {"error": str(result["error"])[:MAX_RESULT_CHARS]}
        return {"keys": sorted(result)[:20], "size": len(result)}
    if isinstance(result, list):
        return {"list_len": len(result)}
    text = str(result)
    return text[:MAX_RESULT_CHARS] if text else None


def log_tool_call(
    tool_name: str,
    args: dict[str, Any] | None,
    result: Any,
    duration_ms: float,
) -> None:
    """Append one entry. Never raises — auditing must not break the call it records."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool_name,
        # Arguments can carry a symbol, a query, occasionally a credential from
        # a mis-set env var. Redact before anything reaches disk.
        "args": redact(args or {}),
        "duration_ms": round(float(duration_ms), 1),
        "result": redact(_summarize(result)),
        "ok": not (isinstance(result, dict) and "error" in result),
    }
    try:
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        with _log_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")
    except OSError as exc:
        log.warning("audit: could not write entry for %s: %s", tool_name, exc)


def _read_entries() -> list[dict[str, Any]]:
    path = _log_path()
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                # One torn line (a crash mid-append) must not discard the rest.
                continue
    except OSError as exc:
        log.warning("audit: could not read log: %s", exc)
    return entries


def get_audit_log(limit: int = DEFAULT_LOG_LIMIT, tool: str = "") -> dict[str, Any]:
    """Most recent entries, newest first, optionally filtered by tool name."""
    entries = _read_entries()
    if tool:
        entries = [e for e in entries if e.get("tool") == tool]
    entries.reverse()
    return {
        "entries": entries[:limit],
        "returned": min(limit, len(entries)),
        "total_this_month": len(entries),
        "source": "audit",
    }


def get_audit_summary() -> dict[str, Any]:
    """Per-tool call counts, failures and timings for the current month."""
    entries = _read_entries()
    if not entries:
        return {"total_calls": 0, "tools": {}, "source": "audit"}

    counts: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    durations: dict[str, list[float]] = {}
    for entry in entries:
        name = entry.get("tool", "unknown")
        counts[name] += 1
        if not entry.get("ok", True):
            failures[name] += 1
        durations.setdefault(name, []).append(float(entry.get("duration_ms") or 0.0))

    tools = {
        name: {
            "calls": counts[name],
            "failures": failures[name],
            "failure_rate_pct": round(failures[name] / counts[name] * 100, 1),
            "avg_ms": round(sum(durations[name]) / len(durations[name]), 1),
            "max_ms": round(max(durations[name]), 1),
        }
        for name in sorted(counts, key=lambda n: counts[n], reverse=True)
    }
    return {
        "total_calls": sum(counts.values()),
        "total_failures": sum(failures.values()),
        "tools": tools,
        "source": "audit",
    }

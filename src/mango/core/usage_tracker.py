"""Track API usage against monthly and daily budgets.

WHY THIS EXISTS
---------------
Several upstream APIs have hard free-tier ceilings. Exceeding one does not fail
loudly — it returns 429s or degraded data — so usage has to be counted locally
to stay under it.

CONCURRENCY IS THE WHOLE DIFFICULTY
-----------------------------------
Counters live in files and every update is read-modify-write. Two coroutines
incrementing concurrently will both read N and both write N+1, losing a call.
Every mutating operation therefore holds a per-provider ``asyncio.Lock``.

The same reasoning forbids a separate ``check_budget()`` then ``increment()``:
between the two, another coroutine can consume the last remaining call, so the
check passes and the budget is still breached. ``increment_and_check()`` does
both inside one lock and is the only correct way to enforce a ceiling.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from mango.core.logging import get_logger

log = get_logger("usage")

USAGE_DIR_ENV_VAR = "MANGO_USAGE_DIR"
DEFAULT_USAGE_DIR = Path.home() / ".mango" / "usage"

USAGE_DIR: Path = (
    Path(os.environ[USAGE_DIR_ENV_VAR]).expanduser()
    if os.environ.get(USAGE_DIR_ENV_VAR)
    else DEFAULT_USAGE_DIR
)

# One lock per provider. A single global lock would serialise unrelated
# providers; no lock at all loses increments.
_locks: dict[str, asyncio.Lock] = {}


def _lock_for(provider: str) -> asyncio.Lock:
    if provider not in _locks:
        _locks[provider] = asyncio.Lock()
    return _locks[provider]


def _month_key() -> str:
    return date.today().strftime("%Y-%m")


def _day_key() -> str:
    return date.today().isoformat()


def _path_for(provider: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in provider)
    return USAGE_DIR / f"{safe}.json"


def _read(provider: str) -> dict[str, Any]:
    path = _path_for(provider)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupt counter must not break the caller; losing history is
        # preferable to refusing to serve.
        log.warning("usage: counter unreadable for %s, starting fresh", provider)
        return {}


def _write(provider: str, data: dict[str, Any]) -> None:
    try:
        USAGE_DIR.mkdir(parents=True, exist_ok=True)
        _path_for(provider).write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("usage: could not persist counter for %s: %s", provider, exc)


async def increment_usage(provider: str, amount: int = 1) -> int:
    """Add to this month's counter and return the new total."""
    async with _lock_for(provider):
        data = _read(provider)
        months = data.setdefault("monthly", {})
        total = months.get(_month_key(), 0) + amount
        months[_month_key()] = total
        _write(provider, data)
        return total


async def get_monthly_usage(provider: str) -> int:
    """Calls recorded for the current month."""
    return _read(provider).get("monthly", {}).get(_month_key(), 0)


async def check_budget(provider: str, limit: int) -> dict[str, Any]:
    """Report remaining headroom WITHOUT consuming any.

    Read-only, so it is safe to call for display. Do NOT use it to gate a call —
    see `increment_and_check`.
    """
    used = await get_monthly_usage(provider)
    return {
        "provider": provider,
        "used": used,
        "limit": limit,
        "remaining": max(0, limit - used),
        "within_budget": used < limit,
    }


async def increment_and_check(provider: str, limit: int, amount: int = 1) -> dict[str, Any]:
    """Consume budget and report the result atomically.

    The only correct way to enforce a ceiling. Checking then incrementing as two
    steps lets a concurrent caller take the last call in between, so the check
    passes and the limit is still exceeded.
    """
    async with _lock_for(provider):
        data = _read(provider)
        months = data.setdefault("monthly", {})
        used = months.get(_month_key(), 0)
        allowed = used < limit
        if allowed:
            used += amount
            months[_month_key()] = used
            _write(provider, data)
        return {
            "provider": provider,
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used),
            "within_budget": allowed,
            "allowed": allowed,
        }


async def increment_daily(provider: str, amount: int = 1) -> int:
    """Add to today's counter and return the new total."""
    async with _lock_for(provider):
        data = _read(provider)
        days = data.setdefault("daily", {})
        total = days.get(_day_key(), 0) + amount
        days[_day_key()] = total
        _write(provider, data)
        return total


async def get_daily_usage(provider: str) -> int:
    """Calls recorded today."""
    return _read(provider).get("daily", {}).get(_day_key(), 0)


async def record_payload_size(provider: str, num_bytes: int) -> None:
    """Accumulate response bytes for today, for spotting runaway payloads."""
    async with _lock_for(provider):
        data = _read(provider)
        sizes = data.setdefault("daily_bytes", {})
        sizes[_day_key()] = sizes.get(_day_key(), 0) + int(num_bytes)
        _write(provider, data)

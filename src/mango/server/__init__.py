"""Mango's MCP server.

WHY THIS IS SHAPED THIS WAY
---------------------------
Every tool does the same four things: call a provider, audit the call, count it
against usage, and serialise the result to JSON. Writing that out 84 times
invites 84 chances to forget one — and the one most easily forgotten is the
audit, whose absence is invisible until you need the log.

So the boilerplate lives in exactly one place (`tool` below) and each tool body
is the single line that is actually specific to it: which provider to call.

TOOL NAMES ARE DELIBERATELY UNCHANGED
-------------------------------------
They are functional identifiers, not decoration: `~/.claude.json` and the
FR/EOD report playbooks call them by name. Renaming would silently break every
report. The names are kept; the implementations are original.

INTROSPECTION DOES NOT AUDIT ITSELF
-----------------------------------
`get_audit_log` and `get_usage_stats` are registered with `audited=False`.
Logging a read of the log grows the file every time it is read and buries the
entries someone was trying to find.
"""

from __future__ import annotations

import inspect
import json
import time
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

from mango.core import audit, usage_tracker
from mango.core.env import load_env
from mango.core.logging import get_logger

load_env()

log = get_logger("server")
mcp = FastMCP("Mango")

# Counted as one bucket rather than per-provider: the useful question is how
# much this server is being asked to do overall.
USAGE_BUCKET = "all_tools"


def _serialise(result: Any) -> str:
    """Render a result as JSON, for size accounting only — not for transport.

    `default=str` is deliberate: dates and Decimals reach here from several
    providers, and a serialisation crash would turn a good result into a tool
    failure with no useful message.
    """
    return json.dumps(result, indent=2, default=str)


def tool(fn: Callable | None = None, *, audited: bool = True) -> Callable:
    """Register an async provider call as an MCP tool.

    The wrapped function returns a plain dict/list; serialisation, timing,
    auditing and usage counting happen here so no tool body repeats them.
    """

    def decorate(func: Callable) -> Callable:
        async def wrapper(*args: Any, **kwargs: Any) -> str:
            started = time.monotonic()
            try:
                result = await func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — a tool must not kill the server
                log.warning("tool %s raised: %s", func.__name__, exc)
                result = {"error": str(exc), "tool": func.__name__}
            duration_ms = (time.monotonic() - started) * 1000

            if audited:
                try:
                    bound = inspect.signature(func).bind(*args, **kwargs)
                    bound.apply_defaults()
                    call_args = dict(bound.arguments)
                except TypeError:
                    call_args = kwargs or {}
                audit.log_tool_call(func.__name__, call_args, result, duration_ms)
                await usage_tracker.increment_daily(USAGE_BUCKET)

            if audited:
                # Size is tracked for spotting runaway payloads; the dict is
                # returned as-is so the framework can emit structured content.
                await usage_tracker.record_payload_size(
                    USAGE_BUCKET, len(_serialise(result).encode())
                )
            return result

        # FastMCP derives the input schema from the signature, so the wrapper
        # must carry the original's rather than (*args, **kwargs).
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        wrapper.__signature__ = inspect.signature(func)  # type: ignore[attr-defined]
        # Keep the original return annotation. Overriding it to `str` and
        # hand-serialising made the framework reject every result: it derives an
        # output schema from this annotation and validates against it.
        wrapper.__annotations__ = dict(func.__annotations__)

        mcp.add_tool(wrapper, name=func.__name__, description=(func.__doc__ or "").strip())
        return func

    return decorate(fn) if fn is not None else decorate


def csv_symbols(raw: str) -> list[str]:
    """Split a comma-separated symbol string, upper-cased, blanks dropped.

    Tools take symbols as a single string because MCP clients pass scalars far
    more reliably than arrays.
    """
    return [s.strip().upper() for s in (raw or "").split(",") if s.strip()]


def main() -> None:
    """Entry point for `python -m mango`."""
    # Importing the tool modules is what registers them; the imports are here
    # rather than at module scope so that importing `mango.server` for
    # inspection does not require every provider to be importable.
    from mango.server import (  # noqa: F401
        tools_crypto,
        tools_macro,
        tools_market,
        tools_portfolio,
        tools_reports,
    )

    log.info("Mango MCP server starting with %d tools", len(mcp._tool_manager._tools))
    mcp.run()

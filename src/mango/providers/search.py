"""Keyless web search, with an optional Brave Search upgrade.

Clean-room implementation written directly from a written specification (the
return-shape contract below is fixed by an existing saved payload this
package's caller relies on), not from any prior search provider in this
codebase family.

DuckDuckGo (via the `ddgs` package) is the keyless default every install
gets for free. When `BRAVE_API_KEY` is set, Brave Search is tried first for
its richer, separately-ranked news results, falling back to DuckDuckGo on
any failure — a missing/invalid key, a rate limit, a network error. Never
raises: every failure path degrades to an ``{"error": ...}`` dict, mirroring
the convention used throughout ``mango.core`` (see `fred.py` for the same
pattern against a different upstream).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from mango.core import cache
from mango.core.env import load_env
from mango.core.limiter import RateLimiter
from mango.core.logging import get_logger
from mango.core.redact import redact_text

log = get_logger("search")

# Load ~/.env before resolving the key — see mango.core.env's docstring for
# why this is explicit rather than inherited from import order.
load_env()
BRAVE_API_KEY: str = os.environ.get("BRAVE_API_KEY", "")

BRAVE_BASE_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_SOURCE = "brave"
DUCKDUCKGO_SOURCE = "duckduckgo"

# Brave's free tier documents a 1 request/second cap.
BRAVE_RATE_LIMIT_PER_MINUTE = 60
_brave_limiter = RateLimiter(BRAVE_RATE_LIMIT_PER_MINUTE)

REQUEST_TIMEOUT_SECONDS = 10.0

# Search results move constantly; a short TTL only dedupes repeated calls
# for the same query within a single report run.
SEARCH_CACHE_TTL_SECONDS = 300

DEFAULT_RESULT_COUNT = 5

DUCKDUCKGO_NOTE = (
    "DuckDuckGo (keyless default). Set BRAVE_API_KEY for Brave Search with a news section."
)
BRAVE_NOTE = "Brave Search."


def _error_dict(query: str, exc: BaseException, source: str) -> dict:
    message = redact_text(str(exc))
    return {"error": message, "query": query, "source": source}


# --- DuckDuckGo (keyless default) ------------------------------------------


def _fetch_duckduckgo(query: str, count: int) -> list[dict[str, Any]]:
    """Blocking `ddgs` call — always dispatch via ``asyncio.to_thread``."""
    from ddgs import DDGS  # imported lazily so the dependency is optional at import time

    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=count))


def _shape_duckduckgo_results(raw_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "title": item.get("title", ""),
            "url": item.get("href", ""),
            "description": item.get("body", ""),
            # DuckDuckGo's text search does not report an article age.
            "age": "",
        }
        for item in raw_results
    ]


async def _search_duckduckgo(query: str, count: int) -> dict:
    try:
        raw_results = await asyncio.to_thread(_fetch_duckduckgo, query, count)
    except Exception as exc:  # ddgs raises a grab-bag of exception types
        log.warning("DuckDuckGo search failed for %r: %s", query, redact_text(str(exc)))
        return _error_dict(query, exc, DUCKDUCKGO_SOURCE)

    results = _shape_duckduckgo_results(raw_results)
    return {
        "query": query,
        "results": results,
        "total_results": len(results),
        "news": [],
        "note": DUCKDUCKGO_NOTE,
        "source": DUCKDUCKGO_SOURCE,
    }


# --- Brave Search (optional upgrade) ---------------------------------------


def _shape_brave_web_results(payload: dict) -> list[dict[str, Any]]:
    web_results = (payload.get("web") or {}).get("results") or []
    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "description": item.get("description", ""),
            "age": item.get("age", ""),
        }
        for item in web_results
    ]


def _shape_brave_news_results(payload: dict) -> list[dict[str, Any]]:
    news_results = (payload.get("news") or {}).get("results") or []
    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "description": item.get("description", ""),
            "age": item.get("age", ""),
        }
        for item in news_results
    ]


async def _fetch_brave(client: httpx.AsyncClient, query: str, count: int) -> dict:
    await _brave_limiter.acquire()
    response = await client.get(
        BRAVE_BASE_URL,
        params={"q": query, "count": count},
        headers={"X-Subscription-Token": BRAVE_API_KEY, "Accept": "application/json"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


async def _search_brave(query: str, count: int) -> dict | None:
    """Try Brave Search. Returns None (never an error dict) on any failure

    so the caller can fall back to DuckDuckGo transparently — Brave is an
    upgrade over the keyless default, not a hard dependency.
    """
    try:
        async with httpx.AsyncClient() as client:
            payload = await _fetch_brave(client, query, count)
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Brave search failed for %r, falling back to DuckDuckGo: %s", query, redact_text(str(exc)))
        return None

    if not isinstance(payload, dict):
        log.warning("Brave search returned an unexpected shape for %r; falling back to DuckDuckGo", query)
        return None

    results = _shape_brave_web_results(payload)
    news = _shape_brave_news_results(payload)
    return {
        "query": query,
        "results": results,
        "total_results": len(results),
        "news": news,
        "note": BRAVE_NOTE,
        "source": BRAVE_SOURCE,
    }


# --- public entry point --------------------------------------------------


async def web_search(query: str, count: int = DEFAULT_RESULT_COUNT) -> dict:
    """Search the web for `query`, returning up to `count` results.

    Returns, on success::

        {
          "query": str, "results": [{"title", "url", "description", "age"}, ...],
          "total_results": int, "news": [...], "note": str, "source": str,
        }

    Prefers Brave Search when `BRAVE_API_KEY` is set, falling back to
    DuckDuckGo on any Brave failure. Otherwise DuckDuckGo is used directly.
    Never raises: any failure from both backends becomes
    ``{"error": ..., "query": query, "source": ...}``.
    """
    if not query:
        return {"error": "query must not be empty", "query": query, "source": DUCKDUCKGO_SOURCE}

    cache_key = f"web_search_{query}_{count}_{bool(BRAVE_API_KEY)}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    if BRAVE_API_KEY:
        brave_result = await _search_brave(query, count)
        if brave_result is not None:
            cache.set(cache_key, brave_result, SEARCH_CACHE_TTL_SECONDS)
            return brave_result

    result = await _search_duckduckgo(query, count)
    if "error" not in result:
        cache.set(cache_key, result, SEARCH_CACHE_TTL_SECONDS)
    return result

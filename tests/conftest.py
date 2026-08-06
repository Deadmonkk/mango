import json
import re
from unittest.mock import MagicMock

import httpx
import pytest


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML-like frontmatter from a markdown file.

    Returns (frontmatter_dict, body_text). Handles simple key: value,
    key: [list] patterns, and YAML folded/literal block scalars (>-, |-)
    without requiring PyYAML.
    """
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    raw = parts[1].strip()
    body = parts[2].strip()
    fm: dict = {}
    current_key = None
    folding_key = None  # Track multiline folded scalar (>- or |-)

    for line in raw.splitlines():
        # If we're collecting a folded/literal scalar
        if folding_key:
            # Indented continuation line
            if line.startswith("  ") and not re.match(r"^  - ", line):
                fm[folding_key] += " " + line.strip()
                continue
            else:
                # End of folded block — strip leading/trailing whitespace
                fm[folding_key] = fm[folding_key].strip()
                folding_key = None

        # List item under current key
        if line.startswith("  - ") and current_key:
            val = line.strip().lstrip("- ").strip()
            if not isinstance(fm.get(current_key), list):
                fm[current_key] = []
            fm[current_key].append(val)
            continue

        # Nested key (e.g., arguments sub-fields) — skip
        if line.startswith("    "):
            continue

        match = re.match(r"^(\w[\w-]*):\s*(.*)", line)
        if match:
            key = match.group(1)
            value = match.group(2).strip()
            current_key = key
            # Detect YAML folded (>-) or literal (|-) block scalar
            if value in (">-", ">", "|-", "|"):
                folding_key = key
                fm[key] = ""
            elif value:
                fm[key] = value
            else:
                fm[key] = []

    # Close any open folded block
    if folding_key and folding_key in fm:
        fm[folding_key] = fm[folding_key].strip()

    return fm, body


@pytest.fixture(autouse=True)
def _never_touch_the_real_cache(tmp_path_factory, monkeypatch):
    """Redirect both caches for EVERY test, whether or not it asks.

    Opt-in isolation is the wrong shape here: a single test that forgets the
    fixture writes fixture values into the operator's live cache, where a later
    run serves them as real data. On 2026-08-06 that put a fabricated CAPE of
    35.0 into the live cache. Tests needing to inspect cache files should still
    request `tmp_cache_dir`; this fixture only guarantees the floor.
    """
    sink = tmp_path_factory.mktemp("cache-sink")
    monkeypatch.setattr("terminalq.cache.CACHE_DIR", sink)
    monkeypatch.setattr("terminalq.mango.cache.CACHE_DIR", sink)
    monkeypatch.setenv("CACHE_DIR", str(sink))


@pytest.fixture
def tmp_cache_dir(tmp_path, monkeypatch):
    """Provide isolated cache directory.

    Both cache implementations must be redirected. Missing either one lets a
    test write fixture values into the operator's real cache, where a later run
    serves them as live data — which is how a fabricated CAPE reached the live
    cache on 2026-08-06.
    """
    monkeypatch.setattr("terminalq.cache.CACHE_DIR", tmp_path)
    monkeypatch.setattr("terminalq.mango.cache.CACHE_DIR", tmp_path)
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def sample_ohlcv():
    """30 days of sample OHLCV data."""
    return [
        {
            "date": f"2026-01-{i + 1:02d}",
            "open": 100 + i * 0.5,
            "high": 101 + i * 0.5,
            "low": 99.5 + i * 0.5,
            "close": 100.5 + i * 0.5,
            "volume": 1000000,
        }
        for i in range(30)
    ]


@pytest.fixture
def mock_httpx_get():
    """Factory for mocking httpx.AsyncClient.get responses."""

    def _make_response(status_code: int = 200, json_data=None, text_data: str = ""):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        resp.json.return_value = json_data if json_data is not None else {}
        resp.text = text_data or json.dumps(json_data) if json_data else text_data
        resp.raise_for_status = MagicMock()
        if status_code >= 400:
            resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                f"HTTP {status_code}",
                request=MagicMock(),
                response=resp,
            )
        return resp

    return _make_response

"""Guard: provider modules must go through `mango.core.http`, not raw httpx.

WHY THIS TEST EXISTS
--------------------
Resilience became a property of the platform on 2026-08-17 (`core/http.py`).
That only holds while every provider actually uses it. A single new
`httpx.get(...)` added later silently opts that source out of retry/backoff and
reintroduces the failure mode this refactor removed — with no visible symptom
except an occasional "data unavailable (source failed)" row that looks like an
upstream outage rather than a missing retry.

A convention in a docstring does not survive contact with a future edit. This
does.

Modules in ALLOWED_RAW_HTTPX carry their OWN retry/backoff and rate limiting;
routing them through the shared layer would double-retry against APIs with hard
free-tier ceilings that `core/usage_tracker.py` counts. Adding to this list is a
deliberate act that should come with a reason, which is why it lives here.
"""

import ast
from pathlib import Path

import pytest

PROVIDERS_DIR = Path(__file__).resolve().parents[1] / "src" / "mango" / "providers"

ALLOWED_RAW_HTTPX = {
    # Owns a rate limiter, 429 backoff and a shared cache (core/coingecko.py).
    "coingecko.py",
    # Routes through the CoinGecko client above for exactly that reason.
    "crypto_funding.py",
    # Encodes a hard "never retry the 403" rule for its premium-walled endpoints.
    "finnhub.py",
}

# What actually bypasses the shared policy is issuing a request and handling the
# raw response, NOT constructing a client: `httpx.AsyncClient()` is encouraged
# (connection pooling) and can be handed to `fetch_json(..., client=...)`, which
# keeps the retry policy. So the tell we look for is a provider driving a
# response itself — `raise_for_status()` is only ever called on one.
_REQUEST_ATTRS = {"get", "post", "put", "delete", "request", "stream"}
_RAW_RESPONSE_TELL = "raise_for_status"


def _provider_files() -> list[Path]:
    return sorted(p for p in PROVIDERS_DIR.glob("*.py") if p.name != "__init__.py")


def _raw_httpx_calls(tree: ast.AST) -> list[str]:
    """Calls that issue a request or drive a raw response outside core/http."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        if attr == _RAW_RESPONSE_TELL:
            found.append(f".{attr}()")
        elif (
            isinstance(node.func.value, ast.Name)
            and node.func.value.id == "httpx"
            and attr in _REQUEST_ATTRS
        ):
            found.append(f"httpx.{attr}")
    return found


@pytest.mark.parametrize("path", _provider_files(), ids=lambda p: p.name)
def test_provider_uses_shared_http_layer(path: Path):
    if path.name in ALLOWED_RAW_HTTPX:
        pytest.skip(f"{path.name} owns its retry/rate-limit policy (see ALLOWED_RAW_HTTPX)")
    calls = _raw_httpx_calls(ast.parse(path.read_text(encoding="utf-8")))
    assert not calls, (
        f"{path.name} performs raw HTTP {sorted(set(calls))}, bypassing core/http.py's "
        "retry and backoff. Use http.fetch_json()/http.fetch_text(), or add the module "
        "to ALLOWED_RAW_HTTPX with a comment explaining what policy it owns instead."
    )


def test_allowlist_entries_all_exist():
    """A stale allowlist entry would silently exempt nothing — or mask a rename."""
    names = {p.name for p in _provider_files()}
    assert ALLOWED_RAW_HTTPX <= names, f"allowlist names no longer present: {ALLOWED_RAW_HTTPX - names}"

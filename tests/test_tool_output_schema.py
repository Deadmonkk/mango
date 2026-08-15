"""Every MCP tool must return what its annotation promises.

FastMCP derives each tool's *output* schema from its return annotation and
validates the result against it. A tool annotated ``-> dict`` that returns a
bare list therefore fails at the transport layer for every single call, while
the provider underneath is perfectly healthy — so the provider tests all pass
and nothing catches it. That is exactly what happened to ``get_crypto_batch``:
it was dead in the MCP server for as long as it had been annotated, and only
surfaced when a caller tried to use it.

These tests close that gap statically. They need no network and no server.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

SERVER_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "mango" / "server"
PROVIDER_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "mango" / "providers"


def _is_tool(node: ast.AST) -> bool:
    """True for a function carrying the @tool decorator (bare or called)."""
    if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
        return False
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "tool":
            return True
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == "tool":
            return True
    return False


def _tools() -> list[tuple[str, ast.AsyncFunctionDef | ast.FunctionDef]]:
    found = []
    for path in sorted(SERVER_DIR.glob("tools_*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if _is_tool(node):
                found.append((path.name, node))
    return found


def _provider_returns() -> dict[str, str]:
    anns: dict[str, str] = {}
    for path in sorted(PROVIDER_DIR.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.returns:
                anns[f"{path.stem}.{node.name}"] = ast.unparse(node.returns)
    return anns


def test_tools_were_discovered():
    """Guard the guard: if the AST walk silently finds nothing, the two tests
    below would pass vacuously and the whole file would be worthless."""
    tools = _tools()
    assert len(tools) > 50, f"expected the full tool surface, found {len(tools)}"


def test_every_tool_declares_a_return_annotation():
    missing = [f"{mod}::{node.name}" for mod, node in _tools() if node.returns is None]
    assert not missing, (
        "these tools have no return annotation, so FastMCP cannot derive an "
        f"output schema for them: {missing}"
    )


def test_no_tool_returns_a_bare_provider_list_under_a_dict_annotation():
    """A tool annotated `-> dict` must not hand back a provider call whose own
    annotation is a list.

    The fix is to wrap it under a key — `{"quotes": [...]}` — rather than to
    re-annotate the tool `-> list[dict]`. The @tool wrapper's exception path
    returns `{"error": ...}`, a dict, so a list-annotated tool would fail
    validation precisely when something had already gone wrong.
    """
    providers = _provider_returns()
    offenders = []

    for mod, node in _tools():
        declared = ast.unparse(node.returns) if node.returns else "None"
        if declared.startswith("list"):
            offenders.append(
                f"{mod}::{node.name} is annotated `-> {declared}`; the @tool error "
                "path returns a dict, so this breaks whenever the tool errors"
            )
            continue

        # Only a *returned* provider call can leak the list out of the tool;
        # calls whose result is bound to a name get post-processed first.
        for stmt in ast.walk(node):
            if not isinstance(stmt, ast.Return) or stmt.value is None:
                continue
            inner = stmt.value
            if isinstance(inner, ast.Await):
                inner = inner.value
            if not isinstance(inner, ast.Call):
                continue
            fn = inner.func
            if not (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)):
                continue
            key = f"{fn.value.id}.{fn.attr}"
            provider_ret = providers.get(key)
            if provider_ret and provider_ret.startswith("list"):
                offenders.append(
                    f"{mod}::{node.name} is annotated `-> {declared}` but returns "
                    f"{key}() directly, which is annotated `-> {provider_ret}`. "
                    "Wrap it under a key, e.g. {\"quotes\": ...}"
                )

    assert not offenders, "tool/provider return-type mismatches:\n  " + "\n  ".join(offenders)


@pytest.mark.parametrize("symbol,expected", [
    ("BTC", "bitcoin"),
    ("SOL", "solana"),
    ("XRP", "ripple"),
    # Both resolve to a slug that is NOT the lowercase ticker; before these
    # were mapped, get_crypto_deep 404'd.
    ("RENDER", "render-token"),
    ("ONDO", "ondo-finance"),
    # Case-insensitive on input.
    ("render", "render-token"),
])
def test_nonobvious_symbols_resolve_to_real_coingecko_ids(symbol, expected):
    from mango.core.coingecko import _resolve_id

    assert _resolve_id(symbol) == expected

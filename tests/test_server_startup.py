"""Can the product actually start?

WHY THIS EXISTS
---------------
Unit tests answer "is this function correct". They do not answer "does the
thing a person runs actually come up". Two separate incidents made that
distinction expensive:

- 2026-08-07, the collector: six providers were repointed at a package where
  they did not exist. 744 unit tests passed; nothing imported the collector.
- 2026-08-07, the MCP server: the `mcp` dependency was declared `>=1.0.0` with
  no upper bound, a resolve took 2.x where `mcp.server.fastmcp` had moved, and
  `python -m terminalq` failed at import. The already-running process masked it.

There are progressively stronger levels of confidence, and each costs more:

    1. the module imports
    2. the application starts
    3. it advertises the capabilities it should
    4. it does useful work

Unit tests give (1) for libraries. These give (1)-(3) for the entry points
someone actually executes. (4) belongs to live verification, not CI.

The MCP server is part of the HOST project, not this package, so every test
here skips cleanly when no host is present — which is the normal case in this
package's own CI.
"""

from __future__ import annotations

import pytest


def _load_host_server():
    """Import the host's server, distinguishing 'absent' from 'broken'.

    `importorskip` cannot tell those apart, and conflating them defeats the
    point of this file: when the mcp 2.x breakage was reintroduced to check
    these tests, they SKIPPED instead of failing — a silent pass for the exact
    bug they exist to catch.

    So: no host package at all is a legitimate skip (this package's own CI).
    A host that is present but whose server will not import is a failure.
    """
    try:
        import terminalq  # noqa: F401
    except ImportError:
        pytest.skip("no host project present; the MCP server is not part of this package")
    try:
        import terminalq.server as host_server
    except Exception as exc:  # ImportError, but a bad dependency can raise anything
        pytest.fail(
            f"the host is installed but its server will not import: {exc!r}. "
            "This is what a broken dependency pin looks like — the server would "
            "fail to start."
        )
    return host_server


server = _load_host_server()

# The count moves as tools are added; this is a floor guarding against a
# collapse (an import failure swallowing a whole provider group), not a spec.
MINIMUM_EXPECTED_TOOLS = 60

# Tools backed by modules this package owns. If the host's imports of `mango.*`
# ever break, these disappear while the host's own tools stay — a partial
# failure that a bare "does it start" check would not notice.
OWNED_BACKED_TOOLS = (
    "terminalq_get_climate_risk_watch",
    "terminalq_get_dealer_gamma",
    "terminalq_get_regime_history",
    "terminalq_get_rsu_tax_analysis",
)


def _registered_tool_names() -> set[str]:
    """Tool names the server module exposes, however its framework stores them."""
    names = {n for n in dir(server) if n.startswith("terminalq_")}
    if names:
        return names
    app = getattr(server, "mcp", None) or getattr(server, "app", None)
    registry = getattr(app, "_tool_manager", None)
    tools = getattr(registry, "_tools", {}) if registry else {}
    return set(tools)


class TestServerStarts:
    def test_server_module_imports(self) -> None:
        # Level 1. This alone would have caught the mcp 2.x breakage, which was
        # an import-time failure inside the server module.
        assert server is not None

    def test_entry_point_is_callable(self) -> None:
        # Level 2, cheaply: `python -m terminalq` resolves to this.
        assert callable(getattr(server, "main", None)), "server.main() is missing"


class TestServerAdvertisesItsTools:
    def test_tool_count_has_not_collapsed(self) -> None:
        # Level 3. A provider group failing to import can leave the server
        # running but silently short of tools.
        names = _registered_tool_names()
        assert len(names) >= MINIMUM_EXPECTED_TOOLS, (
            f"only {len(names)} tools registered (floor {MINIMUM_EXPECTED_TOOLS}) — "
            "a provider group may have failed to import"
        )

    @pytest.mark.parametrize("tool", OWNED_BACKED_TOOLS)
    def test_tools_backed_by_owned_modules_are_present(self, tool: str) -> None:
        # Guards the host's `mango.*` imports specifically. These were repointed
        # during the package rename and are the ones most likely to regress.
        assert tool in _registered_tool_names(), f"{tool} is not registered"

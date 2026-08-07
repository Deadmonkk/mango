"""Skip guards for tests that need this pack wired into TerminalQ's own modules.

Most of this pack is self-contained: a new provider plus a test that mocks its
network calls. A few features are different — they are *fallbacks* that only do
anything once TerminalQ's own provider knows to call them. The Hyperliquid
fallback, for example, only fires when ``coingecko.py`` has been taught to reach
for it after a failure, and that edit lives in an upstream file this pack does
not ship (see the Installation section of the README).

Those integration tests are real and they pass in a wired install. On a clean
TerminalQ checkout the hook simply is not there, so they skip with a reason
naming the missing attribute rather than failing and implying the pack is
broken.
"""

import pytest


def requires(module, attribute: str) -> pytest.MarkDecorator:
    """Skip unless ``module`` exposes ``attribute``.

    Args:
        module: The upstream module the integration hook should live on.
        attribute: The name this pack's wiring is expected to add to it.
    """
    return pytest.mark.skipif(
        not hasattr(module, attribute),
        reason=(
            f"needs `{module.__name__}.{attribute}`, which this pack adds to TerminalQ "
            f"during installation — not present in a clean checkout (see README)"
        ),
    )


def host_module(dotted_name: str):
    """Import a host-project module, or skip this whole test module if absent.

    `requires` above guards a missing *attribute*, which presumes the module
    imported at all. Standalone there is no host package, so the import itself
    raises during collection — a hard error that implies the pack is broken when
    it simply is not wired. This skips instead, with the module name in the
    reason.
    """
    return pytest.importorskip(
        dotted_name,
        reason=f"{dotted_name} belongs to the host project; not present standalone",
    )

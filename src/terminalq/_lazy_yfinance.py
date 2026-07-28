"""Lazy ``yfinance`` proxy — defer the heavy import until first use.

Importing ``yfinance`` pulls in pandas/numpy and costs ~330ms. Seven providers
imported it at module top, so the MCP server — which imports every provider at
startup — paid that cost on every cold start, even for sessions that never touch
a Yahoo-backed tool. Measured: it was ~50% of total server import time.

Providers now do ``from terminalq._lazy_yfinance import yfinance`` and call
``yfinance.Ticker(...)`` exactly as before; the real module is imported on the
first attribute access and cached thereafter. The module-level ``yfinance`` name
stays a patchable object, so existing test hooks —
``patch.object(module.yfinance, "Ticker", ...)`` and
``patch("module.yfinance.Ticker", ...)`` — keep working unchanged.
"""

import importlib
from types import ModuleType


class _LazyYFinance:
    """Attribute-forwarding proxy that imports ``yfinance`` on first access.

    ``_module`` is a class attribute so it resolves via normal lookup and never
    re-enters ``__getattr__`` (which fires only for *missing* attributes),
    avoiding infinite recursion.
    """

    _module: ModuleType | None = None

    def __getattr__(self, name: str):
        module = type(self)._module
        if module is None:
            module = type(self)._module = importlib.import_module("yfinance")
        return getattr(module, name)


yfinance = _LazyYFinance()

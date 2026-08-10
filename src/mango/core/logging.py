"""Logging configuration for the mango extension pack.

Standard-library only. Configures a single named logger ("mango") with a
stderr stream handler so that stdout stays clean for entry points that emit
machine-readable output there (e.g. CLI commands that print JSON to stdout).

The format mirrors the existing project convention:
    [13:02:23] mango.fred INFO: FRED get_series: series=CPI resolved=...
"""

from __future__ import annotations

import logging
import os
import sys

# redact imports only os/re — no cycle back into logging.
from mango.core.redact import redact, redact_text

# Name of the root logger for this project. Child loggers are namespaced
# under this (e.g. "mango.fred") via `get_logger`.
_ROOT_LOGGER_NAME = "mango"

# Environment variable used to override the default log level.
_LOG_LEVEL_ENV_VAR = "MANGO_LOG_LEVEL"

# Fallback level when the env var is unset or holds an unrecognised value.
_DEFAULT_LOG_LEVEL = logging.INFO

# `[HH:MM:SS] <logger name> <LEVEL>: <message>`
_LOG_FORMAT = "[%(asctime)s] %(name)s %(levelname)s: %(message)s"
_DATE_FORMAT = "%H:%M:%S"

# Guards against attaching duplicate handlers if this module is imported
# multiple times (e.g. via different import paths, or reloaded). A second
# handler would cause every log line to be printed N times.
_configured = False


def _resolve_log_level() -> int:
    """Resolve the configured log level from the environment.

    Accepts the standard level names (DEBUG/INFO/WARNING/ERROR/...),
    case-insensitively. Falls back to INFO on anything unrecognised rather
    than raising, since a bad env var should degrade gracefully, not crash
    the process at import time.
    """
    raw_level = os.environ.get(_LOG_LEVEL_ENV_VAR, "")
    if not raw_level:
        return _DEFAULT_LOG_LEVEL

    candidate = logging.getLevelName(raw_level.strip().upper())
    if isinstance(candidate, int):
        return candidate
    return _DEFAULT_LOG_LEVEL


class _RedactingFilter(logging.Filter):
    """Scrub credential values out of every record before it is emitted.

    Applied at the handler rather than the call site on purpose. Providers log
    the URL they were calling (see `coingecko.py`), and for a keyed API that URL
    carries the key — but there are ~150 log statements, and securing them one
    at a time means the next one added reopens the hole. A filter is the only
    placement where "no log line can carry a secret" is a property of the
    system rather than a habit.

    The record's args are redacted before formatting, so lazy `%s` interpolation
    (the form every call site uses) is covered.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.args:
            record.args = redact(record.args)
        return True


def _configure_root_logger() -> logging.Logger:
    """Configure the "mango" logger exactly once and return it."""
    global _configured

    root_logger = logging.getLogger(_ROOT_LOGGER_NAME)

    if _configured:
        return root_logger

    handler = logging.StreamHandler(stream=sys.stderr)
    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)
    handler.setFormatter(formatter)
    handler.addFilter(_RedactingFilter())

    root_logger.addHandler(handler)
    root_logger.setLevel(_resolve_log_level())

    # Don't let records bubble up to the root logger too — otherwise a
    # caller who separately configures `logging.basicConfig()` would get
    # every line printed twice.
    root_logger.propagate = False

    _configured = True
    return root_logger


log = _configure_root_logger()


def get_logger(name: str) -> logging.Logger:
    """Return a child logger namespaced under the "mango" root.

    Example: get_logger("fred") -> logger named "mango.fred".
    Child loggers inherit the root's handler/level/propagate settings, so no
    additional configuration is needed here.
    """
    return log.getChild(name)

"""Load API credentials from the user's dotfile, once.

WHY THIS EXISTS
---------------
Keys live in ``~/.env``. Nothing in this package read that file, so a module
resolving ``FRED_API_KEY`` at import saw it only if some *other* module had
already loaded the dotfile as a side effect. That worked by accident of import
order inside the host project and failed outright standalone: on 2026-08-06 the
owned FRED client returned "FRED_API_KEY not configured" against a live, healthy
API purely because nothing had loaded the file yet.

Credential loading is therefore explicit and owned, not inherited.

``override=False`` is deliberate and differs from the host's ``override=True``:
an explicitly exported environment variable should beat a dotfile. That is the
conventional precedence, and it is also what lets a test set a variable without
the dotfile silently overwriting it.
"""

from __future__ import annotations

import os
from pathlib import Path

# Searched in order; the first that exists is loaded. PROJECT_ENV_FILE lets a
# deployment point at its own file without touching the user's home directory.
ENV_VAR_OVERRIDE = "TERMINALQ_ENV_FILE"
DEFAULT_ENV_PATH = Path.home() / ".env"

_loaded = False


def load_env(force: bool = False) -> bool:
    """Load the dotfile into os.environ. Idempotent; safe to call from anywhere.

    Returns True if a file was read. A missing dotfile is normal — credentials
    may come from the real environment — so it is not an error.
    """
    global _loaded
    if _loaded and not force:
        return False

    configured = os.environ.get(ENV_VAR_OVERRIDE)
    path = Path(configured).expanduser() if configured else DEFAULT_ENV_PATH
    if not path.is_file():
        _loaded = True
        return False

    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv absent: fall back to the real environment
        _loaded = True
        return False

    load_dotenv(path, override=False)
    _loaded = True
    return True

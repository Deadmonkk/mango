"""Where Mango keeps user data — the single source of truth for every path.

WHY THIS EXISTS
---------------
Mango is a public project; the person running it is not the person who wrote
it. That makes one rule load-bearing: **no user state lives in the repository,
and no repository code assumes a particular home directory.**

Before this module, six files each hardcoded ``Path.home() / ".mango"``.
Relocating data meant setting six environment variables and knowing which — and
one path (the BTC valuation cache) could not be moved at all. This module owns
the layout so a caller never has to know it.

RESOLUTION ORDER
----------------
For every directory, most specific wins:

1. Its own variable — ``MANGO_CACHE_DIR``, ``MANGO_AUDIT_DIR``, ...
2. ``MANGO_HOME`` / <subdir>
3. ``~/.mango`` / <subdir>

So ``MANGO_HOME=/data/mango`` relocates everything in one move, while a single
directory can still be split out (a cache on faster disk, say) without
disturbing the rest.

TWO LEGACY NAMES ARE STILL HONOURED
-----------------------------------
``CACHE_DIR`` and ``PORTFOLIO_DIR`` predate this module and are dangerously
generic — a bare ``CACHE_DIR`` in a shared environment belongs to whichever
tool reads it first, and silently pointing Mango's cache at another program's
directory is a data-corruption bug, not a configuration one. Both are still
read, so nobody's setup breaks, but they warn and the namespaced form wins.

IMPORT-TIME RESOLUTION IS DELIBERATE
------------------------------------
Callers resolve into a module-level constant at import and tests monkeypatch
that constant. Re-reading the environment per call looks more flexible and
defeats that isolation: a test run then writes fixture values into the
operator's real directories, where a later run serves them as live data. That
happened on 2026-08-06 — 67 files, including a fabricated CAPE, landed in the
live cache. The helpers here are called once, at import, on purpose.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from mango.core.logging import get_logger

log = get_logger("paths")

HOME_ENV_VAR = "MANGO_HOME"
DEFAULT_HOME = Path.home() / ".mango"

# The directory this project used before it was renamed. Retained only so a
# pre-rename install can be migrated; nothing else should reference it.
LEGACY_HOME = Path.home() / ".terminalq"

# Subdirectory names under the home. Portfolio files live at the home root
# rather than in a subdirectory — they are hand-edited, and asking someone to
# maintain a file three levels down is how they end up not maintaining it.
CACHE_SUBDIR = "cache"
AUDIT_SUBDIR = "audit"
USAGE_SUBDIR = "usage"
HISTORY_SUBDIR = "history"

# Reports are generated output, not state, so they sit outside the data home.
REPORTS_DIR_ENV_VAR = "MANGO_REPORTS_DIR"
DEFAULT_REPORTS_DIR = Path.home() / "market-reports"

# Directories created on bootstrap. The portfolio root is included so a fresh
# install has somewhere obvious to put holdings.
_BOOTSTRAP_SUBDIRS = (CACHE_SUBDIR, AUDIT_SUBDIR, USAGE_SUBDIR, HISTORY_SUBDIR)

# Restrictive by default: this tree holds holdings, an audit trail and cached
# keyed API responses. A default umask yields 0755/0644, which is world-readable.
DIR_MODE = 0o700


def home() -> Path:
    """The root of the user's Mango data directory."""
    configured = os.environ.get(HOME_ENV_VAR, "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_HOME


def resolve_dir(subdir: str, env_var: str, legacy_env_var: str | None = None) -> Path:
    """Resolve one data directory, most-specific-source-wins.

    `subdir` may be "" to mean the home root itself.
    """
    configured = os.environ.get(env_var, "").strip()
    if configured:
        return Path(configured).expanduser()

    if legacy_env_var:
        legacy = os.environ.get(legacy_env_var, "").strip()
        if legacy:
            log.warning(
                "%s is deprecated and its name is generic enough to collide with "
                "another tool; set %s instead. Using %s for now.",
                legacy_env_var, env_var, legacy,
            )
            return Path(legacy).expanduser()

    return home() / subdir if subdir else home()


def reports_dir() -> Path:
    """Where generated reports are written.

    Kept outside the data home, and its historical default (`~/market-reports`)
    is preserved deliberately: silently relocating an existing user's reports
    to a new default is a data-migration disguised as a refactor.
    """
    configured = os.environ.get(REPORTS_DIR_ENV_VAR, "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_REPORTS_DIR


def default_env_file() -> Path:
    """Default location of the credentials dotfile.

    Lives at the home root rather than inside the data directory: it holds API
    keys, not Mango state, and a user may already share one across tools.
    Overridden by ``MANGO_ENV_FILE``.
    """
    return Path.home() / ".env"


def ensure_dirs() -> list[Path]:
    """Create the data directories a fresh install needs. Idempotent.

    Called at server start so the first run of a clean checkout works without
    the user creating anything by hand. Existing directories are left alone —
    including their permissions, since an operator may have set them
    deliberately.
    """
    created: list[Path] = []
    root = home()

    for path in (root, *(root / s for s in _BOOTSTRAP_SUBDIRS)):
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(DIR_MODE)
            created.append(path)

    if created:
        log.info("created %d data directories under %s", len(created), root)
    return created


def migrate_legacy_home() -> dict:
    """Copy a pre-rename ``~/.terminalq`` into the current home, if safe.

    Non-destructive in both directions, which is the whole contract:

    - The source is **copied, never moved**. If anything about the new location
      is wrong, the original is still sitting there untouched.
    - It refuses to run if the destination already holds data. Merging two
      partially-populated directories risks silently overwriting a prediction
      ledger with an older one, and no automatic rule can tell which is
      authoritative. A human must decide that.

    Returns a summary dict rather than raising: a failed migration must not
    stop the server from starting, it must be reported.
    """
    destination = home()

    if not LEGACY_HOME.exists():
        return {"migrated": False, "reason": "no legacy directory"}

    if LEGACY_HOME.resolve() == destination.resolve():
        return {"migrated": False, "reason": "legacy and current home are the same path"}

    if destination.exists() and any(destination.iterdir()):
        log.warning(
            "found legacy data at %s but %s already has data; leaving both alone. "
            "Merge by hand if the legacy copy is the one you want.",
            LEGACY_HOME, destination,
        )
        return {"migrated": False, "reason": "destination not empty", "legacy": str(LEGACY_HOME)}

    try:
        shutil.copytree(LEGACY_HOME, destination, dirs_exist_ok=False)
        destination.chmod(DIR_MODE)
        log.info("migrated data from %s to %s (original left in place)", LEGACY_HOME, destination)
        return {"migrated": True, "source": str(LEGACY_HOME), "destination": str(destination)}
    except OSError as exc:
        log.warning("could not migrate %s to %s: %s", LEGACY_HOME, destination, exc)
        return {"migrated": False, "reason": str(exc)}


def bootstrap() -> dict:
    """Prepare the data directory for use. Safe to call on every start."""
    migration = migrate_legacy_home()
    created = ensure_dirs()
    return {"home": str(home()), "created": [str(p) for p in created], "migration": migration}

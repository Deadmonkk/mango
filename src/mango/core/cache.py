"""File-based key/value cache with per-entry TTL — one JSON file per key.

Why plain JSON files, one per key
----------------------------------
Operators grep this directory by hand to diagnose stale or missing data, so
the storage format is deliberately boring: no SQLite, no pickle, no single
combined index file that would need a query to inspect. `ls` and `cat` are
the debugging tools.

Why this never raises
----------------------
A cache is an optimisation, not a source of truth. A caller that asks for a
cached value should get `None` on any failure (missing key, expired entry,
corrupt file) rather than an exception, and a caller that writes a value
should never have that write take the request down (e.g. an unwritable
cache directory). Every failure path here is caught, logged at WARNING, and
swallowed.

Why errors are never cached
----------------------------
Providers in this stack return `{"error": ...}` payloads instead of raising
(see `mango.cache_guard`). Caching one of those turns a brief upstream
blip into a full TTL window of silently-wrong "data unavailable" reads. This
module refuses to persist anything `cache_guard.should_cache` rejects — see
that module's docstring for the incident that made this a hard rule.
"""

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from mango.cache_guard import should_cache
from mango.core.logging import log

# Default TTL, matches the public `set()` signature's default.
DEFAULT_TTL_SECONDS = 60

# Fallback cache directory when CACHE_DIR is not set in the environment.
# This deliberately does NOT match the host project's repo-relative
# `data/cache` — a standalone extension package should not write inside its
# own source tree, and `~/.mango/` is already where this tool keeps user
# data (e.g. `~/.mango/history/`). Both caches coexist during the
# transition; the worst case of the split is a value being fetched twice.
DEFAULT_CACHE_DIR = Path.home() / ".mango" / "cache"

# Environment variable an operator can set to point the cache somewhere else.
_CACHE_DIR_ENV_VAR = "CACHE_DIR"

# Filenames are <sanitized-key-prefix>__<hash>.json. The sanitized prefix
# keeps files grep-able/eyeballable; the hash guarantees two different keys
# never collide into the same file even if sanitization maps them to the
# same characters (e.g. "a/b" and "a:b" both sanitize to "a_b").
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_MAX_SANITIZED_PREFIX_LEN = 80
_KEY_HASH_LEN = 16


# Resolved once at import so tests can monkeypatch this attribute, which is the
# isolation idiom the existing suite already uses for the host's cache. Resolving
# the environment on every call instead looks more flexible but silently defeats
# that fixture: a test run then writes fixture values into the operator's real
# cache directory, where a later run can serve them as live data. That happened
# on 2026-08-06 — 67 files, including a fabricated CAPE, landed in the live cache.
CACHE_DIR = (
    Path(os.environ[_CACHE_DIR_ENV_VAR]).expanduser()
    if os.environ.get(_CACHE_DIR_ENV_VAR)
    else DEFAULT_CACHE_DIR
)


def _cache_dir() -> Path:
    """The active cache directory. Patch `CACHE_DIR` to redirect it in tests."""
    return CACHE_DIR


def _ensure_cache_dir(cache_dir: Path) -> bool:
    """Create the cache directory on demand. Returns False (never raises) if that fails."""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("mango.cache: could not create cache directory %s: %s", cache_dir, exc)
        return False
    return True


def _safe_filename(key: str) -> str:
    """Turn an arbitrary key into a filesystem-safe, collision-free filename.

    Keys may contain characters that are illegal or dangerous in filenames
    (`/`, `:`, path separators generally). The sanitized prefix is kept for
    readability when an operator lists the directory; the hash suffix is what
    actually guarantees uniqueness, so sanitization collisions are harmless.
    """
    sanitized = _UNSAFE_FILENAME_CHARS.sub("_", key)[:_MAX_SANITIZED_PREFIX_LEN]
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:_KEY_HASH_LEN]
    return f"{sanitized}__{digest}.json"

def _entry_path(key: str) -> Path:
    return _cache_dir() / _safe_filename(key)


def _write_entry_no_follow(path: Path, payload: str) -> None:
    """Write a cache entry, refusing to follow a symlink at the target path.

    `Path.write_text` follows symlinks. Cache filenames are deterministic (a
    sanitized prefix plus a SHA-256 of the key), so anything that can create a
    file in the cache directory can pre-plant a symlink at a path this process
    is going to write — pointing at `~/.zshrc`, `~/.ssh/config`, or any other
    file the user can write — and the cache write lands there instead. Verified
    exploitable before this guard existed.

    `O_NOFOLLOW` makes the kernel refuse rather than trusting a check that could
    go stale between the test and the open (TOCTOU). The mode is 0600 because a
    default umask yields world-readable cache files, and cached payloads can
    include keyed API responses.
    """
    if path.is_symlink():
        log.warning("mango.cache: refusing to write through symlink at %s; removing it", path)
        path.unlink()

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    # fdopen takes ownership of the descriptor, so it must be closed by hand
    # only if fdopen itself fails; once it succeeds, the `with` owns it and
    # closing again could hit an unrelated descriptor that reused the number.
    try:
        handle = os.fdopen(fd, "w", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise
    with handle:
        handle.write(payload)


def _remove_quietly(path: Path) -> None:
    """Delete a cache file, swallowing any error — removal is best-effort cleanup."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("mango.cache: could not remove cache file %s: %s", path, exc)


def get(key: str) -> dict | list | None:
    """Return the cached value for `key`, or None if absent, expired, or unreadable.

    An expired entry's file is deleted as a side effect of reading it, so the
    directory does not accumulate dead entries indefinitely just from reads.
    A corrupt/unreadable file is treated the same as a miss and removed.
    """
    path = _entry_path(key)
    if not path.is_file():
        return None

    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.warning("mango.cache: corrupt cache entry for key %r (%s); discarding", key, exc)
        _remove_quietly(path)
        return None

    expires_at = entry.get("expires_at") if isinstance(entry, dict) else None
    if not isinstance(expires_at, (int, float)):
        log.warning("mango.cache: malformed cache entry for key %r (no expires_at); discarding", key)
        _remove_quietly(path)
        return None

    if time.time() >= expires_at:
        log.debug("mango.cache: entry for key %r expired; discarding", key)
        _remove_quietly(path)
        return None

    return entry.get("value")


def set(key: str, value: dict | list, ttl: int = DEFAULT_TTL_SECONDS) -> None:
    """Store `value` under `key` with expiry now + `ttl` seconds.

    No-op (with a warning) if `value` carries an error payload — see the
    module docstring — or if the cache directory can't be created/written to.
    Never raises: a cache write failing must never break the caller.
    """
    if not should_cache(value):
        log.warning("mango.cache: refusing to cache error payload for key %r", key)
        return

    cache_dir = _cache_dir()
    if not _ensure_cache_dir(cache_dir):
        return

    now = time.time()
    entry = {
        "key": key,
        "value": value,
        # Human-readable so an operator can eyeball an entry's age directly.
        "cached_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # Epoch seconds so expiry checks don't need to re-parse a timestamp.
        "expires_at": now + ttl,
    }

    path = _entry_path(key)
    try:
        _write_entry_no_follow(path, json.dumps(entry, indent=2, sort_keys=True))
    except OSError as exc:
        log.warning("mango.cache: failed to write cache entry for key %r to %s: %s", key, path, exc)

"""The public/private split: no user state in the repo, all of it relocatable.

These tests exist because the guarantees they cover are invisible when they
work and catastrophic when they don't — a migration that overwrites a
prediction ledger looks exactly like a migration that worked.
"""

from __future__ import annotations


import pytest

from mango.core import paths


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Point every path variable somewhere disposable.

    Without this a failing assertion could write into the operator's real
    ~/.mango — the exact class of accident this module is meant to prevent.
    """
    for var in (
        "MANGO_HOME", "MANGO_CACHE_DIR", "MANGO_AUDIT_DIR", "MANGO_USAGE_DIR",
        "MANGO_HISTORY_DIR", "MANGO_PORTFOLIO_DIR", "CACHE_DIR", "PORTFOLIO_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(paths, "LEGACY_HOME", tmp_path / "nonexistent-legacy")
    monkeypatch.setattr(paths, "DEFAULT_HOME", tmp_path / "default-home")


# --- resolution order -------------------------------------------------------


def test_mango_home_relocates_every_directory_at_once(monkeypatch, tmp_path):
    """One variable must move the whole tree — that is its entire purpose."""
    new_home = tmp_path / "elsewhere"
    monkeypatch.setenv("MANGO_HOME", str(new_home))

    assert paths.home() == new_home
    assert paths.resolve_dir(paths.CACHE_SUBDIR, "MANGO_CACHE_DIR") == new_home / "cache"
    assert paths.resolve_dir(paths.AUDIT_SUBDIR, "MANGO_AUDIT_DIR") == new_home / "audit"
    assert paths.resolve_dir(paths.USAGE_SUBDIR, "MANGO_USAGE_DIR") == new_home / "usage"


def test_specific_variable_beats_mango_home(monkeypatch, tmp_path):
    """Splitting one directory out must not require moving the rest."""
    monkeypatch.setenv("MANGO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MANGO_CACHE_DIR", str(tmp_path / "fast-disk"))

    assert paths.resolve_dir(paths.CACHE_SUBDIR, "MANGO_CACHE_DIR") == tmp_path / "fast-disk"
    assert paths.resolve_dir(paths.AUDIT_SUBDIR, "MANGO_AUDIT_DIR") == tmp_path / "home" / "audit"


def test_legacy_generic_variable_still_works_but_loses_to_the_namespaced_one(monkeypatch, tmp_path):
    """Nobody's existing setup breaks, but the collision-prone name is not preferred."""
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "legacy"))
    assert paths.resolve_dir(paths.CACHE_SUBDIR, "MANGO_CACHE_DIR", "CACHE_DIR") == tmp_path / "legacy"

    monkeypatch.setenv("MANGO_CACHE_DIR", str(tmp_path / "namespaced"))
    assert paths.resolve_dir(paths.CACHE_SUBDIR, "MANGO_CACHE_DIR", "CACHE_DIR") == tmp_path / "namespaced"


def test_home_expands_user(monkeypatch, tmp_path):
    monkeypatch.setenv("MANGO_HOME", "~/somewhere")
    assert "~" not in str(paths.home())


# --- fresh install ----------------------------------------------------------


def test_fresh_install_creates_every_directory(monkeypatch, tmp_path):
    """A clean clone must work without the user creating anything by hand."""
    home = tmp_path / "brand-new"
    monkeypatch.setenv("MANGO_HOME", str(home))
    assert not home.exists()

    created = paths.ensure_dirs()

    assert home.is_dir()
    for sub in ("cache", "audit", "usage", "history"):
        assert (home / sub).is_dir(), f"{sub} missing after bootstrap"
    assert len(created) == 5
    # Holdings and an audit trail live here; a default umask would be 0755.
    assert home.stat().st_mode & 0o777 == 0o700


def test_ensure_dirs_is_idempotent_and_leaves_existing_content_alone(monkeypatch, tmp_path):
    home = tmp_path / "existing"
    monkeypatch.setenv("MANGO_HOME", str(home))
    paths.ensure_dirs()
    (home / "history").mkdir(exist_ok=True)
    ledger = home / "history" / "predictions.jsonl"
    ledger.write_text('{"claim": "keep me"}\n')

    assert paths.ensure_dirs() == []            # nothing new to create
    assert ledger.read_text() == '{"claim": "keep me"}\n'


# --- migration --------------------------------------------------------------


def test_migration_copies_legacy_data_without_moving_it(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy"
    (legacy / "history").mkdir(parents=True)
    (legacy / "history" / "predictions.jsonl").write_text('{"claim": "original"}\n')
    (legacy / "watchlist.md").write_text("# Watchlist\n")

    home = tmp_path / "new-home"
    monkeypatch.setattr(paths, "LEGACY_HOME", legacy)
    monkeypatch.setenv("MANGO_HOME", str(home))

    result = paths.migrate_legacy_home()

    assert result["migrated"] is True
    assert (home / "history" / "predictions.jsonl").read_text() == '{"claim": "original"}\n'
    assert (home / "watchlist.md").exists()
    # Copied, never moved: if the new location is wrong, the original survives.
    assert (legacy / "history" / "predictions.jsonl").exists()


def test_migration_refuses_when_destination_already_has_data(monkeypatch, tmp_path):
    """The guarantee that matters most.

    Merging two populated directories could overwrite a current prediction
    ledger with a stale one, and nothing in the filesystem says which is
    authoritative. Refusing is the only safe automatic answer.
    """
    legacy = tmp_path / "legacy"
    (legacy / "history").mkdir(parents=True)
    (legacy / "history" / "predictions.jsonl").write_text('{"claim": "OLD"}\n')

    home = tmp_path / "home-with-data"
    (home / "history").mkdir(parents=True)
    current = home / "history" / "predictions.jsonl"
    current.write_text('{"claim": "CURRENT"}\n')

    monkeypatch.setattr(paths, "LEGACY_HOME", legacy)
    monkeypatch.setenv("MANGO_HOME", str(home))

    result = paths.migrate_legacy_home()

    assert result["migrated"] is False
    assert result["reason"] == "destination not empty"
    assert current.read_text() == '{"claim": "CURRENT"}\n'   # untouched
    assert (legacy / "history" / "predictions.jsonl").exists()  # also untouched


def test_migration_is_a_no_op_without_a_legacy_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "LEGACY_HOME", tmp_path / "not-there")
    monkeypatch.setenv("MANGO_HOME", str(tmp_path / "home"))

    assert paths.migrate_legacy_home()["migrated"] is False


def test_bootstrap_migrates_then_creates(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "watchlist.md").write_text("# Watchlist\n")
    home = tmp_path / "home"
    monkeypatch.setattr(paths, "LEGACY_HOME", legacy)
    monkeypatch.setenv("MANGO_HOME", str(home))

    state = paths.bootstrap()

    assert state["migration"]["migrated"] is True
    assert (home / "watchlist.md").exists()      # migrated content survives
    assert (home / "cache").is_dir()             # and the rest is filled in


# --- the repo itself --------------------------------------------------------


def test_no_module_hardcodes_a_home_directory():
    """Every data path must be relocatable; a literal home defeats MANGO_HOME."""
    import pathlib

    src = pathlib.Path(__file__).resolve().parent.parent / "src" / "mango"
    offenders = [
        f"{p.relative_to(src)}:{i}"
        for p in src.rglob("*.py")
        if p.name != "paths.py"
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if "Path.home()" in line and not line.lstrip().startswith("#")
    ]
    assert offenders == [], f"hardcoded home directory outside paths.py: {offenders}"

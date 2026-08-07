"""Tests for the mango file-based cache — see mango.core.cache for the design rationale."""

import importlib
import json
from pathlib import Path

import pytest

from mango.core import cache as mango_cache


@pytest.fixture(autouse=True)
def _isolated_cache_dir(tmp_path, monkeypatch):
    """Every test gets its own throwaway cache directory — never the real one."""
    monkeypatch.delenv("CACHE_DIR", raising=False)
    monkeypatch.setattr(mango_cache, "DEFAULT_CACHE_DIR", tmp_path / "cache")
    return tmp_path


def test_round_trip_returns_the_stored_value():
    # Arrange
    payload = {"latest_value": 4.63, "series": "DGS10"}

    # Act
    mango_cache.set("ten_year_yield", payload, ttl=60)
    result = mango_cache.get("ten_year_yield")

    # Assert
    assert result == payload


def test_round_trip_supports_list_values():
    # Arrange
    payload = [{"date": "2026-08-01", "value": 1.0}, {"date": "2026-08-02", "value": 2.0}]

    # Act
    mango_cache.set("history_series", payload, ttl=60)
    result = mango_cache.get("history_series")

    # Assert
    assert result == payload


def test_expired_entry_returns_none_and_deletes_its_file(monkeypatch):
    # Arrange
    current_time = [1_000_000.0]
    monkeypatch.setattr(mango_cache.time, "time", lambda: current_time[0])
    mango_cache.set("btc_price", {"price": 65000}, ttl=60)
    path = mango_cache._entry_path("btc_price")
    assert path.exists()

    # Act
    current_time[0] += 61  # advance past the 60s TTL without sleeping
    result = mango_cache.get("btc_price")

    # Assert
    assert result is None
    assert not path.exists()


def test_entry_within_ttl_is_not_expired(monkeypatch):
    # Arrange
    current_time = [1_000_000.0]
    monkeypatch.setattr(mango_cache.time, "time", lambda: current_time[0])
    mango_cache.set("btc_price", {"price": 65000}, ttl=60)

    # Act
    current_time[0] += 30  # still inside the TTL window
    result = mango_cache.get("btc_price")

    # Assert
    assert result == {"price": 65000}


def test_missing_key_returns_none():
    # Act
    result = mango_cache.get("never_set_this_key")

    # Assert
    assert result is None


def test_corrupt_json_file_is_treated_as_a_miss_and_removed():
    # Arrange
    mango_cache.set("weather", {"temp_c": 20}, ttl=60)
    path = mango_cache._entry_path("weather")
    path.write_text("{not valid json at all", encoding="utf-8")

    # Act
    result = mango_cache.get("weather")

    # Assert
    assert result is None
    assert not path.exists()


def test_entry_missing_expiry_field_is_treated_as_a_miss_and_removed():
    # Arrange
    path = mango_cache._entry_path("no_expiry")
    mango_cache._ensure_cache_dir(mango_cache._cache_dir())
    path.write_text(json.dumps({"key": "no_expiry", "value": {"a": 1}}), encoding="utf-8")

    # Act
    result = mango_cache.get("no_expiry")

    # Assert
    assert result is None
    assert not path.exists()


def test_different_keys_sanitize_to_different_files():
    # Arrange
    key_a = "quote:AAPL"
    key_b = "quote/AAPL"  # sanitizes to the same visible prefix as key_a

    # Act
    mango_cache.set(key_a, {"symbol": "AAPL", "source": "a"}, ttl=60)
    mango_cache.set(key_b, {"symbol": "AAPL", "source": "b"}, ttl=60)

    # Assert
    assert mango_cache._entry_path(key_a) != mango_cache._entry_path(key_b)
    assert mango_cache.get(key_a) == {"symbol": "AAPL", "source": "a"}
    assert mango_cache.get(key_b) == {"symbol": "AAPL", "source": "b"}


def test_filename_is_safe_for_keys_with_illegal_path_characters():
    # Arrange
    key = "fred:series/DGS10:2026-08-06"

    # Act
    mango_cache.set(key, {"value": 4.63}, ttl=60)
    path = mango_cache._entry_path(key)

    # Assert
    assert path.exists()
    assert "/" not in path.name
    assert ":" not in path.name


def test_top_level_error_payload_is_never_cached():
    # Arrange
    error_payload = {"error": "Request timed out", "source": "fred"}

    # Act
    mango_cache.set("bad_series", error_payload, ttl=60)
    result = mango_cache.get("bad_series")

    # Assert
    assert result is None
    assert not mango_cache._entry_path("bad_series").exists()


def test_nested_dashboard_shaped_error_payload_is_never_cached():
    # Arrange — the shape a multi-indicator dashboard payload takes.
    dashboard_payload = {
        "indicators": {
            "unemployment": {"latest_value": 4.2},
            "gdp": {"error": "Connection failed"},
        }
    }

    # Act
    mango_cache.set("fred_dashboard", dashboard_payload, ttl=60)
    result = mango_cache.get("fred_dashboard")

    # Assert
    assert result is None
    assert not mango_cache._entry_path("fred_dashboard").exists()


def test_set_does_not_raise_when_cache_directory_is_unwritable(monkeypatch):
    # Arrange: point at a directory whose creation always fails.
    def _boom(self, parents=True, exist_ok=True):
        raise OSError("permission denied")

    monkeypatch.setattr(mango_cache.Path, "mkdir", _boom)

    # Act / Assert — must not raise.
    mango_cache.set("some_key", {"a": 1}, ttl=60)
    assert mango_cache.get("some_key") is None


def test_cached_at_is_recorded_alongside_the_value():
    # Act
    mango_cache.set("audit_key", {"a": 1}, ttl=60)
    path = mango_cache._entry_path("audit_key")
    entry = json.loads(path.read_text(encoding="utf-8"))

    # Assert
    assert "cached_at" in entry
    assert isinstance(entry["cached_at"], str)


def _reload_with_env(monkeypatch, value: str | None):
    """Re-import the module so import-time directory resolution runs again.

    CACHE_DIR is resolved at import rather than per call, so that a test can
    monkeypatch the module attribute and be certain nothing escapes to the
    operator's real cache. Verifying the env var therefore means reloading.
    """
    if value is None:
        monkeypatch.delenv("CACHE_DIR", raising=False)
    else:
        monkeypatch.setenv("CACHE_DIR", value)
    return importlib.reload(mango_cache)


def test_cache_dir_module_attribute_redirects_writes(monkeypatch, tmp_path):
    # The isolation contract the shared `tmp_cache_dir` fixture depends on.
    custom_dir = tmp_path / "custom-cache-location"
    monkeypatch.setattr(mango_cache, "CACHE_DIR", custom_dir)

    mango_cache.set("env_key", {"a": 1}, ttl=60)

    assert custom_dir.exists()
    assert any(custom_dir.iterdir())


def test_cache_dir_env_var_is_honoured_at_import(monkeypatch, tmp_path):
    custom_dir = tmp_path / "from-env"

    reloaded = _reload_with_env(monkeypatch, str(custom_dir))

    try:
        assert reloaded.CACHE_DIR == custom_dir
    finally:
        _reload_with_env(monkeypatch, None)


def test_cache_dir_defaults_under_dot_terminalq_when_env_var_unset(monkeypatch):
    reloaded = _reload_with_env(monkeypatch, None)

    try:
        assert reloaded.CACHE_DIR == Path.home() / ".terminalq" / "cache"
        # It must never default inside the package's own source tree.
        assert "terminalq-extensions" not in str(reloaded.CACHE_DIR)
    finally:
        _reload_with_env(monkeypatch, None)


def test_cache_dir_env_var_expands_user_home(monkeypatch, tmp_path):
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    reloaded = _reload_with_env(monkeypatch, "~/cache-under-home")

    try:
        assert reloaded.CACHE_DIR == fake_home / "cache-under-home"
    finally:
        monkeypatch.delenv("HOME", raising=False)
        _reload_with_env(monkeypatch, None)

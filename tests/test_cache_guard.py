"""Errors must never be cached — see cache_guard for the incident this encodes."""

from mango.cache_guard import contains_error, should_cache


def test_returns_false_for_clean_payload():
    assert contains_error({"latest_value": 4.63, "series": "DGS10"}) is False
    assert should_cache({"latest_value": 4.63}) is True


def test_detects_top_level_error():
    assert contains_error({"error": "Request timed out", "source": "fred"}) is True
    assert should_cache({"error": "Request timed out"}) is False


def test_detects_nested_dashboard_error():
    # The shape that poisoned fred_dashboard.json on 2026-08-06.
    payload = {
        "indicators": {
            "unemployment": {"latest_value": 4.2},
            "gdp": {"error": "Connection failed"},
        }
    }
    assert contains_error(payload) is True


def test_partial_failure_is_still_not_cacheable():
    payload = {"indicators": {"a": {"v": 1}, "b": {"v": 2}, "c": {"error": "HTTP 502"}}}
    assert should_cache(payload) is False


def test_detects_error_inside_list():
    assert contains_error({"results": [{"v": 1}, {"error": "boom"}]}) is True


def test_falsy_error_value_is_not_an_error():
    assert contains_error({"error": None, "latest_value": 1.0}) is False
    assert contains_error({"error": "", "latest_value": 1.0}) is False


def test_non_dict_values_are_clean():
    assert contains_error("error") is False
    assert contains_error(42) is False
    assert contains_error(None) is False


def test_deep_nesting_beyond_scan_depth_is_ignored():
    deep = {"a": {"b": {"c": {"d": {"e": {"f": {"error": "too deep"}}}}}}}
    assert contains_error(deep) is False

"""Credentials must never reach a persisted artifact."""

import pytest

from terminalq.mango import redact as redact_mod
from terminalq.mango.redact import REDACTED, redact, redact_text

FRED_ERROR = (
    "Client error '403 Forbidden' for url "
    "'https://api.stlouisfed.org/fred/series/observations"
    "?series_id=PSAVERT&api_key=74b4f3f84060d51161b9b8aa07dec423&file_type=json'"
)


@pytest.fixture
def live_key(monkeypatch):
    key = "74b4f3f84060d51161b9b8aa07dec423"
    monkeypatch.setenv("FRED_API_KEY", key)
    return key


def test_removes_a_live_key_from_an_error_string(live_key):
    result = redact_text(FRED_ERROR)

    assert live_key not in result
    assert REDACTED in result


def test_preserves_the_surrounding_diagnostic_text(live_key):
    result = redact_text(FRED_ERROR)

    # The message must stay useful for debugging after scrubbing.
    assert "403 Forbidden" in result
    assert "series_id=PSAVERT" in result


def test_redacts_key_shaped_params_even_when_not_in_the_environment(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)

    result = redact_text("https://x.test/v1?apikey=abcdef0123456789&z=1")

    assert "abcdef0123456789" not in result
    assert "z=1" in result


@pytest.mark.parametrize("param", ["api_key", "api-key", "apikey", "token", "secret"])
def test_covers_common_credential_param_names(param):
    result = redact_text(f"https://x.test/?{param}=supersecretvalue")

    assert "supersecretvalue" not in result


def test_walks_nested_payload_structures(live_key):
    payload = {"mc_PSAVERT": {"error": FRED_ERROR}, "items": [{"e": FRED_ERROR}]}

    result = redact(payload)

    assert live_key not in str(result)
    assert isinstance(result["items"], list)  # structure preserved


def test_leaves_clean_payloads_byte_identical(live_key):
    payload = {"cape": {"latest": 42.19, "note": "no secrets here"}, "n": [1, 2, None]}

    assert redact(payload) == payload


def test_does_not_blank_short_env_values(monkeypatch):
    # A 2-character secret would otherwise match everywhere and shred the text.
    monkeypatch.setenv("FRED_API_KEY", "ab")

    assert redact_text("a grab bag of absolutely ordinary words") == (
        "a grab bag of absolutely ordinary words"
    )


def test_longer_secret_is_replaced_before_a_shorter_overlapping_one(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "abcdefgh")
    monkeypatch.setenv("BRAVE_API_KEY", "abcdefgh12345678")

    result = redact_text("key=abcdefgh12345678 end")

    # Replacing the short one first would leave "REDACTED12345678" behind.
    assert "12345678 end" not in result
    assert result == f"key={REDACTED} end"


def test_non_string_scalars_pass_through():
    assert redact(42) == 42
    assert redact(None) is None
    assert redact(True) is True


def test_secret_env_var_list_covers_the_keys_this_project_uses():
    for name in ("FRED_API_KEY", "FINNHUB_API_KEY", "BRAVE_API_KEY"):
        assert name in redact_mod.SECRET_ENV_VARS

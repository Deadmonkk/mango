"""Tests for crypto_analytics helpers."""

from terminalq.providers import crypto_analytics


def test_satoshi_to_btc_converts_positive_values():
    assert crypto_analytics._satoshi_to_btc(150_000_000) == 1.5


def test_satoshi_to_btc_rejects_impossible_values():
    """blockchain.info sometimes returns negative fee totals — never report them."""
    assert crypto_analytics._satoshi_to_btc(-43_125_000_000) is None
    assert crypto_analytics._satoshi_to_btc(0) is None
    assert crypto_analytics._satoshi_to_btc(None) is None

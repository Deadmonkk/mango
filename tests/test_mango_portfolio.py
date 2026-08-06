"""Tests for terminalq.mango.portfolio.

All fixtures are synthetic (made-up tickers, round dollar amounts) written
into `tmp_path` — never real holdings. `PORTFOLIO_DIR` is monkeypatched to
`tmp_path` for every test so the module never touches `~/.terminalq/`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from terminalq.mango import portfolio


@pytest.fixture(autouse=True)
def _isolated_portfolio_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the module at an isolated tmp_path directory for every test."""
    monkeypatch.setattr(portfolio, "PORTFOLIO_DIR", tmp_path)
    return tmp_path


def _write(tmp_path: Path, filename: str, content: str) -> None:
    (tmp_path / filename).write_text(content, encoding="utf-8")


# --- load_portfolio: multi-account parsing ----------------------------------


def test_load_portfolio_attributes_rows_to_the_correct_account(tmp_path: Path) -> None:
    # Arrange
    content = """# Portfolio Holdings (as of Mar 3, 2026)

## Alpha Account (0001)

| Symbol | Name | Shares | Cost Basis | Market Value | Unrealized G/L |
|--------|------|--------|------------|--------------|----------------|
| ZZZ | Zephyr Fund | 10 | 1000 | 1200 | 200 |

## Beta Account (0002)

| Symbol | Name | Shares | Cost Basis | Market Value | Unrealized G/L |
|--------|------|--------|------------|--------------|----------------|
| QQQ | Quasar Fund | 20 | 2000 | 1800 | -200 |
"""
    _write(tmp_path, "portfolio-holdings.md", content)

    # Act
    holdings = portfolio.load_portfolio()

    # Assert
    assert [h["account"] for h in holdings] == ["Alpha Account", "Beta Account"]
    assert holdings[0]["symbol"] == "ZZZ"
    assert holdings[1]["symbol"] == "QQQ"


def test_load_portfolio_strips_trailing_parenthetical_from_account_heading(tmp_path: Path) -> None:
    # Arrange
    content = """# Portfolio Holdings (as of Mar 3, 2026)

## Gamma Account (9999)

| Symbol | Name | Shares | Cost Basis | Market Value | Unrealized G/L |
|--------|------|--------|------------|--------------|----------------|
| FOO | Foo Fund | 5 | 500 | 550 | 50 |
"""
    _write(tmp_path, "portfolio-holdings.md", content)

    # Act
    holdings = portfolio.load_portfolio()

    # Assert
    assert holdings[0]["account"] == "Gamma Account"


def test_load_portfolio_returns_empty_list_when_file_missing(tmp_path: Path) -> None:
    # Arrange (no file written)

    # Act
    holdings = portfolio.load_portfolio()

    # Assert
    assert holdings == []


# --- load_portfolio: numeric coercion ---------------------------------------


def test_load_portfolio_coerces_dollar_comma_and_negative_formats(tmp_path: Path) -> None:
    # Arrange
    content = """# Portfolio Holdings (as of Mar 3, 2026)

## Delta Account

| Symbol | Name | Shares | Cost Basis | Market Value | Unrealized G/L |
|--------|------|--------|------------|--------------|----------------|
| BAR | Bar Fund | 1,000 | $10,000.00 | $9,500.00 | (500.00) |
| BAZ | Baz Fund | — | - |  |  |
"""
    _write(tmp_path, "portfolio-holdings.md", content)

    # Act
    holdings = portfolio.load_portfolio()

    # Assert
    bar, baz = holdings
    assert bar["shares"] == 1000.0
    assert bar["cost_basis"] == 10000.0
    assert bar["market_value"] == 9500.0
    assert bar["unrealized_gl"] == -500.0
    assert baz["shares"] == 0.0
    assert baz["cost_basis"] == 0.0
    assert baz["market_value"] == 0.0
    assert baz["unrealized_gl"] == 0.0


def test_load_portfolio_numeric_fields_are_always_float(tmp_path: Path) -> None:
    # Arrange
    content = """# Portfolio Holdings (as of Mar 3, 2026)

## Delta Account

| Symbol | Name | Shares | Cost Basis | Market Value | Unrealized G/L |
|--------|------|--------|------------|--------------|----------------|
| BAR | Bar Fund | 100 | 1000 | 1100 | 100 |
"""
    _write(tmp_path, "portfolio-holdings.md", content)

    # Act
    holding = portfolio.load_portfolio()[0]

    # Assert
    for key in ("shares", "cost_basis", "market_value", "unrealized_gl"):
        assert isinstance(holding[key], float)
    total = sum(h["market_value"] for h in portfolio.load_portfolio())
    assert total == 1100.0


# --- get_portfolio_as_of -----------------------------------------------------


def test_get_portfolio_as_of_extracts_h1_date_text(tmp_path: Path) -> None:
    # Arrange
    content = """# Portfolio Holdings (as of Mar 3, 2026)

## Delta Account

| Symbol | Name | Shares | Cost Basis | Market Value | Unrealized G/L |
|--------|------|--------|------------|--------------|----------------|
| BAR | Bar Fund | 100 | 1000 | 1100 | 100 |
"""
    _write(tmp_path, "portfolio-holdings.md", content)

    # Act
    as_of = portfolio.get_portfolio_as_of()

    # Assert
    assert as_of == "Mar 3, 2026"


def test_get_portfolio_as_of_returns_none_when_h1_absent(tmp_path: Path) -> None:
    # Arrange
    content = """## Delta Account

| Symbol | Name | Shares | Cost Basis | Market Value | Unrealized G/L |
|--------|------|--------|------------|--------------|----------------|
| BAR | Bar Fund | 100 | 1000 | 1100 | 100 |
"""
    _write(tmp_path, "portfolio-holdings.md", content)

    # Act
    as_of = portfolio.get_portfolio_as_of()

    # Assert
    assert as_of is None


def test_get_portfolio_as_of_returns_none_when_file_missing(tmp_path: Path) -> None:
    # Act
    as_of = portfolio.get_portfolio_as_of()

    # Assert
    assert as_of is None


# --- get_unique_symbols -------------------------------------------------------


def test_get_unique_symbols_is_order_stable_and_excludes_cash(tmp_path: Path) -> None:
    # Arrange
    content = """# Portfolio Holdings (as of Mar 3, 2026)

## Delta Account

| Symbol | Name | Shares | Cost Basis | Market Value | Unrealized G/L |
|--------|------|--------|------------|--------------|----------------|
| CASH | Cash | 100 | 100 | 100 | 0 |
| ZZZ | Zephyr Fund | 10 | 1000 | 1200 | 200 |
| QQQ | Quasar Fund | 5 | 500 | 550 | 50 |

## Echo Account

| Symbol | Name | Shares | Cost Basis | Market Value | Unrealized G/L |
|--------|------|--------|------------|--------------|----------------|
| ZZZ | Zephyr Fund | 20 | 2000 | 2400 | 400 |
| WWW | Wombat Fund | 1 | 100 | 110 | 10 |
"""
    _write(tmp_path, "portfolio-holdings.md", content)

    # Act
    symbols = portfolio.get_unique_symbols()

    # Assert
    assert symbols == ["ZZZ", "QQQ", "WWW"]
    assert "CASH" not in symbols


def test_get_unique_symbols_returns_empty_list_when_file_missing(tmp_path: Path) -> None:
    # Act
    symbols = portfolio.get_unique_symbols()

    # Assert
    assert symbols == []


# --- load_rsu_schedule ---------------------------------------------------------


def test_load_rsu_schedule_keeps_values_as_raw_strings_with_grant_section(tmp_path: Path) -> None:
    # Arrange
    content = """# RSU Vesting Schedule

## 2027 Grant: $40,000 Initial Value
Some notes here.

### Vesting Schedule

| Date | Grant | Pct of Grant | Est Value |
|------|-------|--------------|-----------|
| 2027-03-15 | 2027 Grant | 50% | $20,000 |
| 2027-09-15 | 2027 Grant | 50% | $20,000 |
"""
    _write(tmp_path, "rsu-schedule.md", content)

    # Act
    rows = portfolio.load_rsu_schedule()

    # Assert
    assert len(rows) == 2
    first = rows[0]
    assert first["date"] == "2027-03-15"
    assert first["grant"] == "2027 Grant"
    assert first["pct_of_grant"] == "50%"
    assert first["est_value"] == "$20,000"
    assert isinstance(first["est_value"], str)
    assert first["grant_section"] == "2027 Grant: $40,000 Initial Value"


def test_load_rsu_schedule_handles_multiple_grant_sections(tmp_path: Path) -> None:
    # Arrange
    content = """# RSU Vesting Schedule

## 2026 Grant: $10,000 Initial Value

### Vesting Schedule

| Date | Grant | Pct of Grant | Est Value |
|------|-------|--------------|-----------|
| 2026-05-01 | 2026 Grant | 100% | $10,000 |

## 2027 Grant: $20,000 Initial Value

### Vesting Schedule

| Date | Grant | Pct of Grant | Est Value |
|------|-------|--------------|-----------|
| 2027-05-01 | 2027 Grant | 100% | $20,000 |
"""
    _write(tmp_path, "rsu-schedule.md", content)

    # Act
    rows = portfolio.load_rsu_schedule()

    # Assert
    assert [r["grant_section"] for r in rows] == [
        "2026 Grant: $10,000 Initial Value",
        "2027 Grant: $20,000 Initial Value",
    ]


def test_load_rsu_schedule_returns_empty_list_when_file_missing(tmp_path: Path) -> None:
    # Act
    rows = portfolio.load_rsu_schedule()

    # Assert
    assert rows == []


# --- load_watchlist -------------------------------------------------------------


def test_load_watchlist_parses_symbol_name_notes(tmp_path: Path) -> None:
    # Arrange
    content = """# Watchlist

| Symbol | Name | Notes |
|--------|------|-------|
| ABC | Acme Corp | Testing thesis |
| XYZ | Xylo Inc | Watching for pullback |
"""
    _write(tmp_path, "watchlist.md", content)

    # Act
    rows = portfolio.load_watchlist()

    # Assert
    assert rows == [
        {"symbol": "ABC", "name": "Acme Corp", "notes": "Testing thesis"},
        {"symbol": "XYZ", "name": "Xylo Inc", "notes": "Watching for pullback"},
    ]


def test_load_watchlist_returns_empty_list_when_file_missing(tmp_path: Path) -> None:
    # Act
    rows = portfolio.load_watchlist()

    # Assert
    assert rows == []


# --- Malformed / ragged table tolerance ------------------------------------


def test_ragged_rows_do_not_raise_and_pad_missing_cells(tmp_path: Path) -> None:
    # Arrange: last data row is missing the trailing Unrealized G/L cell.
    content = """# Portfolio Holdings (as of Mar 3, 2026)

## Delta Account

| Symbol | Name | Shares | Cost Basis | Market Value | Unrealized G/L |
|--------|------|--------|------------|--------------|----------------|
| BAR | Bar Fund | 100 | 1000 | 1100
"""
    _write(tmp_path, "portfolio-holdings.md", content)

    # Act
    holdings = portfolio.load_portfolio()

    # Assert
    assert len(holdings) == 1
    assert holdings[0]["symbol"] == "BAR"
    assert holdings[0]["unrealized_gl"] == 0.0


def test_separator_row_never_becomes_a_data_row(tmp_path: Path) -> None:
    # Arrange
    content = """# Watchlist

| Symbol | Name | Notes |
|--------|------|-------|
| ABC | Acme Corp | Testing thesis |
"""
    _write(tmp_path, "watchlist.md", content)

    # Act
    rows = portfolio.load_watchlist()

    # Assert
    assert len(rows) == 1
    assert all("-" not in row["symbol"] for row in rows)


def test_watchlist_tolerates_extra_whitespace_and_missing_edge_pipes(tmp_path: Path) -> None:
    # Arrange
    content = """# Watchlist

Symbol | Name | Notes
-------|------|------
  ABC   |  Acme Corp  |  Testing thesis
"""
    _write(tmp_path, "watchlist.md", content)

    # Act
    rows = portfolio.load_watchlist()

    # Assert
    assert rows == [{"symbol": "ABC", "name": "Acme Corp", "notes": "Testing thesis"}]

"""Sidecar relevance filtering.

The 2026-08-10 macro run is the fixture that matters: its six top-ranked
clusters all matched the bare token "federal" and none concerned markets. The
filter has to drop those while keeping the Polymarket odds from the same run.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fr_sidecar import Item, is_relevant, parse_items, render, select  # noqa: E402

# Verbatim titles from the 2026-08-10 run.
NOISE_TITLES = (
    "Federal Communications Commission scraps limit on broadcast TV ownership",
    "Former Federal Prosecutors to Senate: Stop Confirming Election Deniers as Judges",
    "Federal proposal would deeply cut Colorado River water for AZ, CA and NV",
    "Federal prosecutors > Trump administration's claims of vandalism were false",
    "Trump administration Is Repurposing Federal Land for A.I. Data Centers",
    "Federal loan support cut for degree programs that lead to low wages",
)

SIGNAL_TITLES = (
    "Fed rate cut by...?",
    "Federal Reserve holds rates steady as inflation cools",
    "MicroStrategy Sells More Bitcoin to Fix STRC Stock: Will It Work?",
    "Bitcoin cold-wallet attack spreads to 4,500 addresses as losses near $89M",
)


def test_every_known_noise_title_is_dropped():
    for title in NOISE_TITLES:
        assert not is_relevant(title), f"kept noise: {title}"


def test_market_relevant_titles_survive():
    for title in SIGNAL_TITLES:
        assert is_relevant(title), f"dropped signal: {title}"


def test_a_bare_domain_token_is_not_enough_on_its_own():
    assert not is_relevant("Federal building renamed after local hero")


def test_a_domain_token_paired_with_a_market_token_qualifies():
    assert is_relevant("Fed signals one more rate move this year")


def test_polymarket_rows_are_kept_even_when_the_title_reads_as_noise():
    items = [Item("polymarket", "Fed rate cut by...?", "down 6.5% this month"),
             Item("hackernews", NOISE_TITLES[0], "")]
    kept = select(items)
    assert len(kept) == 1
    assert kept[0].source == "polymarket"


def test_prediction_markets_are_ranked_ahead_of_discussion():
    items = [Item("hackernews", "Bitcoin rallies as ETF flows turn positive", ""),
             Item("polymarket", "Fed emergency rate cut before 2027?", "down 1.0%")]
    assert select(items)[0].source == "polymarket"


def test_duplicate_titles_collapse():
    items = [Item("reddit", "Bitcoin ETF flows turn positive", ""),
             Item("hackernews", "bitcoin etf flows turn positive", "")]
    assert len(select(items)) == 1


def test_output_is_capped():
    items = [Item("polymarket", f"rate cut market {i}", "x") for i in range(50)]
    assert len(select(items, max_items=6)) == 6


def test_parse_reads_the_compact_emit_format():
    raw = """
### 1. Fed rate cut by...? (score 0, 1 item, sources: Polymarket)
1. [polymarket] Fed rate cut by...?
   - 2026-08-10 | [729175.9volume, 264564.7liquidity] | score:0
   - Evidence: down 6.5% this month
"""
    items = parse_items(raw)
    assert len(items) == 1
    assert items[0].source == "polymarket"
    assert "Fed rate cut" in items[0].title


def test_rendered_block_is_small_and_labelled_external():
    items = [Item("polymarket", f"rate cut {i}", "d" * 500) for i in range(6)]
    block = render("macro", select(items), 40)
    assert "EXTERNAL" in block
    assert "never a scored input" in block
    assert len(block) // 4 < 500, "a sidecar leg must stay well under 500 tokens"


def test_an_empty_result_says_so_rather_than_inventing_a_read():
    block = render("macro", [], 12)
    assert "nothing market-relevant surfaced" in block

"""Report-intelligence tools: the prediction ledger, regime history, introspection.

The ledger is the honesty mechanism for the whole system: it records dated,
falsifiable calls and grades them later against real prices. Its value depends
entirely on predictions being logged BEFORE the outcome is known, so nothing
here lets a call be edited or back-dated after the fact.
"""

from __future__ import annotations

from mango import history, voice
from mango.analytics import prediction_grader, regime_history
from mango.core import audit, usage_tracker
from mango.providers import event_scenarios, prediction_markets, reports
from mango.server import tool


@tool
async def record_snapshot(
    equity_regime: float | None = None,
    crypto_regime: float | None = None,
    btc: float | None = None,
    eth: float | None = None,
    fear_greed: float | None = None,
    spx: float | None = None,
    vix: float | None = None,
    ten_year: float | None = None,
    hy_spread: float | None = None,
    gold: float | None = None,
    wti: float | None = None,
    dxy: float | None = None,
    stablecoin_supply_b: float | None = None,
    btc_etf_flow_m: float | None = None,
    cpi_mom: float | None = None,
    claims_k: float | None = None,
    fed_path: str = "",
    notes: str = "",
    data_quality: str = "",
    snapshot_date: str = "",
) -> dict:
    """Record one report snapshot for later calibration.

    Only pass values that came from a tool result in this run. A snapshot is
    what the regime-score calibration is later measured against, so a filled-in
    number corrupts the record permanently.

    Set ``data_quality="degraded"`` when a SCORED input fell back to a lesser
    source or dropped out entirely — a renormalised leg, or a single-venue
    funding read standing in for the market-wide aggregate. Such a score can
    cross a band boundary on data quality alone (observed three times on
    2026-08-12), so calibration must be able to exclude it rather than treat it
    as equivalent to a clean run. Leave empty for a clean run.
    """
    return history.record_snapshot(
        equity_regime=equity_regime, crypto_regime=crypto_regime, btc=btc, eth=eth,
        fear_greed=fear_greed, spx=spx, vix=vix, ten_year=ten_year, hy_spread=hy_spread,
        gold=gold, wti=wti, dxy=dxy, stablecoin_supply_b=stablecoin_supply_b,
        btc_etf_flow_m=btc_etf_flow_m, cpi_mom=cpi_mom, claims_k=claims_k,
        fed_path=fed_path, notes=notes, data_quality=data_quality,
        snapshot_date=snapshot_date,
    )


@tool
async def get_regime_history(forward_days: int = 30) -> dict:
    """Realised forward returns grouped by regime-score band.

    Reports matured sample counts honestly. A band with few samples, or only
    one band populated, shows the scores are stable — not that they are right.
    """
    return await regime_history.get_regime_history(forward_days)


@tool
async def log_prediction(
    claim: str,
    symbol: str,
    direction: str,
    horizon_days: int = 30,
    baseline: float | None = None,
) -> dict:
    """Log a dated, falsifiable prediction to the ledger.

    `baseline` is the level the call is measured from and is required for
    grading. Log before the outcome is known; the record is only worth
    something if it can embarrass you.
    """
    return history.log_prediction(claim, symbol, direction, horizon_days, baseline)


@tool
async def grade_predictions() -> dict:
    """Settle every due prediction against actual prices and report the record.

    Each call settles on the close at its due date, not on the day grading ran,
    so a late run still measures the horizon that was actually predicted.
    """
    return await prediction_grader.grade_open_predictions()


@tool
async def load_recent_reports(n: int = 7) -> dict:
    """Key sections of recent saved reports, for comparing against earlier runs."""
    return await reports.load_recent_reports(n)


@tool
async def get_event_scenarios(days: int = 7) -> dict:
    """Upcoming releases with what a hotter or cooler print would imply.

    Anchored to the latest recorded regime, so the reaction function is
    pre-computed rather than improvised after the number lands.
    """
    return await event_scenarios.get_event_scenarios(days)


@tool
async def get_prediction_markets(topic: str = "Fed rate") -> dict:
    """Real-money prediction-market odds. External; a cross-check, never an input."""
    return await prediction_markets.get_prediction_markets(topic)


@tool
async def speak(text: str, voice_name: str = "") -> dict:
    """Read text aloud through the system voice."""
    return await voice.speak(text, voice_name)


# --- Introspection: deliberately NOT audited ------------------------------
# Logging a read of the audit log grows the file every time it is read and
# buries the entries someone was looking for.

@tool(audited=False)
async def get_audit_log(date: str = "") -> dict:
    """Recent tool calls with timings and failures. Not itself audited."""
    return audit.get_audit_log()


@tool(audited=False)
async def get_usage_stats() -> dict:
    """Per-tool call counts, failure rates and payload sizes. Not itself audited."""
    summary = audit.get_audit_summary()
    summary["daily_calls"] = await usage_tracker.get_daily_usage("all_tools")
    return summary

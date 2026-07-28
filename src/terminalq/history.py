"""Local append-only history store for FR snapshots and the prediction ledger.

Each Full Report run computes a structured set of headline metrics and the two
regime scores. Persisting one clean row per run turns the report archive into a
time series we can mine — forward-return calibration of the regime scores, and a
graded track record of the calls each report makes. Stored as JSON Lines under
``~/.terminalq/history/`` (private, gitignored), one record per line.

This is storage only — no network, no analytics. It never raises: callers get a
result dict or an empty list, matching the provider error convention elsewhere.
"""

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from terminalq.logging_config import log

from terminalq.ext_settings import PORTFOLIO_DIR

HISTORY_DIR = PORTFOLIO_DIR / "history"
SNAPSHOTS_FILE = HISTORY_DIR / "snapshots.jsonl"
PREDICTIONS_FILE = HISTORY_DIR / "predictions.jsonl"

# Known snapshot metric keys — extra keys are still stored, these document intent.
SNAPSHOT_METRIC_KEYS = (
    "equity_regime",
    "crypto_regime",
    "btc",
    "eth",
    "fear_greed",
    "spx",
    "vix",
    "ten_year",
    "hy_spread",
    "gold",
    "wti",
    "dxy",
    "stablecoin_supply_b",
    "btc_etf_flow_m",
    "cpi_mom",
    "claims_k",
    "fed_path",
    "notes",
)


def _append_jsonl(path: Path, record: dict) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            log.warning("Skipping corrupt history line in %s", path.name)
    return records


# ── Snapshots ──────────────────────────────────────────────────────────────


def record_snapshot(snapshot_date: str | None = None, **metrics) -> dict:
    """Append one FR snapshot. Only non-None metrics are stored.

    Args:
        snapshot_date: ISO date (YYYY-MM-DD). Defaults to today's LOCAL date,
            which is what the daily report is keyed on. Note ``recorded_at``
            is UTC — the two can straddle midnight in either direction.
        **metrics: Any of SNAPSHOT_METRIC_KEYS plus arbitrary extras.

    Returns:
        The stored record (with ``recorded_at`` timestamp).
    """
    record = {
        "date": snapshot_date or date.today().isoformat(),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    record.update({k: v for k, v in metrics.items() if v is not None})
    _append_jsonl(SNAPSHOTS_FILE, record)
    log.info("Recorded FR snapshot for %s (%d metrics)", record["date"], len(record) - 2)
    return record


def load_snapshots() -> list[dict]:
    """All recorded snapshots, oldest first (file order)."""
    return _read_jsonl(SNAPSHOTS_FILE)


def latest_snapshot_per_day() -> list[dict]:
    """One snapshot per calendar day — the last recorded that day wins."""
    by_day: dict[str, dict] = {}
    for snap in load_snapshots():
        by_day[snap.get("date", "")] = snap  # later lines overwrite earlier same-day
    return [by_day[d] for d in sorted(by_day)]


# ── Predictions ────────────────────────────────────────────────────────────


def log_prediction(
    claim: str,
    symbol: str,
    direction: str,
    horizon_days: int,
    baseline: float | None = None,
    target: float | None = None,
) -> dict:
    """Append a falsifiable, dated prediction to the ledger.

    Args:
        claim: Human-readable claim (e.g. "BTC reclaims its 200-day").
        symbol: yfinance symbol whose move settles the claim (e.g. BTC-USD, ^GSPC).
        direction: "up" or "down" relative to baseline.
        horizon_days: Days until the claim is due for grading.
        baseline: Reference value at creation; grading fetches the outcome later.
        target: Optional explicit price target.

    Returns:
        The stored prediction record (status "open").
    """
    created = date.today()
    record = {
        "id": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"),
        "created": created.isoformat(),
        "claim": claim,
        "symbol": symbol,
        "direction": direction.lower().strip(),
        "horizon_days": horizon_days,
        "due": (created + timedelta(days=horizon_days)).isoformat(),
        "baseline": baseline,
        "target": target,
        "status": "open",
        "outcome": None,
    }
    _append_jsonl(PREDICTIONS_FILE, record)
    log.info("Logged prediction %s: %s", record["id"], claim)
    return record


def load_predictions() -> list[dict]:
    """All predictions with the latest state per id (later lines supersede)."""
    by_id: dict[str, dict] = {}
    for pred in _read_jsonl(PREDICTIONS_FILE):
        pid = pred.get("id")
        if pid:
            by_id[pid] = pred
    return list(by_id.values())


def update_prediction(pred_id: str, **updates) -> bool:
    """Append an updated copy of a prediction (grading result, status change).

    Append-only: the newest line for an id wins via ``load_predictions``.
    Returns True if the id existed.
    """
    current = {p["id"]: p for p in load_predictions()}
    if pred_id not in current:
        return False
    updated = {**current[pred_id], **updates, "graded_at": datetime.now(timezone.utc).isoformat()}
    _append_jsonl(PREDICTIONS_FILE, updated)
    return True

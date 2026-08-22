"""Finalize a report: inject prose, verify, then record — atomically and once.

WHY THIS EXISTS
---------------
Everything after "the model has written its prose" is deterministic: injection,
two verifications, the calibration snapshot, the prediction ledger, the
publication stamp and the site build. Done as separate tool calls that was seven
LLM round-trips per report, and on 2026-08-16 the replay cost of those handoffs
was roughly 86k of a 97k-token run. None of them needed judgment.

They also needed to be TRANSACTIONAL, which separate calls cannot be. The
snapshot and the prediction ledger are append-only permanent stores: a row
written from an unverified run is a row that quietly corrupts calibration
forever. So verification gates recording, in one process:

    stage 1  inject prose
    stage 2  verify prose + verify sources   -> FAIL stops here, nothing recorded
    stage 3  snapshot -> predictions -> stamp -> build

FAIL vs WARNING
---------------
Only correctness failures stop the run: prose that drifted from the tables, or a
figure that disagrees with its primary source. Degradation is a WARNING and must
not block — a provider outage, a cached fallback, a renormalised score component
are all expected, correctly reported states of a good run. Blocking on those
would re-add the round-trips this exists to remove.

IDEMPOTENCE
-----------
Re-running is safe by design. Predictions are logged at most once per day.
An identical snapshot is not re-appended. The stamp is content-addressed. The
site build is already idempotent. A retry, a double-invocation or an operator
re-run is a no-op, not a second set of records.

USAGE
-----
    uv run python scripts/fr_finalize.py --report <report.md> --prose <prose.json>
    ... --json          machine-readable result record
    ... --skip-build    everything except the site rebuild
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import fr_lint_labels
import fr_prose
import fr_report_state
import fr_verify_prose
import fr_verify_sources
from mango import history

SITE_BUILD = Path.home() / "Projects" / "mango-site" / "build_site.py"

# fr_values label -> record_snapshot keyword. Only labels that map to a snapshot
# field; everything else in the values file is carried by the report itself.
SNAPSHOT_MAP: dict[str, str] = {
    "Equity Regime Score": "equity_regime",
    "Crypto Regime Score": "crypto_regime",
    "BTC price": "btc",
    "ETH price": "eth",
    "Fear & Greed": "fear_greed",
    "S&P 500": "spx",
    "VIX": "vix",
    "10y Treasury": "ten_year",
    "HY spread": "hy_spread",
    "Gold (COMEX front month)": "gold",
    "WTI crude (Cushing spot)": "wti",
    "Dollar index (FRED broad, Jan 2006=100 — NOT ICE DXY)": "dxy",
    "BTC ETF flow (latest day)": "btc_etf_flow_m",
    "CPI m/m change": "cpi_mom",
    "Initial claims": "claims_k",
}

# Regime-score bands that decide the crypto forward-test direction. Defined in
# the FR playbook; restated here because this file must not need the model to
# make the call.
CRYPTO_UP_AT = 50.0
CRYPTO_DOWN_BELOW = 45.0


def _site_interpreter() -> str | None:
    """An interpreter that can actually run the site builder.

    NOT sys.executable, and not bare "python3": this runs under `uv run`, which
    puts the mango venv first on PATH, and that venv has no `markdown`. The site
    builder belongs to a different project with its own dependencies, so the
    interpreter is chosen by capability rather than assumed.

    Probes for `mango` alongside `markdown`: an interpreter with `markdown` but
    no `mango` install still "passes" the site build (no exception — build_site.py
    catches the ImportError itself and degrades to an empty climate payload), so
    checking `markdown` alone silently picked /opt/homebrew's python3 for months
    while its `mango` install was stale, and every climate.html render came out
    "0/0 regions" with no error anywhere in the pipeline. Found 2026-08-22.
    """
    candidates = [
        shutil.which("python3", path="/opt/homebrew/bin:/usr/local/bin:/usr/bin"),
        "/opt/anaconda3/bin/python3",
        shutil.which("python3"),
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        probe = subprocess.run(
            [candidate, "-c", "import markdown, mango.providers.climate"], capture_output=True
        )
        if probe.returncode == 0:
            return candidate
    return None


def _load_values(report: Path) -> tuple[dict, Path]:
    """The flat metric snapshot for this report's date."""
    stem = report.stem.rsplit("-", 1)[0]  # 2026-08-16-fr -> 2026-08-16
    values_path = report.parent / ".briefs" / f"fr_values_{stem}.json"
    if not values_path.exists():
        raise FileNotFoundError(f"no values file for this run: {values_path}")
    return json.loads(values_path.read_text()), values_path


def _num(values: dict, label: str) -> float | None:
    value = values.get(label)
    return float(value) if isinstance(value, (int, float)) else None


# Keys in prose.json that are metadata for the snapshot rather than report slots.
# They carry the only two finalization inputs that genuinely need judgment, so
# they ride along with the prose instead of costing their own round-trip.
META_KEYS = frozenset({"notes"})


def _slots(prose: dict) -> dict:
    return {k: v for k, v in prose.items() if k not in META_KEYS}


def stage_inject(report: Path, prose_path: Path) -> dict:
    prose = _slots(json.loads(prose_path.read_text()))
    text = report.read_text()
    unknown = sorted(set(prose) - fr_prose.known_keys(text))
    if unknown:
        return {"ok": False, "error": f"unknown prose slots: {', '.join(unknown)}"}
    report.write_text(fr_prose.inject_all(text, prose))
    remaining = fr_prose.unfilled(report.read_text())
    if remaining:
        return {"ok": False, "error": f"slots left unfilled: {', '.join(remaining)}"}
    return {"ok": True, "filled": len(prose)}


def stage_verify(report: Path, values: dict) -> dict:
    text = report.read_text()
    prose, tables = fr_verify_prose.split_report(text)
    numbers = fr_verify_prose.unverified_numbers(prose, tables)
    claims = fr_verify_prose.unsupported_failure_claims(prose, tables)
    sources = fr_verify_sources.check(values)
    # A WARNING by design: whether a label describes its value is a judgement,
    # and this lints the field definitions rather than this run's data — it only
    # changes when the code does. Blocking a report on it would gate today's
    # numbers on a naming question that has nothing to do with them.
    labels = fr_lint_labels.lint()
    failed = bool(numbers or claims or sources["mismatches"])
    return {
        "ok": not failed,
        "unverified_numbers": numbers,
        "unsupported_failure_claims": claims,
        "source_mismatches": sources["mismatches"],
        "warnings": {
            "sources_unreachable": sources["unreachable"],
            "sources_not_produced": sources["skipped"],
            "sources_checked": sources["checked"],
            "label_suspects": labels,
        },
    }


def _already_logged_today() -> bool:
    today = date.today().isoformat()
    return any(
        p.get("created") == today and "EOD" not in p.get("claim", "")
        for p in history.load_predictions()
    )


def _snapshot_is_duplicate(payload: dict) -> bool:
    """True when today's most recent snapshot already holds these values."""
    todays = [s for s in history.load_snapshots() if s.get("date") == date.today().isoformat()]
    if not todays:
        return False
    last = todays[-1]
    return all(last.get(k) == v for k, v in payload.items() if v is not None)


def stage_record(values: dict, notes: str, degraded: bool, skip_build: bool) -> dict:
    payload = {
        field: _num(values, label)
        for label, field in SNAPSHOT_MAP.items()
        if _num(values, label) is not None
    }
    stable = _num(values, "Stablecoin supply")
    if stable is not None:
        payload["stablecoin_supply_b"] = round(stable / 1e9, 2)
    fed = _num(values, "Fed path implied rate")
    if fed is not None:
        payload["fed_path"] = f"implied {fed}%"

    result: dict = {"snapshot": None, "predictions": [], "build": None}

    if _snapshot_is_duplicate(payload):
        result["snapshot"] = "skipped (identical to today's latest)"
    else:
        history.record_snapshot(
            notes=notes,
            data_quality="degraded" if degraded else "",
            **payload,
        )
        result["snapshot"] = f"recorded ({len(payload)} metrics)"

    if _already_logged_today():
        result["predictions"] = ["skipped (already logged today)"]
    else:
        crypto_score = _num(values, "Crypto Regime Score")
        btc, btc_sma = _num(values, "BTC price"), _num(values, "BTC 200d SMA")
        spx = _num(values, "S&P 500")
        calls = []
        if btc_sma is not None:
            calls.append(("BTC reclaims its 200-day MA", "BTC-USD", "up", btc_sma))
        if spx is not None:
            calls.append(
                ("S&P 500 higher in 30d (equity-regime forward test)", "^GSPC", "up", spx)
            )
        if btc is not None and crypto_score is not None:
            direction = "up" if crypto_score >= CRYPTO_UP_AT else (
                "down" if crypto_score < CRYPTO_DOWN_BELOW else "up"
            )
            calls.append(
                (
                    f"BTC 30d direction implied by crypto regime {crypto_score}",
                    "BTC-USD",
                    direction,
                    btc,
                )
            )
        for claim, symbol, direction, baseline in calls:
            history.log_prediction(
                claim=claim,
                symbol=symbol,
                direction=direction,
                horizon_days=30,
                baseline=baseline,
            )
            result["predictions"].append(f"{claim} [{direction} from {baseline}]")

    if skip_build:
        result["build"] = "skipped"
    else:
        interpreter = _site_interpreter()
        if interpreter is None:
            result["build"] = "FAILED: no interpreter found with `markdown` installed"
            return result
        proc = subprocess.run(
            [interpreter, str(SITE_BUILD)], capture_output=True, text=True
        )
        result["build"] = (
            proc.stdout.strip().splitlines()[-1] if proc.returncode == 0
            else f"FAILED: {proc.stderr.strip()[:300]}"
        )
    return result


def finalize(report: Path, prose_path: Path, skip_build: bool) -> dict:
    values, values_path = _load_values(report)
    out: dict = {"status": "PASS", "report": str(report), "values": str(values_path)}

    injected = stage_inject(report, prose_path)
    out["inject"] = injected
    if not injected["ok"]:
        out.update(status="FAILED", repair_required=True, stage="inject")
        return out

    verified = stage_verify(report, values)
    out["verify"] = verified
    if not verified["ok"]:
        out.update(status="FAILED", repair_required=True, stage="verify")
        return out

    prose = json.loads(prose_path.read_text())
    notes = prose.get("notes", "").strip()
    degraded = fr_verify_prose.FAIL in report.read_text()

    report.write_text(fr_report_state.stamp(report.read_text()))
    out["state"] = fr_report_state.state(report.read_text())
    out["record"] = stage_record(values, notes, degraded, skip_build)
    out["repair_required"] = False
    return out


def render(out: dict) -> None:
    if out["status"] == "PASS":
        warn = out["verify"]["warnings"]
        print(f"FINALIZED — {Path(out['report']).name} [{out['state']}]")
        print(f"  sources verified : {warn['sources_checked']} agree with primary")
        print(f"  snapshot         : {out['record']['snapshot']}")
        for line in out["record"]["predictions"]:
            print(f"  prediction       : {line}")
        print(f"  site build       : {out['record']['build']}")
        for u in warn["sources_unreachable"]:
            print(f"  WARNING          : could not verify {u['metric']} ({u['reason']})")
        for s_ in warn["label_suspects"]:
            print(f"  WARNING          : label may not match value — {s_['label']} <- {s_['path']}")
        if warn["sources_not_produced"]:
            print(f"  WARNING          : {len(warn['sources_not_produced'])} metric(s) not produced this run")
        return

    print(f"FAILED at stage: {out['stage']} — NOTHING recorded, NOTHING published.")
    if out["stage"] == "inject":
        print(f"  {out['inject']['error']}")
        return
    verify = out["verify"]
    for token in verify["unverified_numbers"]:
        print(f"  unverified number : {token}")
    for claim in verify["unsupported_failure_claims"]:
        print(f"  unsupported claim : {claim}")
    for m in verify["source_mismatches"]:
        print(
            f"  source mismatch   : {m['metric']} report {m['reported']:,.4g} "
            f"vs primary {m['primary']:,.4g}"
        )
    print("  Fix ALL of the above in ONE rewrite, then re-run. Never patch iteratively.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", required=True, type=Path)
    ap.add_argument("--prose", required=True, type=Path)
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    for path in (args.report, args.prose):
        if not path.exists():
            print(f"no such file: {path}")
            return 1

    out = finalize(args.report, args.prose, args.skip_build)
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        render(out)
    return 1 if out["status"] == "FAILED" else 0


if __name__ == "__main__":
    sys.exit(main())

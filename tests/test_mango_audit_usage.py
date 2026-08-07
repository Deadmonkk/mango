"""Audit log and usage counters.

The concurrency tests here are the point of the file: both modules do
read-modify-write on files, and the failure mode is silent undercounting rather
than an exception.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from mango.core import audit, usage_tracker


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch):
    """Never touch the operator's real audit or usage directories."""
    monkeypatch.setattr(audit, "AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(usage_tracker, "USAGE_DIR", tmp_path / "usage")
    monkeypatch.setattr(usage_tracker, "_locks", {})
    return tmp_path


class TestAuditLog:
    def test_entry_is_recorded_and_readable(self):
        audit.log_tool_call("get_quote", {"symbol": "ZZZ"}, {"price": 1.0}, 12.5)

        out = audit.get_audit_log()

        assert out["total_this_month"] == 1
        assert out["entries"][0]["tool"] == "get_quote"
        assert out["entries"][0]["duration_ms"] == 12.5

    def test_entries_return_newest_first(self):
        for name in ("first", "second", "third"):
            audit.log_tool_call(name, {}, {"ok": True}, 1.0)

        assert [e["tool"] for e in audit.get_audit_log()["entries"]] == ["third", "second", "first"]

    def test_error_results_are_marked_not_ok(self):
        audit.log_tool_call("get_quote", {}, {"error": "boom"}, 1.0)

        assert audit.get_audit_log()["entries"][0]["ok"] is False

    def test_credentials_in_arguments_are_redacted(self, monkeypatch):
        monkeypatch.setenv("FRED_API_KEY", "supersecretvalue123")

        audit.log_tool_call("t", {"url": "https://x/?api_key=supersecretvalue123"}, {}, 1.0)

        assert "supersecretvalue123" not in json.dumps(audit.get_audit_log())

    def test_large_results_are_truncated_not_copied(self):
        audit.log_tool_call("t", {}, "x" * 50_000, 1.0)

        stored = audit.get_audit_log()["entries"][0]["result"]
        assert len(stored) <= audit.MAX_RESULT_CHARS

    def test_a_torn_line_does_not_discard_the_rest(self):
        audit.log_tool_call("good_one", {}, {}, 1.0)
        path = audit._log_path()
        path.write_text(path.read_text() + "{ this line is truncated\n", encoding="utf-8")

        assert audit.get_audit_log()["total_this_month"] == 1

    def test_unwritable_directory_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(audit.Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))

        audit.log_tool_call("t", {}, {}, 1.0)  # must not raise

    def test_summary_reports_failure_rate_and_timings(self):
        audit.log_tool_call("a", {}, {"ok": 1}, 10.0)
        audit.log_tool_call("a", {}, {"error": "x"}, 30.0)

        summary = audit.get_audit_summary()["tools"]["a"]

        assert summary["calls"] == 2
        assert summary["failures"] == 1
        assert summary["failure_rate_pct"] == 50.0
        assert summary["avg_ms"] == 20.0
        assert summary["max_ms"] == 30.0

    def test_summary_on_empty_log(self):
        assert audit.get_audit_summary()["total_calls"] == 0


class TestUsageCounters:
    @pytest.mark.asyncio
    async def test_monthly_increment_accumulates(self):
        await usage_tracker.increment_usage("brave", 3)
        await usage_tracker.increment_usage("brave", 2)

        assert await usage_tracker.get_monthly_usage("brave") == 5

    @pytest.mark.asyncio
    async def test_daily_counter_is_separate_from_monthly(self):
        await usage_tracker.increment_daily("brave", 4)

        assert await usage_tracker.get_daily_usage("brave") == 4
        assert await usage_tracker.get_monthly_usage("brave") == 0

    @pytest.mark.asyncio
    async def test_check_budget_does_not_consume(self):
        await usage_tracker.increment_usage("brave", 1)

        first = await usage_tracker.check_budget("brave", limit=10)
        second = await usage_tracker.check_budget("brave", limit=10)

        assert first["used"] == second["used"] == 1
        assert first["remaining"] == 9

    @pytest.mark.asyncio
    async def test_increment_and_check_refuses_past_the_limit(self):
        for _ in range(3):
            await usage_tracker.increment_and_check("brave", limit=3)

        blocked = await usage_tracker.increment_and_check("brave", limit=3)

        assert blocked["allowed"] is False
        assert blocked["used"] == 3, "a refused call must not consume budget"

    @pytest.mark.asyncio
    async def test_concurrent_increments_do_not_lose_counts(self):
        # The reason every mutation holds a lock: without one, both coroutines
        # read N and write N+1, and one call vanishes.
        await asyncio.gather(*[usage_tracker.increment_usage("brave") for _ in range(50)])

        assert await usage_tracker.get_monthly_usage("brave") == 50

    @pytest.mark.asyncio
    async def test_concurrent_budget_enforcement_never_exceeds_the_limit(self):
        limit = 10
        results = await asyncio.gather(
            *[usage_tracker.increment_and_check("brave", limit=limit) for _ in range(40)]
        )

        assert sum(1 for r in results if r["allowed"]) == limit
        assert await usage_tracker.get_monthly_usage("brave") == limit

    @pytest.mark.asyncio
    async def test_payload_sizes_accumulate(self):
        await usage_tracker.record_payload_size("brave", 100)
        await usage_tracker.record_payload_size("brave", 250)

        data = json.loads(usage_tracker._path_for("brave").read_text())
        assert sum(data["daily_bytes"].values()) == 350

    @pytest.mark.asyncio
    async def test_corrupt_counter_file_starts_fresh_without_raising(self):
        await usage_tracker.increment_usage("brave", 1)
        usage_tracker._path_for("brave").write_text("{not json", encoding="utf-8")

        assert await usage_tracker.get_monthly_usage("brave") == 0
        assert await usage_tracker.increment_usage("brave", 1) == 1

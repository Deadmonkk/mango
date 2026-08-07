"""Contract tests for the FR/EOD collector's source map.

WHY THIS EXISTS
---------------
`fr_collect.py` fetches every source the reports are built from. It is a single
point of failure for the whole pipeline and, until now, had no test at all.

On 2026-08-07 a namespace migration repointed six providers at a package where
they did not exist. That would have broken every report outright. The full
suite — 744 tests — still passed, because nothing imported the collector. The
break was found by reading the import block.

These tests close that gap. They are deliberately cheap and make no network
calls: merely importing the module resolves every provider, which is the exact
failure that escaped. The rest assert the shape the runner relies on.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


@pytest.fixture(scope="module")
def collector():
    """Importing this is itself the main assertion — it resolves every provider."""
    import fr_collect

    return fr_collect


class TestSourceMapsImport:
    def test_module_imports_resolving_every_provider(self, collector) -> None:
        # A provider pointed at a non-existent module raises here, which is
        # precisely the regression this file exists to catch.
        assert collector is not None

    def test_both_modes_are_populated(self, collector) -> None:
        assert collector.TOOL_MAP_FR, "FR source map is empty"
        assert collector.TOOL_MAP_EOD, "EOD source map is empty"

    @pytest.mark.parametrize("mode", ["fr", "eod"])
    def test_source_count_has_not_silently_collapsed(self, collector, mode: str) -> None:
        # A canary, not a spec: sources get added over time, so this only guards
        # against a large accidental loss (a botched merge, a truncated dict).
        floor = {"fr": 50, "eod": 12}[mode]
        table = collector.TOOL_MAP_FR if mode == "fr" else collector.TOOL_MAP_EOD
        assert len(table) >= floor, f"{mode} dropped to {len(table)} sources (floor {floor})"


class TestSourceMapShape:
    @pytest.mark.parametrize("mode", ["fr", "eod"])
    def test_every_entry_is_a_callable_with_args_and_kwargs(self, collector, mode: str) -> None:
        table = collector.TOOL_MAP_FR if mode == "fr" else collector.TOOL_MAP_EOD
        for label, entry in table.items():
            assert isinstance(entry, tuple) and len(entry) == 3, f"{label}: expected (fn, args, kwargs)"
            fn, args, kwargs = entry
            assert callable(fn), f"{label}: first element is not callable"
            assert isinstance(args, tuple), f"{label}: args must be a tuple"
            assert isinstance(kwargs, dict), f"{label}: kwargs must be a dict"

    @pytest.mark.parametrize("mode", ["fr", "eod"])
    def test_wrapped_providers_are_not_left_as_coroutines(self, collector, mode: str) -> None:
        # Providers are async and get wrapped so the synchronous runner can call
        # them. An unwrapped coroutine function would return a coroutine object
        # that is never awaited, and the source would silently render as failed.
        table = collector.TOOL_MAP_FR if mode == "fr" else collector.TOOL_MAP_EOD
        offenders = [label for label, (fn, _, _) in table.items() if inspect.iscoroutinefunction(fn)]
        assert not offenders, f"unwrapped async providers: {offenders}"

    def test_labels_are_unique_across_a_mode(self, collector) -> None:
        # Dict keys are unique by construction; this guards the *source* file
        # against a duplicated label silently overwriting an earlier source.
        text = (Path(__file__).resolve().parent.parent / "scripts" / "fr_collect.py").read_text()
        for mode, marker in (("FR", "TOOL_MAP_FR"), ("EOD", "TOOL_MAP_EOD")):
            start = text.index(f"{marker}: dict = {{") if f"{marker}: dict = {{" in text else text.index(marker)
            block = text[start : text.index("\n}", start)]
            labels = [line.split('"')[1] for line in block.splitlines() if line.strip().startswith('"')]
            duplicates = {label for label in labels if labels.count(label) > 1}
            assert not duplicates, f"{mode} has duplicate source labels: {duplicates}"


class TestRunnerHelpers:
    def test_safe_returns_sentinel_instead_of_raising(self, collector) -> None:
        def boom():
            raise RuntimeError("provider exploded")

        result = collector.safe(boom)

        # One failing source must never abort a whole run.
        assert result["_value"] == collector.FAIL
        assert "provider exploded" in result["_error"]

    def test_dig_returns_sentinel_for_a_missing_path(self, collector) -> None:
        assert collector.dig({"a": {}}, "a.b.c") == collector.FAIL

    def test_dig_reads_a_present_path(self, collector) -> None:
        assert collector.dig({"a": {"b": {"c": 7}}}, "a.b.c") == 7

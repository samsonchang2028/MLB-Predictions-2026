"""Unit tests for the validation result contract."""

from __future__ import annotations

from validation.results import (
    FAIL,
    PASS,
    WARN,
    fail,
    finding,
    ok,
    summarize,
    warn,
)


def test_blocking_only_for_p0_p1_failures() -> None:
    assert fail("c", "P0", "x").blocking is True
    assert fail("c", "P1", "x").blocking is True
    assert fail("c", "P2", "x").blocking is False
    assert ok("c", "P0").blocking is False
    assert warn("c", "x").blocking is False


def test_finding_passes_when_no_bad_records() -> None:
    result = finding("c", "P0", [], "dupes")
    assert result.status == PASS
    assert result.failing == ()


def test_finding_fails_with_sorted_capped_sample() -> None:
    result = finding("c", "P1", list(range(30, 0, -1)), "dupes")
    assert result.status == FAIL
    assert result.message == "30 dupes"
    # Deterministic: sorted ascending (by repr) and capped at 20.
    assert len(result.failing) == 20
    assert result.failing[0] == 1


def test_summarize_flags_merge_blocking_subset() -> None:
    results = [
        ok("a", "P0"),
        warn("b", "advisory"),
        fail("c", "P2", "soft fail"),
        fail("d", "P1", "hard fail"),
    ]
    summary = summarize(results)
    assert summary["status"] == FAIL  # any FAIL => overall FAIL
    assert summary["failed"] == 2
    assert summary["warnings"] == 1
    assert summary["merge_blocking"] == ["d"]  # only the P0/P1 failure blocks


def test_summarize_clean_run_passes() -> None:
    summary = summarize([ok("a", "P0"), warn("b", "note")])
    assert summary["status"] == PASS
    assert summary["merge_blocking"] == []
    assert summary["warnings"] == 1

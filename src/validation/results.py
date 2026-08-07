"""Structured, deterministic validation results.

This is the shared contract consumed by the DATA-007 certification gate and
reused by DATA-009. Keep the shape small and obvious.

A ``CheckResult`` records one validation check outcome:

- ``check``    stable check id (e.g. ``"results.home_win_derivation"``),
- ``status``   ``PASS`` | ``FAIL`` | ``WARN``,
- ``severity`` ``P0`` | ``P1`` | ``P2`` — the merge-blocking weight *if* the
  check fails. ``P0``/``P1`` failures are merge-blocking (ADR-004); ``P2`` and
  ``WARN`` are advisory.
- ``message``  human-readable summary,
- ``failing``  a deterministic (sorted, capped) tuple of record identifiers so
  a consumer can locate the failing rows.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"

# Merge-blocking severities (ADR-004: P0/P1 data findings and leakage failures).
BLOCKING_SEVERITIES = ("P0", "P1")

# Deterministic cap on how many failing records are attached to a result.
FAILING_SAMPLE_LIMIT = 20


@dataclass(frozen=True)
class CheckResult:
    check: str
    status: str
    severity: str
    message: str
    failing: tuple[object, ...] = ()

    @property
    def blocking(self) -> bool:
        return self.status == FAIL and self.severity in BLOCKING_SEVERITIES


def ok(check: str, severity: str = "P2", message: str = "ok") -> CheckResult:
    return CheckResult(check, PASS, severity, message)


def fail(
    check: str, severity: str, message: str, failing: Iterable[object] = ()
) -> CheckResult:
    return CheckResult(check, FAIL, severity, message, _sample(failing))


def warn(
    check: str, message: str, failing: Iterable[object] = (), severity: str = "P2"
) -> CheckResult:
    return CheckResult(check, WARN, severity, message, _sample(failing))


def finding(
    check: str, severity: str, bad: Iterable[object], noun: str
) -> CheckResult:
    """PASS when ``bad`` is empty, else a FAIL naming the offending records."""
    bad = list(bad)
    if bad:
        return fail(check, severity, f"{len(bad)} {noun}", bad)
    return ok(check, severity)


def _sample(failing: Iterable[object]) -> tuple[object, ...]:
    # Sort for determinism; cap so results stay small and readable.
    return tuple(sorted(failing, key=repr))[:FAILING_SAMPLE_LIMIT]


def summarize(results: Iterable[CheckResult]) -> dict[str, object]:
    """Collapse results into a certification-consumable verdict.

    ``status`` is ``FAIL`` when any check failed. ``merge_blocking`` lists the
    P0/P1 (and leakage) failures that block the certification gate per ADR-004.
    DATA-007 should treat a non-empty ``merge_blocking`` as a hard fail.
    """
    results = list(results)
    failed = [r for r in results if r.status == FAIL]
    blocking = [r for r in results if r.blocking]
    return {
        "status": FAIL if failed else PASS,
        "checks": len(results),
        "passed": sum(1 for r in results if r.status == PASS),
        "failed": len(failed),
        "warnings": sum(1 for r in results if r.status == WARN),
        "merge_blocking": [r.check for r in blocking],
    }

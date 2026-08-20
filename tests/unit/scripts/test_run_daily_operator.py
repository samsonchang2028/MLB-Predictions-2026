from __future__ import annotations

import json

from scripts import run_daily_operator as operator


def _args(*extra: str):
    return operator.build_parser().parse_args(
        ["--date", "2026-08-20", "--run-id", "test-run", *extra]
    )


def _operator_records(output: str) -> list[dict]:
    return [
        json.loads(line.removeprefix("[operator] "))
        for line in output.splitlines()
        if line.startswith("[operator] ")
    ]


def test_predict_stage_refreshes_schedule_before_prediction(monkeypatch, capsys) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        operator,
        "_refresh_and_normalize",
        lambda database, run_date: calls.append(("schedule", database, str(run_date))) or 0,
    )
    monkeypatch.setattr(
        operator.daily_predictions,
        "main",
        lambda argv: calls.append(("prediction", argv)) or 0,
    )

    assert operator.run(_args("--stage", "predict")) == 0
    assert calls[0] == ("schedule", "data/mlb.duckdb", "2026-08-20")
    assert calls[1][0] == "prediction"
    assert calls[1][1][:2] == ["--date", "2026-08-20"]

    records = _operator_records(capsys.readouterr().out)
    assert [(row["stage"], row["status"]) for row in records] == [
        ("operator", "started"),
        ("schedule_refresh", "started"),
        ("schedule_refresh", "completed"),
        ("prediction", "started"),
        ("prediction", "completed"),
        ("operator", "completed"),
    ]
    assert all(row["run_id"] == "test-run" for row in records)
    assert all(row["run_date"] == "2026-08-20" for row in records)
    assert all(row["timestamp"].endswith("+00:00") for row in records)


def test_all_stage_runs_prediction_then_enrichment_without_second_schedule_refresh(
    monkeypatch,
) -> None:
    calls: list[tuple[str, list[str] | None]] = []
    monkeypatch.setattr(
        operator,
        "_refresh_and_normalize",
        lambda *_args: calls.append(("schedule", None)) or 0,
    )
    monkeypatch.setattr(
        operator.daily_predictions,
        "main",
        lambda argv: calls.append(("prediction", argv)) or 0,
    )
    monkeypatch.setattr(
        operator.enrich_prediction_results,
        "main",
        lambda argv: calls.append(("enrichment", argv)) or 0,
    )

    assert operator.run(_args("--stage", "all")) == 0
    assert [name for name, _argv in calls] == ["schedule", "prediction", "enrichment"]
    assert "--skip-schedule-refresh" in calls[-1][1]


def test_failure_is_nonzero_and_stops_downstream_stages(monkeypatch, capsys) -> None:
    enrichment_called = False
    monkeypatch.setattr(operator, "_refresh_and_normalize", lambda *_args: 0)
    monkeypatch.setattr(operator.daily_predictions, "main", lambda _argv: 7)

    def enrichment(_argv):
        nonlocal enrichment_called
        enrichment_called = True
        return 0

    monkeypatch.setattr(operator.enrich_prediction_results, "main", enrichment)

    assert operator.run(_args("--stage", "all")) == 7
    assert enrichment_called is False
    records = _operator_records(capsys.readouterr().out)
    failed = [row for row in records if row["status"] == "failed"]
    assert failed[-1]["stage"] == "prediction"
    assert failed[-1]["exit_code"] == 7


def test_exception_identifies_failing_stage(monkeypatch, capsys) -> None:
    def fail(*_args):
        raise RuntimeError("schedule unavailable")

    monkeypatch.setattr(operator, "_refresh_and_normalize", fail)
    assert operator.run(_args("--stage", "predict")) == 1
    [failure] = [
        row
        for row in _operator_records(capsys.readouterr().out)
        if row["status"] == "failed"
    ]
    assert failure["stage"] == "schedule_refresh"
    assert failure["error"] == "RuntimeError: schedule unavailable"


def test_explicit_timestamps_make_manual_rerun_arguments_stable(monkeypatch) -> None:
    prediction_argv: list[list[str]] = []
    enrichment_argv: list[list[str]] = []
    monkeypatch.setattr(operator, "_refresh_and_normalize", lambda *_args: 0)
    monkeypatch.setattr(
        operator.daily_predictions,
        "main",
        lambda argv: prediction_argv.append(argv) or 0,
    )
    monkeypatch.setattr(
        operator.enrich_prediction_results,
        "main",
        lambda argv: enrichment_argv.append(argv) or 0,
    )
    args = _args(
        "--stage",
        "all",
        "--prediction-timestamp",
        "2026-08-20T16:00:00+00:00",
        "--enrichment-timestamp",
        "2026-08-21T07:00:00+00:00",
    )

    assert operator.run(args) == 0
    assert operator.run(args) == 0
    assert prediction_argv[0] == prediction_argv[1]
    assert enrichment_argv[0] == enrichment_argv[1]
    assert "2026-08-20T16:00:00+00:00" in prediction_argv[0]
    assert "2026-08-21T07:00:00+00:00" in enrichment_argv[0]


def test_prediction_stage_never_adds_a_first_pitch_bypass() -> None:
    argv = operator._prediction_argv(_args("--stage", "predict"), operator.date(2026, 8, 20))
    assert not any("first-pitch" in value for value in argv)

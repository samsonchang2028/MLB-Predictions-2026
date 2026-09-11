from pathlib import Path


SYSTEMD = Path("deploy/systemd")


def test_services_use_wrapper_environment_file_journal_and_shared_lock() -> None:
    daily = (SYSTEMD / "mlb-predictions-daily.service").read_text(encoding="utf-8")
    enrich = (SYSTEMD / "mlb-predictions-enrich.service").read_text(encoding="utf-8")

    for unit in (daily, enrich):
        assert "EnvironmentFile=/etc/mlb-predictions/mlb-predictions.env" in unit
        assert "scripts/run_daily_operator.py" in unit
        assert "/usr/bin/flock --wait 1800" in unit
        assert "/opt/mlb-predictions/data/operator.lock" in unit
        assert "StandardOutput=journal" in unit
        assert "StandardError=journal" in unit
        assert "THE_ODDS_API_KEY=" not in unit
    assert "--stage predict" in daily
    assert "--stage enrich" in enrich


def test_timers_are_persistent_and_explicitly_pacific() -> None:
    daily = (SYSTEMD / "mlb-predictions-daily.timer").read_text(encoding="utf-8")
    enrich = (SYSTEMD / "mlb-predictions-enrich.timer").read_text(encoding="utf-8")

    assert daily.count("OnCalendar=") == 5
    assert enrich.count("OnCalendar=") == 3
    for timer in (daily, enrich):
        assert "America/Los_Angeles" in timer
        assert "Persistent=true" in timer


def test_closing_odds_services_use_env_file_journal_and_no_operator_lock() -> None:
    live = (SYSTEMD / "mlb-predictions-closing-odds.service").read_text(encoding="utf-8")
    backfill = (SYSTEMD / "mlb-predictions-closing-odds-backfill.service").read_text(
        encoding="utf-8"
    )

    for unit in (live, backfill):
        assert "User=mlbpred" in unit
        assert "Group=mlbpred" in unit
        assert "WorkingDirectory=/opt/mlb-predictions" in unit
        assert "EnvironmentFile=/etc/mlb-predictions/mlb-predictions.env" in unit
        assert "scripts/closing_odds_capture.py" in unit
        assert "StandardOutput=journal" in unit
        assert "StandardError=journal" in unit
        assert "THE_ODDS_API_KEY=" not in unit
        assert "/usr/bin/flock" not in unit
        assert "operator.lock" not in unit

    assert "--backfill-from-odds-books" in backfill
    assert "--anchor earliest" in backfill
    assert "--backfill-from-odds-books" not in live
    assert "--date" not in live


def test_closing_odds_timer_runs_every_ten_minutes() -> None:
    timer = (SYSTEMD / "mlb-predictions-closing-odds.timer").read_text(encoding="utf-8")

    assert "OnCalendar=*-*-* *:00/10:00 America/Los_Angeles" in timer
    assert "Persistent=true" in timer
    assert "Unit=mlb-predictions-closing-odds.service" in timer

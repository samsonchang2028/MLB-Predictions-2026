from pathlib import Path


SYSTEMD = Path("deploy/systemd")


def test_services_use_wrapper_environment_file_journal_and_shared_lock() -> None:
    daily = (SYSTEMD / "mlb-predictions-daily.service").read_text(encoding="utf-8")
    enrich = (SYSTEMD / "mlb-predictions-enrich.service").read_text(encoding="utf-8")

    for unit in (daily, enrich):
        assert "EnvironmentFile=/etc/mlb-predictions/mlb-predictions.env" in unit
        assert "scripts/run_daily_operator.py" in unit
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

"""Tests for the silent-source report CLI (``ma-signal-source-health``)."""

from datetime import datetime, timedelta

from ma_signal_monitor import source_health_cli
from ma_signal_monitor.config import load_config
from ma_signal_monitor.models import SourceFetchOutcome
from ma_signal_monitor.storage import StateStore


def _log_old_failure(root, source_name="Test Feed", days_ago=30):
    config = load_config(root)
    store = StateStore(root / config.db_path)
    try:
        run_id = store.start_run()
        conn = store._get_conn()
        old = (datetime.utcnow() - timedelta(days=days_ago)).isoformat()
        conn.execute(
            """INSERT INTO source_fetch_log
                   (run_id, source_name, fetched_at, status, n_items, n_persisted, error)
               VALUES (?, ?, ?, 'error', 0, 0, '403 Forbidden')""",
            (run_id, source_name, old),
        )
        conn.commit()
    finally:
        store.close()


def _log_recent_success(root, source_name="Test Feed"):
    config = load_config(root)
    store = StateStore(root / config.db_path)
    try:
        run_id = store.start_run()
        store.log_source_fetches(
            run_id,
            outcomes=[SourceFetchOutcome(source_name, "ok", n_items=2)],
            persisted_counts={source_name: 2},
        )
    finally:
        store.close()


def test_exits_two_with_no_data(project_root_with_config, capsys):
    code = source_health_cli.main(["--root", str(project_root_with_config)])
    assert code == 2
    assert "unavailable" in capsys.readouterr().err


def test_exits_zero_when_source_recently_healthy(project_root_with_config, capsys):
    _log_recent_success(project_root_with_config)
    code = source_health_cli.main(["--root", str(project_root_with_config)])
    assert code == 0


def test_exits_one_and_reports_silent_source(project_root_with_config, capsys):
    _log_old_failure(project_root_with_config)
    code = source_health_cli.main(["--root", str(project_root_with_config)])
    out = capsys.readouterr().out
    assert code == 1
    assert "1 silent source" in out
    assert "Test Feed" in out
    assert "403 Forbidden" in out


def test_removed_source_observations_are_unavailable(project_root_with_config, capsys):
    _log_recent_success(project_root_with_config, source_name="Removed Feed")
    assert source_health_cli.main(["--root", str(project_root_with_config)]) == 2
    assert "no fetch observations for enabled sources" in capsys.readouterr().err


def test_disabled_source_observations_are_unavailable(
    project_root_with_config, monkeypatch, capsys
):
    _log_recent_success(project_root_with_config)
    config = load_config(project_root_with_config)
    config.sources[0].enabled = False
    monkeypatch.setattr(source_health_cli, "load_config", lambda root: config)
    assert source_health_cli.main(["--root", str(project_root_with_config)]) == 2
    assert "no enabled sources" in capsys.readouterr().err


def test_partial_coverage_is_explicit_not_silent(
    project_root_with_config, monkeypatch, capsys
):
    from dataclasses import replace

    _log_recent_success(project_root_with_config)
    config = load_config(project_root_with_config)
    config.sources.append(replace(config.sources[0], name="New Feed"))
    monkeypatch.setattr(source_health_cli, "load_config", lambda root: config)
    assert source_health_cli.main(["--root", str(project_root_with_config)]) == 0
    out = capsys.readouterr().out
    assert "1/2 enabled sources observed" in out
    assert "1 unobserved" in out
    assert "No silent sources among observed enabled sources" in out


def test_disabled_rows_do_not_cover_enabled_source(
    project_root_with_config, monkeypatch
):
    from dataclasses import replace

    _log_recent_success(project_root_with_config)
    config = load_config(project_root_with_config)
    config.sources.append(replace(config.sources[0], name="Unobserved Feed"))
    config.sources[0].enabled = False
    monkeypatch.setattr(source_health_cli, "load_config", lambda root: config)
    assert source_health_cli.main(["--root", str(project_root_with_config)]) == 2

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


def test_exits_zero_with_no_data(project_root_with_config, capsys):
    code = source_health_cli.main(["--root", str(project_root_with_config)])
    assert code == 0
    assert "No silent sources" in capsys.readouterr().out


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

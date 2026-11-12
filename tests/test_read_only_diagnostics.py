"""Diagnostic reads must not initialize, migrate, or repair their evidence."""

import sqlite3

import pytest

from ma_signal_monitor import source_health_cli
from ma_signal_monitor.storage import StateStore


def test_read_only_missing_archive_does_not_create_parent(tmp_path):
    path = tmp_path / "absent" / "state.db"
    with pytest.raises(sqlite3.OperationalError):
        StateStore(path, read_only=True)
    assert not path.parent.exists()


def test_read_only_store_cannot_write_or_create_fts(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE stories (item_id TEXT, title TEXT, summary TEXT)")
        conn.execute("INSERT INTO stories VALUES ('one', 'Original', 'Evidence')")
    before = path.read_bytes()
    store = StateStore(path, read_only=True)
    try:
        assert not store.fts_enabled
        assert (
            store._get_conn().execute("SELECT title FROM stories").fetchone()[0]
            == "Original"
        )
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            store._get_conn().execute("DELETE FROM stories")
    finally:
        store.close()
    assert path.read_bytes() == before


def test_missing_archive_is_unknown_not_healthy(project_root_with_config, capsys):
    root = project_root_with_config
    assert source_health_cli.main(["--root", str(root)]) == 2
    output = capsys.readouterr()
    assert "unavailable" in output.err
    assert "No silent sources" not in output.out
    assert not (root / "data").exists()


def test_legacy_archive_is_not_migrated(project_root_with_config, capsys):
    path = project_root_with_config / "data" / "state.db"
    path.parent.mkdir()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE evidence (value TEXT)")
        conn.execute("INSERT INTO evidence VALUES ('preserve')")
    before = path.read_bytes()
    assert source_health_cli.main(["--root", str(project_root_with_config)]) == 2
    assert "unavailable" in capsys.readouterr().err
    assert path.read_bytes() == before


def test_corrupt_archive_is_unavailable_and_unchanged(project_root_with_config, capsys):
    path = project_root_with_config / "data" / "state.db"
    path.parent.mkdir()
    path.write_bytes(b"not a SQLite database")
    before = path.read_bytes()
    assert source_health_cli.main(["--root", str(project_root_with_config)]) == 2
    assert "unavailable" in capsys.readouterr().err
    assert path.read_bytes() == before


def test_existing_empty_archive_is_unknown(project_root_with_config, capsys):
    path = project_root_with_config / "data" / "state.db"
    StateStore(path).close()
    before = path.read_bytes()
    assert source_health_cli.main(["--root", str(project_root_with_config)]) == 2
    assert "no fetch observations" in capsys.readouterr().err
    assert path.read_bytes() == before


def test_read_only_existing_fts_and_special_path(tmp_path):
    path = tmp_path / "archive?#.db"
    StateStore(path).close()
    before = path.read_bytes()
    store = StateStore(path, read_only=True)
    try:
        assert store.fts_enabled
        assert (
            store._get_conn().execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 0
        )
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            store._get_conn().execute("CREATE TABLE forbidden (value TEXT)")
    finally:
        store.close()
    assert path.read_bytes() == before

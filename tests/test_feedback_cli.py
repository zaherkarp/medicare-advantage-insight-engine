"""Tests for the reader-feedback CLI (``ma-signal-feedback``): the
scoring-detail extension to ``summary``, and the retired ``alert`` command."""

import sys

import pytest

from ma_signal_monitor import feedback_cli
from ma_signal_monitor.config import load_config
from ma_signal_monitor.models import NormalizedItem, ScoredItem
from ma_signal_monitor.storage import StateStore


def _seed_story(root, item_id="s1", *, score=0.42):
    """Seed one archived story for CLI tests."""
    config = load_config(root)
    store = StateStore(root / config.db_path)
    try:
        item = NormalizedItem(
            item_id=item_id,
            source_name="Test Feed",
            source_type="rss",
            source_priority=4,
            source_tags=["test"],
            title="CMS Star Ratings update",
            link=f"https://example.com/{item_id}",
            published_date=None,
            summary="A summary",
        )
        scored = ScoredItem(item=item, relevance_score=score)
        store.upsert_story(
            scored, primary_category="policy_regulatory", threshold_at_score=0.3
        )
    finally:
        store.close()


class TestSummaryCommand:
    """``ma-signal-feedback summary`` reports feedback plus scoring detail."""

    def test_summary_includes_scoring_breakdown(
        self, project_root_with_config, monkeypatch, capsys
    ):
        _seed_story(project_root_with_config, score=0.42)
        monkeypatch.chdir(project_root_with_config)
        monkeypatch.setattr(sys, "argv", ["ma-signal-feedback", "summary", "s1"])
        feedback_cli.main()
        out = capsys.readouterr().out
        assert "no feedback yet" in out
        assert "combined score: 0.42" in out
        assert "threshold at score time: 0.3" in out
        assert "webhook" not in out


class TestRetiredAlertCommand:
    """Alert-outcome feedback was retired in 2026-10; ``alert`` is no longer a
    command, so it falls through to the usage text and exits non-zero."""

    def test_alert_command_is_gone(self, project_root_with_config, monkeypatch, capsys):
        _seed_story(project_root_with_config)
        monkeypatch.chdir(project_root_with_config)
        monkeypatch.setattr(
            sys, "argv", ["ma-signal-feedback", "alert", "s1", "correct"]
        )
        with pytest.raises(SystemExit) as exc:
            feedback_cli.main()
        assert exc.value.code == 2
        out = capsys.readouterr().out
        assert "Usage:" in out
        assert "alert <item_id>" not in out

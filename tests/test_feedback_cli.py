"""Tests for the reader-feedback CLI (``ma-signal-feedback``), focused on the
``alert`` subcommand and the scoring-detail extension to ``summary``."""

import sys

import pytest

from ma_signal_monitor import feedback_cli
from ma_signal_monitor.config import load_config
from ma_signal_monitor.models import DeliveryResult, NormalizedItem, ScoredItem
from ma_signal_monitor.storage import StateStore


def _seed_story(root, item_id="s1", *, delivered=False, score=0.42):
    """Seed one archived story (and optionally a successful delivery) for CLI tests."""
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
        if delivered:
            store.log_delivery(
                DeliveryResult(
                    alert_title="T", success=True, status_code=200, item_id=item_id
                )
            )
    finally:
        store.close()


class TestAlertCommand:
    """``ma-signal-feedback alert <item_id> <correct|false_positive|missed>``."""

    def test_correct_requires_delivery(
        self, project_root_with_config, monkeypatch, capsys
    ):
        _seed_story(project_root_with_config, delivered=False)
        monkeypatch.chdir(project_root_with_config)
        monkeypatch.setattr(
            sys, "argv", ["ma-signal-feedback", "alert", "s1", "correct"]
        )
        with pytest.raises(SystemExit) as exc:
            feedback_cli.main()
        assert exc.value.code == 2
        assert "never successfully posted" in capsys.readouterr().out

    def test_correct_records_when_delivered(
        self, project_root_with_config, monkeypatch, capsys
    ):
        _seed_story(project_root_with_config, delivered=True)
        monkeypatch.chdir(project_root_with_config)
        monkeypatch.setattr(
            sys, "argv", ["ma-signal-feedback", "alert", "s1", "correct"]
        )
        feedback_cli.main()
        assert "Recorded alert outcome 'correct'" in capsys.readouterr().out

        config = load_config(project_root_with_config)
        store = StateStore(project_root_with_config / config.db_path)
        try:
            info = store.get_alert_feedback("s1")
        finally:
            store.close()
        assert [v["verdict"] for v in info["alert_verdicts"]] == ["alert_correct"]

    def test_missed_requires_no_delivery(
        self, project_root_with_config, monkeypatch, capsys
    ):
        _seed_story(project_root_with_config, delivered=True)
        monkeypatch.chdir(project_root_with_config)
        monkeypatch.setattr(
            sys, "argv", ["ma-signal-feedback", "alert", "s1", "missed"]
        )
        with pytest.raises(SystemExit) as exc:
            feedback_cli.main()
        assert exc.value.code == 2
        assert "WAS posted" in capsys.readouterr().out

    def test_missed_records_when_not_delivered(
        self, project_root_with_config, monkeypatch, capsys
    ):
        _seed_story(project_root_with_config, delivered=False)
        monkeypatch.chdir(project_root_with_config)
        monkeypatch.setattr(
            sys, "argv", ["ma-signal-feedback", "alert", "s1", "missed"]
        )
        feedback_cli.main()
        assert "Recorded alert outcome 'missed'" in capsys.readouterr().out

    def test_unknown_story(self, project_root_with_config, monkeypatch, capsys):
        monkeypatch.chdir(project_root_with_config)
        monkeypatch.setattr(
            sys, "argv", ["ma-signal-feedback", "alert", "nope", "correct"]
        )
        with pytest.raises(SystemExit) as exc:
            feedback_cli.main()
        assert exc.value.code == 1
        assert "No story with id" in capsys.readouterr().out

    def test_unknown_verdict_word(self, project_root_with_config, monkeypatch, capsys):
        _seed_story(project_root_with_config)
        monkeypatch.chdir(project_root_with_config)
        monkeypatch.setattr(sys, "argv", ["ma-signal-feedback", "alert", "s1", "bogus"])
        with pytest.raises(SystemExit) as exc:
            feedback_cli.main()
        assert exc.value.code == 2
        assert "Unknown alert verdict" in capsys.readouterr().out


class TestSummaryCommand:
    """``ma-signal-feedback summary`` now also reports scoring/delivery detail."""

    def test_summary_includes_scoring_breakdown_and_delivery(
        self, project_root_with_config, monkeypatch, capsys
    ):
        _seed_story(project_root_with_config, delivered=True, score=0.42)
        monkeypatch.chdir(project_root_with_config)
        monkeypatch.setattr(
            sys, "argv", ["ma-signal-feedback", "alert", "s1", "correct"]
        )
        feedback_cli.main()
        capsys.readouterr()  # discard the `alert` command's output

        monkeypatch.setattr(sys, "argv", ["ma-signal-feedback", "summary", "s1"])
        feedback_cli.main()
        out = capsys.readouterr().out
        assert "combined score: 0.42" in out
        assert "threshold at score time: 0.3" in out
        assert "posted to webhook: yes" in out
        assert "alert_correct" in out

    def test_summary_without_alert_activity_omits_scoring_section(
        self, project_root_with_config, monkeypatch, capsys
    ):
        """A story that's never had its alert outcome scored yet still summarizes."""
        _seed_story(project_root_with_config, delivered=False)
        monkeypatch.chdir(project_root_with_config)
        monkeypatch.setattr(sys, "argv", ["ma-signal-feedback", "summary", "s1"])
        feedback_cli.main()
        out = capsys.readouterr().out
        assert "no feedback yet" in out
        assert "posted to webhook: no" in out

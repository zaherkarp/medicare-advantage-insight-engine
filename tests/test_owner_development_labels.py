"""Validate approved development data, not current selection correctness.

These tests do not promote the examples to an unseen holdout or reinterpret
reference relevance as draft eligibility. Production gaps are not relabelled.
"""

from pathlib import Path
from datetime import datetime

import yaml

from ma_signal_monitor.config import load_config
from ma_signal_monitor.models import NormalizedItem
from ma_signal_monitor.scoring import score_item
from ma_signal_monitor.drafting import draft_alerts
from ma_signal_monitor.main import _persist_stories
from ma_signal_monitor.storage import StateStore
from ma_signal_monitor.web.routes import _story_view

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/owner_development_2026_11.yaml"


def test_approved_labels_are_three_separate_axes():
    data = yaml.safe_load(FIXTURE.read_text())
    assert data["designation"] == "development"
    assert data["label_provenance"] == "owner_approved"
    rows = {row["id"]: row for row in data["cases"]}
    assert set(rows) == {"ca7b8bc14feb207f", "86fe45ef857045ba", "7b068358ef756143"}
    assert rows["ca7b8bc14feb207f"]["expected"]["public_draft"] == "review_only"
    assert rows["86fe45ef857045ba"]["expected"]["entity_exclusions"] == ["SCAN"]
    faq = rows["7b068358ef756143"]["expected"]
    assert faq["relevance"] == "relevant_reference"
    assert faq["briefing"] == "ineligible_without_substantive_change"
    for row in rows.values():
        assert row["source_url"].startswith("https://")
        assert row["title"] and row["summary"] and row["qualifications"]


def test_development_inputs_load_through_current_persistence(tmp_path, monkeypatch):
    """Execution smoke test only: label satisfaction is deliberately not asserted."""
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "replay@example.invalid")
    config = load_config(ROOT)
    data = yaml.safe_load(FIXTURE.read_text())
    items = [
        NormalizedItem(
            item_id=row["id"],
            source_name=row["source_name"],
            source_type="fixture",
            source_priority=row["source_priority"],
            source_tags=row["source_tags"],
            title=row["title"],
            summary=row["summary"],
            link=row["source_url"],
            published_date=datetime(2026, 11, 2),
        )
        for row in data["cases"]
    ]
    scored = [score_item(item, config) for item in items]
    store = StateStore(
        tmp_path / "state.db", eligibility_gate=config.ma_eligibility_gate
    )
    try:
        _persist_stories(scored, draft_alerts(scored, config), config, store)
        for row in data["cases"]:
            saved = store.get_story(row["id"])
            assert saved is not None
            assert "public_draft" in _story_view(saved)
    finally:
        store.close()

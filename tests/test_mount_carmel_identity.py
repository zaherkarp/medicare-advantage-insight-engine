"""Mount Carmel/MediGold identity regression from a public CourtListener feed."""

from datetime import datetime
from pathlib import Path

import pytest

from ma_signal_monitor.config import load_config
from ma_signal_monitor.models import NormalizedItem
from ma_signal_monitor.scoring import score_item


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def real_config(monkeypatch):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "replay@example.invalid")
    monkeypatch.setenv("MA_ELIGIBILITY_GATE", "false")
    return load_config(ROOT)


def _item(item_id: str, title: str, summary: str) -> NormalizedItem:
    return NormalizedItem(
        item_id=item_id,
        source_name="CourtListener - MA Stars Filings",
        source_type="rss",
        source_priority=4,
        source_tags=["litigation", "enforcement", "federal"],
        title=title,
        summary=summary,
        link=f"https://www.courtlistener.com/docket/{item_id}/1/",
        published_date=datetime(2026, 10, 30),
        fetched_at=datetime(2026, 11, 7),
    )


def test_mount_carmel_health_plan_clears_archive_floor(real_config):
    """The exact legal payer identity should survive the broad-source gate."""
    scored = score_item(
        _item(
            "75048725",
            "Mount Carmel Health Plan, Inc. v. Health and Human Services (HHS)",
            (
                "Columbus, OH 43213, Plaintiff, v. Civil Action No. "
                "2:26-cv-1580 Department of Health and Human Services and "
                "Centers for Medicare & Medicaid Services."
            ),
        ),
        real_config,
    )

    assert scored.matched_entities == ["Mount Carmel Health Plan"]
    assert scored.relevance_score == pytest.approx(0.28)
    assert scored.relevance_score >= real_config.archive_min_score
    assert not any(reason.factor == "ma_context_gate" for reason in scored.reasons)


def test_unrelated_mount_carmel_litigation_remains_below_floor(real_config):
    """A shared place/system name is not the exact health-plan identity."""
    scored = score_item(
        _item(
            "negative-control",
            "Doe v. Mount Carmel Health System",
            "An employment dispute concerning a hospital position.",
        ),
        real_config,
    )

    assert scored.matched_entities == []
    assert scored.relevance_score == pytest.approx(0.08)
    assert scored.relevance_score < real_config.archive_min_score
    assert any(reason.factor == "ma_context_gate" for reason in scored.reasons)

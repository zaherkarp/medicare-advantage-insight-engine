"""Tests for the FastAPI web frontend."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from ma_signal_monitor.models import NormalizedItem, ScoredItem
from ma_signal_monitor.web.app import create_app


def _seed_story(
    store,
    item_id,
    title,
    *,
    category,
    score=0.6,
    states=None,
    entities=None,
    draft=None,
    published=datetime(2024, 1, 1, 12, 0),
):
    item = NormalizedItem(
        item_id=item_id,
        source_name="Test Feed",
        source_type="rss",
        source_priority=4,
        source_tags=["test"],
        title=title,
        link=f"https://example.com/{item_id}",
        published_date=published,
        summary=f"Summary for {title}.",
    )
    scored = ScoredItem(
        item=item,
        relevance_score=score,
        matched_categories=[category],
        matched_entities=entities or [],
    )
    store.upsert_story(
        scored, primary_category=category, public_draft=draft, states=states or []
    )


@pytest.fixture
def client(sample_config, temp_db):
    """A TestClient over an app backed by a seeded in-memory-ish DB."""
    _seed_story(
        temp_db,
        "story-a",
        "UnitedHealthcare expands enrollment in California",
        category="membership_movement",
        states=["CA"],
        entities=["UnitedHealthcare"],
        draft={
            "opening_hook": "A notable move",
            "analytic_angles": ["Angle one", "Angle two"],
            "draft_paragraph": "[DRAFT] Something happened.",
            "uncertainty_caution": "Early signal.",
            "suggested_hashtags": ["#MedicareAdvantage"],
        },
    )
    _seed_story(
        temp_db,
        "story-b",
        "CMS proposes new Star Ratings rule",
        category="policy_regulatory",
        published=datetime(2024, 2, 1, 12, 0),
    )
    app = create_app(sample_config, temp_db)
    return TestClient(app)


def test_feed_lists_stories(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Star Ratings" in resp.text
    assert "California" in resp.text


def test_topic_filters_by_category(client):
    resp = client.get("/topics/policy_regulatory")
    assert resp.status_code == 200
    assert "Star Ratings" in resp.text
    assert "enrollment in California" not in resp.text


def test_unknown_topic_404(client):
    assert client.get("/topics/not_a_category").status_code == 404


def test_sources_page_lists_sources(client):
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "Test Feed" in resp.text
    assert "every 6h" in resp.text  # default cadence label


def test_states_overview(client):
    resp = client.get("/states")
    assert resp.status_code == 200
    assert "California" in resp.text


def test_state_detail_filters(client):
    resp = client.get("/states/CA")
    assert resp.status_code == 200
    assert "enrollment in California" in resp.text
    assert "Star Ratings" not in resp.text


def test_unknown_state_404(client):
    assert client.get("/states/ZZ").status_code == 404


def test_story_detail_renders_draft(client):
    resp = client.get("/story/story-a")
    assert resp.status_code == 200
    assert "Draft Insight Angle" in resp.text
    assert "Angle one" in resp.text


def test_unknown_story_404(client):
    assert client.get("/story/does-not-exist").status_code == 404


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["stories"] == 2
    assert body["fts_enabled"] is True
    assert "categories" in body
    assert "last_run_end" in body


def test_status_page(client):
    resp = client.get("/status")
    assert resp.status_code == 200
    assert "System Status" in resp.text
    assert "Stories by topic" in resp.text
    assert "Test Feed" in resp.text  # source listed


def test_feed_cards_have_lightweight_rating(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "card-feedback" in resp.text
    assert 'data-verdict="relevant"' in resp.text


def test_story_renders_feedback_widget(client):
    resp = client.get("/story/story-a")
    assert resp.status_code == 200
    assert 'id="feedback"' in resp.text
    assert "Is this relevant" in resp.text
    assert 'data-verdict="relevant"' in resp.text


def test_about_feedback_page(client):
    resp = client.get("/about-feedback")
    assert resp.status_code == 200
    assert "How feedback works" in resp.text


def test_submit_feedback_relevant(client, temp_db):
    resp = client.post("/feedback", json={"item_id": "story-a", "verdict": "relevant"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["feedback"]["my_verdict"] == "relevant"
    assert body["feedback"]["counts"]["relevant"] == 1


def test_submit_feedback_is_reflected_on_reload(client):
    client.post("/feedback", json={"item_id": "story-b", "verdict": "irrelevant"})
    resp = client.get("/story/story-b")
    # The 👎 button should come back pre-pressed.
    assert 'data-verdict="irrelevant"' in resp.text
    assert 'aria-pressed="true"' in resp.text


def test_submit_feedback_unknown_story_404(client):
    resp = client.post("/feedback", json={"item_id": "nope", "verdict": "relevant"})
    assert resp.status_code == 404


def test_submit_feedback_bad_verdict_400(client):
    resp = client.post("/feedback", json={"item_id": "story-a", "verdict": "spam"})
    assert resp.status_code == 400


def test_wrong_category_requires_valid_category(client):
    bad = client.post(
        "/feedback",
        json={
            "item_id": "story-a",
            "verdict": "wrong_category",
            "suggested_category": "not_a_topic",
        },
    )
    assert bad.status_code == 400
    good = client.post(
        "/feedback",
        json={
            "item_id": "story-a",
            "verdict": "wrong_category",
            "suggested_category": "policy_regulatory",
        },
    )
    assert good.status_code == 200

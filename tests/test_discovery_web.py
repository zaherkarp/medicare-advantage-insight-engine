"""Tests for the /candidates web review page."""

import pytest
from fastapi.testclient import TestClient

from ma_signal_monitor.web.app import create_app


@pytest.fixture
def client(sample_config, temp_db):
    temp_db.upsert_candidate_source(
        feed_url="https://insightoutlet.test/feed",
        domain="insightoutlet.test",
        feed_title="Insight Outlet",
        discovery_method="link_rel",
        times_seen=3,
        relevance_score=1.8,
        status="new",
    )
    temp_db.upsert_candidate_source(
        feed_url="https://promoted.test/feed",
        domain="promoted.test",
        feed_title="Promoted Outlet",
        status="auto_promoted",
    )
    app = create_app(sample_config, temp_db)
    return TestClient(app)


def test_candidates_page_lists_feeds(client):
    resp = client.get("/candidates")
    assert resp.status_code == 200
    assert "Insight Outlet" in resp.text
    assert "Promoted Outlet" in resp.text
    assert "insightoutlet.test" in resp.text


def test_candidates_status_filter(client):
    resp = client.get("/candidates?status=auto_promoted")
    assert resp.status_code == 200
    assert "Promoted Outlet" in resp.text
    assert "Insight Outlet" not in resp.text


def test_candidates_empty_when_no_match(client):
    resp = client.get("/candidates?status=rejected")
    assert resp.status_code == 200
    assert "No candidate sources" in resp.text

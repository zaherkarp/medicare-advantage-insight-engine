"""Tests for the FastAPI web frontend."""

from datetime import datetime, timedelta

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


def test_feed_has_filter_bar(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'class="filter-bar"' in resp.text
    # Every configured topic renders as a chip.
    assert 'href="/topics/policy_regulatory"' in resp.text
    assert 'href="/topics/membership_movement"' in resp.text
    # Seeded data surfaces state and payer chips, plus the directory links.
    assert 'href="/states/CA"' in resp.text
    assert 'href="/payers/unitedhealthcare"' in resp.text
    assert "All states" in resp.text and "All payers" in resp.text


def test_topic_and_state_pages_mark_active_chip(client):
    assert "chip-active" in client.get("/topics/policy_regulatory").text
    state = client.get("/states/CA")
    assert "chip-active" in state.text
    assert "Clear filter" in state.text
    # The unfiltered feed has no active chip.
    assert "chip-active" not in client.get("/").text


def test_nav_is_streamlined(client):
    resp = client.get("/")
    assert "System ▾" in resp.text
    assert 'href="/post-ideas"' in resp.text
    # Demoted sections left the top nav (they live in the filter bar and the
    # System menu now).
    assert "Topics ▾" not in resp.text
    assert "State Intelligence" not in resp.text


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


def test_feed_hides_sub_floor_noise(sample_config, temp_db):
    """Sub-floor 'noise' is kept in the archive but hidden from public views."""
    _seed_story(
        temp_db,
        "keep",
        "CMS finalizes Star Ratings rule",
        category="policy_regulatory",
        score=0.5,
    )
    _seed_story(
        temp_db,
        "noise",
        "Local parade draws a big crowd",
        category="uncategorized",
        score=0.04,
        states=["TX"],
    )
    client = TestClient(create_app(sample_config, temp_db))

    feed = client.get("/")
    assert "Star Ratings rule" in feed.text
    assert "Local parade" not in feed.text  # noise filtered from the feed

    # State intelligence pages are filtered the same way.
    assert "Local parade" not in client.get("/states/TX").text

    # But nothing is deleted: the story is still archived and directly reachable,
    # and the diagnostic counts still see the full archive (so low-yield sources
    # remain visible for pruning).
    assert client.get("/story/noise").status_code == 200
    assert client.get("/health").json()["stories"] == 2


def test_post_ideas_empty_state(client):
    """The 2024-dated fixture stories fall outside any recent rolling window."""
    resp = client.get("/post-ideas")
    assert resp.status_code == 200
    assert "Potential LinkedIn Post Topics This Week" in resp.text
    assert "No post-worthy signals" in resp.text


def test_post_ideas_groups_recent_window(sample_config, temp_db):
    now = datetime.utcnow()
    _seed_story(
        temp_db,
        "fin-1",
        "Humana flags MLR pressure",
        category="financial_pressure",
        score=0.7,
        entities=["Humana"],
        states=["FL"],
        published=now - timedelta(days=1),
        draft={
            "opening_hook": "Margin pressure is the story of the season.",
            "analytic_angles": ["Angle"],
            "draft_paragraph": "[DRAFT] x",
            "uncertainty_caution": "Early.",
            "suggested_hashtags": ["#MLR", "#MedicareAdvantage"],
        },
    )
    _seed_story(
        temp_db,
        "fin-2",
        "Benefit trims ahead",
        category="financial_pressure",
        score=0.5,
        published=now - timedelta(days=2),
    )
    _seed_story(
        temp_db,
        "pol-now",
        "CMS rule lands",
        category="policy_regulatory",
        score=0.6,
        published=now - timedelta(days=3),
    )
    # Previous-window-only story → drives the momentum comparison.
    _seed_story(
        temp_db,
        "pol-prev",
        "Older CMS rule",
        category="policy_regulatory",
        score=0.6,
        published=now - timedelta(days=10),
    )
    client = TestClient(create_app(sample_config, temp_db))

    resp = client.get("/post-ideas")
    assert resp.status_code == 200
    text = resp.text
    # financial_pressure (2 signals, new) outranks policy_regulatory (1, steady).
    assert text.index("Financial / Operating Pressure") < text.index(
        "Policy / Regulatory Changes"
    )
    assert "Margin pressure is the story of the season." in text
    assert "new this period" in text
    assert "steady vs. last period" in text
    assert "#MLR" in text
    assert 'href="/story/fin-1"' in text
    assert 'href="/payers/humana"' in text


def test_post_ideas_days_param(client):
    # Out-of-range values clamp to the max window.
    resp = client.get("/post-ideas?days=9999")
    assert resp.status_code == 200
    assert "last 90 days" in resp.text
    # Garbage falls back to the default.
    resp = client.get("/post-ideas?days=abc")
    assert resp.status_code == 200
    assert "last 7 days" in resp.text
    # Period presets render as links on the live app.
    assert 'href="/post-ideas?days=14"' in resp.text


def test_post_ideas_excludes_future_dated_stories(sample_config, temp_db):
    """A story dated in the future can't pad the current window."""
    now = datetime.utcnow()
    _seed_story(
        temp_db,
        "real",
        "CMS rule lands",
        category="policy_regulatory",
        published=now - timedelta(days=1),
    )
    _seed_story(
        temp_db,
        "future",
        "Misparsed future date",
        category="policy_regulatory",
        published=now + timedelta(days=30),
    )
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/post-ideas?days=7")
    assert resp.status_code == 200
    # Only the genuinely-recent story counts toward the window total.
    assert "1 signal" in resp.text
    assert "Misparsed future date" not in resp.text


def test_post_ideas_unknown_category_not_linked(sample_config, temp_db):
    """A theme on a stale/removed category renders as text, not a 404 link."""
    now = datetime.utcnow()
    _seed_story(
        temp_db,
        "stale",
        "Legacy topic signal",
        category="star_ratings_legacy",  # not in the config taxonomy
        published=now - timedelta(days=1),
    )
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/post-ideas")
    assert resp.status_code == 200
    # No dead link to a /topics page that doesn't exist for this key.
    assert 'href="/topics/star_ratings_legacy"' not in resp.text
    assert '<span class="badge badge-cat">' in resp.text


def test_system_dropdown_trigger_is_keyboard_focusable(client):
    """The System ▾ menu trigger must be reachable by Tab (a11y regression pin)."""
    resp = client.get("/")
    assert 'class="nav-label" tabindex="0"' in resp.text
    # ...and the stylesheet must open the menu on focus, not hover alone.
    css = client.get("/static/style.css")
    assert css.status_code == 200
    assert ":focus-within" in css.text


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
    assert "Source relevance yield" in resp.text
    # Signal-volume sparkline renders (inline SVG, no JS).
    assert "Signal volume" in resp.text
    assert "<svg" in resp.text and "spark-line" in resp.text


def test_status_flags_low_yield_source(sample_config, temp_db):
    # 30 stories from one source, all well below the 0.3 threshold → flagged.
    for i in range(30):
        _seed_story(
            temp_db,
            f"junk-{i}",
            f"Off-topic item {i}",
            category="membership_movement",
            score=0.02,
        )
    app = create_app(sample_config, temp_db)
    resp = TestClient(app).get("/status")
    assert resp.status_code == 200
    assert "flagged for review" in resp.text
    assert ">review<" in resp.text


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


def test_feed_hides_duplicates_but_status_counts_them(sample_config, temp_db):
    """The feed shows one representative; /status reports the full archive."""
    from ma_signal_monitor.models import NormalizedItem, ScoredItem

    def seed(item_id, title, source, dup_of=None):
        item = NormalizedItem(
            item_id=item_id,
            source_name=source,
            source_type="rss",
            source_priority=4,
            source_tags=[],
            title=title,
            link=f"https://example.com/{item_id}",
            published_date=datetime(2024, 1, 1, 12, 0),
            summary="",
        )
        temp_db.upsert_story(
            ScoredItem(
                item=item, relevance_score=0.6, matched_categories=["policy_regulatory"]
            ),
            primary_category="policy_regulatory",
            duplicate_of=dup_of,
        )

    seed("rep", "UnitedHealth reaches insulin settlement", "Healthcare Dive")
    seed("dup", "UnitedHealth reaches insulin settlement", "Beckers", dup_of="rep")
    client = TestClient(create_app(sample_config, temp_db))

    feed = client.get("/")
    assert "Healthcare Dive" in feed.text  # representative shown
    assert "Beckers" not in feed.text  # duplicate hidden from the feed

    status = client.get("/status")
    assert "2" in status.text  # full archive count includes the duplicate
    assert client.get("/health").json()["stories"] == 2


def test_story_page_shows_also_reported_by(sample_config, temp_db):
    from ma_signal_monitor.models import NormalizedItem, ScoredItem

    def seed(item_id, source, dup_of=None):
        item = NormalizedItem(
            item_id=item_id,
            source_name=source,
            source_type="rss",
            source_priority=4,
            source_tags=[],
            title="Same story",
            link=f"https://example.com/{item_id}",
            published_date=datetime(2024, 1, 1, 12, 0),
            summary="",
        )
        temp_db.upsert_story(
            ScoredItem(
                item=item, relevance_score=0.6, matched_categories=["policy_regulatory"]
            ),
            primary_category="policy_regulatory",
            duplicate_of=dup_of,
        )

    seed("rep", "Healthcare Dive")
    seed("dup", "Beckers Payer Issues", dup_of="rep")
    client = TestClient(create_app(sample_config, temp_db))

    rep_page = client.get("/story/rep")
    assert "Also reported by" in rep_page.text
    assert "Beckers Payer Issues" in rep_page.text
    # The duplicate's own page has no "also reported by" block.
    assert "Also reported by" not in client.get("/story/dup").text

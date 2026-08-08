"""Tests for the FastAPI web frontend."""

import dataclasses
import re
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
    categories=None,
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
    # ``categories`` seeds the full multi-category lens the Angles engine reads;
    # ``category`` stays the stored primary. Defaults to a single-category story.
    scored = ScoredItem(
        item=item,
        relevance_score=score,
        matched_categories=categories or [category],
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
    assert '<a class="chip chip-active"' in client.get("/topics/policy_regulatory").text
    state = client.get("/states/CA")
    assert '<a class="chip chip-active"' in state.text
    assert "Clear filter" in state.text
    # The unfiltered feed marks no *filter* chip active. (The coverage-window
    # picker renders its own active chip, but as a <span>, not a filter link.)
    assert '<a class="chip chip-active"' not in client.get("/").text


def test_nav_is_streamlined(client):
    resp = client.get("/")
    assert "System ▾" in resp.text
    assert 'href="/angles"' in resp.text
    assert 'href="/timeline"' in resp.text
    assert 'href="/ask"' in resp.text
    # Demoted sections left the top nav (they live in the filter bar and the
    # System menu now).
    assert "Topics ▾" not in resp.text
    assert "State Intelligence" not in resp.text


class TestAsk:
    """Natural-language query over the archive (read-only, no new engine)."""

    def test_blank_question_just_renders_the_form(self, client):
        resp = client.get("/ask")
        assert resp.status_code == 200
        assert "<form" in resp.text
        assert "Parsed as:" not in resp.text

    def test_category_only_question_filters_by_category(self, client):
        # "star ratings" resolves to the policy_regulatory category via its
        # taxonomy keyword — a pure structured filter, no leftover keywords.
        resp = client.get("/ask", params={"q": "star ratings"})
        assert resp.status_code == 200
        assert "Star Ratings rule" in resp.text  # story-b
        assert "enrollment in California" not in resp.text  # story-a excluded
        assert "policy_regulatory" in resp.text

    def test_keyword_fallback_question_uses_full_text_search(self, client):
        # "expands" matches no taxonomy vocabulary, so it falls through to
        # the FTS keyword search over title/summary.
        resp = client.get("/ask", params={"q": "expands"})
        assert resp.status_code == 200
        assert "enrollment in California" in resp.text  # story-a
        assert "Star Ratings rule" not in resp.text  # story-b excluded

    def test_no_matches_shows_empty_state(self, client):
        resp = client.get("/ask", params={"q": "nonexistent topic zzy"})
        assert resp.status_code == 200
        assert "No signals match" in resp.text

    def test_never_writes_to_the_archive(self, client, temp_db):
        before = temp_db.count_stories()
        client.get("/ask", params={"q": "everything above alert grade since March"})
        assert temp_db.count_stories() == before


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


def test_angles_empty_state(client):
    """The 2024-dated fixture stories fall outside any recent rolling window."""
    resp = client.get("/angles")
    assert resp.status_code == 200
    assert "<h1>Angles</h1>" in resp.text
    assert "No signals in this period yet." in resp.text
    # The About-this-model panel renders even with no cards (the model is
    # declared in config, independent of the window's contents).
    assert "About this model" in resp.text
    assert "<details" in resp.text


def test_angles_end_to_end_intersection_render(sample_config, temp_db):
    now = datetime.utcnow()
    # A Humana cascade policy → financial (the payer active on both edge ends).
    _seed_story(
        temp_db,
        "h-pol1",
        "CMS rate notice hits Humana",
        category="policy_regulatory",
        entities=["Humana"],
        score=0.9,
        published=now - timedelta(days=1),
    )
    _seed_story(
        temp_db,
        "h-pol2",
        "Final rule pressures Humana plans",
        category="policy_regulatory",
        entities=["Humana"],
        score=0.85,
        published=now - timedelta(days=2),
    )
    _seed_story(
        temp_db,
        "h-fin1",
        "Humana warns on margins in Florida",
        category="financial_pressure",
        entities=["Humana"],
        states=["FL"],
        score=0.8,
        published=now - timedelta(days=2),
    )
    # A plain payer/topic/state overlap (no declared edge → ∩, not →).
    _seed_story(
        temp_db,
        "m1",
        "UnitedHealthcare grows in Texas",
        category="membership_movement",
        entities=["UnitedHealthcare"],
        states=["TX"],
        score=0.7,
        published=now - timedelta(days=1),
    )
    _seed_story(
        temp_db,
        "m2",
        "UnitedHealthcare Texas push",
        category="membership_movement",
        entities=["UnitedHealthcare"],
        states=["TX"],
        score=0.6,
        published=now - timedelta(days=2),
    )
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/angles")
    assert resp.status_code == 200
    text = resp.text
    # Partitioned sections, with the causal cascade up top.
    assert "Causal chains in motion" in text
    assert "More angles" in text
    assert "venn-cascade" in text  # a three-circle payer cascade card rendered
    assert "→" in text and "∩" in text  # directional + plain separators
    # Per-card model evidence, fact line, glyph, badges, and links.
    assert "Model evidence" in text
    assert "CMS final-rule impact analyses" in text  # fixture evidence sentence
    assert "Strongest:" in text
    assert "badge-angle-type" in text and "badge-layer" in text
    assert "aria-hidden" in text
    assert 'class="sr-only"' in text
    assert 'href="/payers/humana"' in text
    assert 'href="/story/h-pol1"' in text
    # About panel lists the fixture edge.
    assert "About this model" in text
    assert "Policy / Regulatory Changes → Financial / Operating Pressure" in text


def test_angles_momentum_labels(sample_config, temp_db):
    now = datetime.utcnow()
    for i, day in enumerate((1, 2, 3)):
        _seed_story(
            temp_db,
            f"n{i}",
            f"Humana enrollment story {i}",
            category="membership_movement",
            entities=["Humana"],
            published=now - timedelta(days=day),
        )
    # The same overlap had one story in the previous window → momentum "up".
    _seed_story(
        temp_db,
        "o1",
        "Humana earlier enrollment gain",
        category="membership_movement",
        entities=["Humana"],
        published=now - timedelta(days=10),
    )
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/angles")
    assert "up from 1 last period" in resp.text


def test_angles_chain_seed_renders_causal_card(sample_config, temp_db):
    now = datetime.utcnow()
    # Two dual-category stories form a topic∩topic overlap lying along the
    # declared policy → financial edge.
    _seed_story(
        temp_db,
        "c1",
        "CMS rate notice squeezes MA margins",
        category="policy_regulatory",
        categories=["policy_regulatory", "financial_pressure"],
        published=now - timedelta(days=1),
    )
    _seed_story(
        temp_db,
        "c2",
        "Final rule pressures plan margins",
        category="policy_regulatory",
        categories=["policy_regulatory", "financial_pressure"],
        published=now - timedelta(days=2),
    )
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/angles")
    text = resp.text
    assert "Causal chains in motion" in text
    # Directional chain label + the combined layer badge.
    assert "Policy / Regulatory Changes → Financial / Operating Pressure" in text
    assert "Drivers → Pressure" in text
    assert "Model evidence" in text
    assert "CMS final-rule impact analyses" in text


def test_angles_consistency_line_needs_upstream_previous(sample_config, temp_db):
    now = datetime.utcnow()
    _seed_story(
        temp_db,
        "c1",
        "CMS rule and margins",
        category="policy_regulatory",
        categories=["policy_regulatory", "financial_pressure"],
        published=now - timedelta(days=1),
    )
    _seed_story(
        temp_db,
        "c2",
        "Rate notice squeezes margins",
        category="policy_regulatory",
        categories=["policy_regulatory", "financial_pressure"],
        published=now - timedelta(days=2),
    )
    # An upstream (policy) signal in the previous window makes the sequence
    # consistent: source active last period, target rising now.
    _seed_story(
        temp_db,
        "prev-pol",
        "Earlier CMS proposal",
        category="policy_regulatory",
        published=now - timedelta(days=10),
    )
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/angles")
    assert "Sequence consistent with the model this period" in resp.text


def test_angles_days_param(client):
    # Out-of-range values clamp to the max window.
    resp = client.get("/angles?days=9999")
    assert resp.status_code == 200
    assert "last 90 days" in resp.text
    # Garbage falls back to the default.
    resp = client.get("/angles?days=abc")
    assert resp.status_code == 200
    assert "last 7 days" in resp.text
    # Period presets render as links on the live app.
    assert 'href="/angles?days=14"' in resp.text


def test_angles_excludes_future_dated_stories(sample_config, temp_db):
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
    resp = client.get("/angles?days=7")
    assert resp.status_code == 200
    assert "1 signal this period" in resp.text  # only the genuinely-recent story
    assert "Misparsed future date" not in resp.text


def test_angles_fallback_topic_card(sample_config, temp_db):
    """A sparse window (no overlaps) falls back to single-lens topic cards."""
    now = datetime.utcnow()
    _seed_story(
        temp_db,
        "t1",
        "Policy update one",
        category="policy_regulatory",
        published=now - timedelta(days=1),
    )
    _seed_story(
        temp_db,
        "t2",
        "Policy update two",
        category="policy_regulatory",
        published=now - timedelta(days=2),
    )
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/angles")
    assert resp.status_code == 200
    assert 'badge-angle-type">Topic<' in resp.text  # the single-lens fallback card
    assert "Policy / Regulatory Changes" in resp.text
    # No causal section, so no "More angles" contrast heading either.
    assert "Causal chains in motion" not in resp.text
    assert "More angles" not in resp.text


def test_angles_unknown_category_not_linked(sample_config, temp_db):
    """An overlap on a stale/removed category renders as text, not a 404 link."""
    now = datetime.utcnow()
    _seed_story(
        temp_db,
        "s1",
        "Legacy topic signal one",
        category="star_ratings_legacy",  # not in the config taxonomy
        categories=["star_ratings_legacy", "financial_pressure"],
        published=now - timedelta(days=1),
    )
    _seed_story(
        temp_db,
        "s2",
        "Legacy topic signal two",
        category="star_ratings_legacy",
        categories=["star_ratings_legacy", "financial_pressure"],
        published=now - timedelta(days=2),
    )
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/angles")
    assert resp.status_code == 200
    # No dead link to a /topics page that doesn't exist for this key.
    assert 'href="/topics/star_ratings_legacy"' not in resp.text
    # ...but the valid side of the same overlap still links.
    assert 'href="/topics/financial_pressure"' in resp.text


def test_post_ideas_redirects_to_angles(client):
    """The former ``/post-ideas`` path 301s to the renamed page."""
    resp = client.get("/post-ideas", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/angles"
    # An all-digits days preset is forwarded; the target clamps it.
    resp = client.get("/post-ideas?days=14", follow_redirects=False)
    assert resp.headers["location"] == "/angles?days=14"
    # A non-numeric days value is dropped, never reflected into the new URL.
    resp = client.get("/post-ideas?days=abc", follow_redirects=False)
    assert resp.headers["location"] == "/angles"
    # Following the redirect lands on the Angles page.
    resp = client.get("/post-ideas")
    assert resp.status_code == 200
    assert "<h1>Angles</h1>" in resp.text


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


def _log_silent_source(temp_db, source_name, days_ago):
    run_id = temp_db.start_run()
    conn = temp_db._get_conn()
    old = (datetime.utcnow() - timedelta(days=days_ago)).isoformat()
    conn.execute(
        """INSERT INTO source_fetch_log
               (run_id, source_name, fetched_at, status, n_items, n_persisted, error)
           VALUES (?, ?, ?, 'error', 0, 0, '403 Forbidden')""",
        (run_id, source_name, old),
    )
    conn.commit()


def test_status_flags_silent_source(sample_config, temp_db):
    _log_silent_source(temp_db, "Test Feed", days_ago=30)
    app = create_app(sample_config, temp_db)
    resp = TestClient(app).get("/status")
    assert resp.status_code == 200
    assert "Silent sources" in resp.text
    assert "403 Forbidden" in resp.text


def test_status_no_silent_sources_by_default(client):
    resp = client.get("/status")
    assert "No silent sources" in resp.text


def test_sources_page_flags_silent_source(sample_config, temp_db):
    _log_silent_source(temp_db, "Test Feed", days_ago=30)
    app = create_app(sample_config, temp_db)
    resp = TestClient(app).get("/sources")
    assert resp.status_code == 200
    assert "gone silent" in resp.text
    assert ">silent<" in resp.text


def test_sources_page_no_silent_badge_by_default(client):
    resp = client.get("/sources")
    assert ">silent<" not in resp.text


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


# --- Per-card related-coverage timelines ---


def test_feed_cards_show_related_coverage_timeline(sample_config, temp_db):
    now = datetime.utcnow()
    _seed_story(
        temp_db,
        "uhc-recent",
        "UnitedHealthcare adds counties",
        category="membership_movement",
        entities=["UnitedHealthcare"],
        published=now,
    )
    _seed_story(
        temp_db,
        "uhc-recent-2",
        "UnitedHealthcare earnings",
        category="financial_pressure",
        entities=["UnitedHealth"],
        published=now - timedelta(days=3),
    )
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'class="sparkline card-timeline"' in resp.text
    assert 'class="spark-svg"' in resp.text
    # Caption names the canonical payer group and links to its scoped timeline.
    assert 'href="/timeline/payers/unitedhealthcare"' in resp.text
    assert "last 30 days" in resp.text
    # A non-default window is carried into the caption's timeline link.
    assert (
        'href="/timeline/payers/unitedhealthcare?days=7"' in client.get("/?days=7").text
    )


def test_timeline_days_param_clamps(sample_config, temp_db):
    _seed_story(
        temp_db,
        "r1",
        "Humana update",
        category="membership_movement",
        entities=["Humana"],
        published=datetime.utcnow(),
    )
    client = TestClient(create_app(sample_config, temp_db))
    assert "last 90 days" in client.get("/?days=9999").text  # clamped to the cap
    assert "last 1 days" in client.get("/?days=0").text  # clamped up to 1
    assert "last 30 days" in client.get("/?days=abc").text  # garbage → default


def test_period_picker_renders_on_feed(sample_config, temp_db):
    _seed_story(
        temp_db,
        "r1",
        "Humana update",
        category="membership_movement",
        entities=["Humana"],
        published=datetime.utcnow(),
    )
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/")
    assert "Coverage window" in resp.text
    assert 'href="/?days=14"' in resp.text  # a non-active preset links out
    assert '<span class="chip chip-active">30 days</span>' in resp.text  # default


def test_pagination_preserves_nondefault_days(sample_config, temp_db):
    sample_config.web_page_size = 1  # force a second page
    now = datetime.utcnow()
    _seed_story(
        temp_db,
        "p1",
        "Humana one",
        category="membership_movement",
        entities=["Humana"],
        published=now,
    )
    _seed_story(
        temp_db,
        "p2",
        "Humana two",
        category="membership_movement",
        entities=["Humana"],
        published=now - timedelta(days=1),
    )
    client = TestClient(create_app(sample_config, temp_db))
    # A non-default window is carried through the Older link (& is HTML-escaped).
    assert "page=2&amp;days=7" in client.get("/?days=7").text
    # At the default window, no days= is appended to the pagination link (the
    # picker still has its own days= links, so scope the check to pagination).
    older = client.get("/")
    assert 'href="/?page=2"' in older.text
    assert "page=2&amp;days" not in older.text


def test_entityless_story_falls_back_to_category_scope(sample_config, temp_db):
    _seed_story(
        temp_db,
        "cat-only",
        "CMS finalizes a rule",  # no entities → scope is the topic
        category="policy_regulatory",
        published=datetime.utcnow(),
    )
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/")
    assert 'class="sparkline card-timeline"' in resp.text
    assert 'href="/timeline/topics/policy_regulatory"' in resp.text
    assert "Policy / Regulatory Changes" in resp.text


def test_uncategorized_entityless_story_has_no_timeline(sample_config, temp_db):
    _seed_story(
        temp_db,
        "bare",
        "A bare signal",  # no entities, no real category → nothing to plot
        category="uncategorized",
        published=datetime.utcnow(),
    )
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "A bare signal" in resp.text  # the card still renders
    assert "card-timeline" not in resp.text  # but with no timeline


def test_old_story_shows_no_zero_total_timeline(client):
    # The shared fixture's stories are dated 2024 — outside any window — so their
    # coverage series are all-zero and the timeline is suppressed as noise.
    resp = client.get("/")
    assert resp.status_code == 200
    assert "card-timeline" not in resp.text


def test_search_results_show_timelines_and_picker_preserves_query(
    sample_config, temp_db
):
    _seed_story(
        temp_db,
        "s-humana",
        "Humana expands Medicare Advantage footprint",
        category="membership_movement",
        entities=["Humana"],
        published=datetime.utcnow(),
    )
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/search?q=Humana")
    assert resp.status_code == 200
    assert 'class="sparkline card-timeline"' in resp.text
    # The picker keeps both the query and the chosen window in its links
    # (the ampersand joining them is HTML-escaped in the rendered attribute).
    assert 'href="/search?q=Humana&amp;days=14"' in resp.text


# --- Timeline page (layered callout band + topic strip) ---


def test_topic_color_global_returns_positional_palette_color(sample_config, temp_db):
    """The `topic_color` Jinja global (registered next to `category_label`)
    resolves the first configured category to the first palette hex."""
    app = create_app(sample_config, temp_db)
    topic_color = app.state.templates.env.globals["topic_color"]
    assert topic_color(sample_config.categories[0].key) == "#2a78d6"


def test_timeline_page_renders_topic_strip(sample_config, temp_db):
    now = datetime.utcnow()
    _seed_story(
        temp_db,
        "uhc-1",
        "UnitedHealthcare adds counties",
        category="membership_movement",
        entities=["UnitedHealthcare"],
        published=now - timedelta(days=1),
    )
    _seed_story(
        temp_db,
        "pol-1",
        "CMS proposes a rule",
        category="policy_regulatory",
        published=now - timedelta(days=3),
    )
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/timeline")
    assert resp.status_code == 200
    text = resp.text
    assert "<h1>Signal Timeline</h1>" in text
    assert "2 signals in the last 30 days" in text
    # One strip row per configured topic (config order), rendered even when
    # quiet, each wearing its config-order palette color.
    for cat in sample_config.categories:
        assert cat.label in text
    assert 'style="--topic: #2a78d6"' in text  # membership_movement (index 0)
    assert 'style="--topic: #008300"' in text  # policy_regulatory (index 1)
    # Strip labels link to the scoped topic timelines.
    assert 'href="/timeline/topics/membership_movement"' in text
    # The legend lists every configured topic (no "Other" — nothing stray).
    assert 'class="timeline-legend"' in text
    assert text.count('class="legend-item"') == len(sample_config.categories)
    # The window's strongest stories plot as labeled callout cards.
    assert 'class="callout"' in text
    assert 'href="/story/uhc-1"' in text
    assert 'href="/story/pol-1"' in text
    assert "UnitedHealthcare adds counties" in text
    assert "Test Feed" in text
    assert 'class="strip-bubble"' in text
    # The plotted set is listed below the chart; the axis labels the days.
    assert 'id="timeline-stories"' in text
    assert 'class="axis-tick' in text
    # The filter bar's chips point at scoped timelines, not the feed.
    assert 'href="/timeline/states/' not in text  # no states seeded here
    assert 'href="/timeline/payers/unitedhealthcare"' in text


def test_timeline_topic_scope_flips_to_payer_strip_one_color(sample_config, temp_db):
    now = datetime.utcnow()
    _seed_story(
        temp_db,
        "h-1",
        "Humana reacts to the rule",
        category="policy_regulatory",
        entities=["Humana"],
        published=now - timedelta(days=1),
    )
    _seed_story(
        temp_db,
        "plain",
        "CMS proposes a rule",  # entityless → lands in the "Other" row
        category="policy_regulatory",
        published=now - timedelta(days=2),
    )
    _seed_story(
        temp_db,
        "other-topic",
        "UnitedHealthcare adds counties",
        category="membership_movement",
        entities=["UnitedHealthcare"],
        published=now - timedelta(days=1),
    )
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/timeline/topics/policy_regulatory")
    assert resp.status_code == 200
    text = resp.text
    # Under a topic filter the row dimension flips to payers.
    assert 'href="/timeline/payers/humana"' in text
    assert "<span>Other</span>" in text
    # Only this topic's stories are plotted/listed; the topic chip highlights.
    assert 'href="/story/h-1"' in text and 'href="/story/plain"' in text
    assert "UnitedHealthcare adds counties" not in text
    assert 'class="chip chip-active"' in text
    # No legend under a single-topic scope — every row already shares one color.
    assert 'class="timeline-legend"' not in text
    # Every payer row (Humana + Other) wears policy_regulatory's single color.
    colors = re.findall(r'class="strip-plot" style="--topic: (#[0-9a-fA-F]{6})"', text)
    assert len(colors) == 2
    assert len(set(colors)) == 1
    assert colors[0] == "#008300"  # policy_regulatory is config index 1
    assert client.get("/timeline/topics/not_a_category").status_code == 404


def test_timeline_payer_scope_marks_chip_active(sample_config, temp_db):
    now = datetime.utcnow()
    _seed_story(
        temp_db,
        "h-1",
        "Humana grows in Florida",
        category="membership_movement",
        entities=["Humana"],
        published=now - timedelta(days=1),
    )
    _seed_story(
        temp_db,
        "u-1",
        "UnitedHealthcare retreats",
        category="membership_movement",
        entities=["UnitedHealthcare"],
        published=now - timedelta(days=1),
    )
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/timeline/payers/humana")
    assert resp.status_code == 200
    text = resp.text
    assert "Timeline — Humana" in text
    assert 'href="/story/h-1"' in text
    assert "UnitedHealthcare retreats" not in text
    # The payer chip is highlighted (feed pages never mark payer chips active).
    assert 'class="chip chip-active"' in text
    assert client.get("/timeline/payers/nope").status_code == 404


def test_timeline_state_scope(sample_config, temp_db):
    now = datetime.utcnow()
    _seed_story(
        temp_db,
        "ca-1",
        "California expansion news",
        category="membership_movement",
        states=["CA"],
        published=now - timedelta(days=1),
    )
    _seed_story(
        temp_db,
        "tx-1",
        "Texas-only development",
        category="membership_movement",
        states=["TX"],
        published=now - timedelta(days=1),
    )
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/timeline/states/CA")
    assert resp.status_code == 200
    assert "California expansion news" in resp.text
    assert "Texas-only development" not in resp.text
    assert client.get("/timeline/states/ZZ").status_code == 404


def test_timeline_page_days_param_clamps(sample_config, temp_db):
    _seed_story(
        temp_db,
        "r1",
        "Humana update",
        category="membership_movement",
        entities=["Humana"],
        published=datetime.utcnow(),
    )
    client = TestClient(create_app(sample_config, temp_db))
    # The /timeline page's own clamp ceiling is wider than the feed's (365).
    assert "in the last 365 days" in client.get("/timeline?days=9999").text
    assert "in the last 30 days" in client.get("/timeline?days=abc").text
    resp = client.get("/timeline")
    # Root picker chips are plain paths (D5), not ?days= links, so they survive
    # a static export unchanged.
    assert 'href="/timeline/w/7"' in resp.text
    assert 'href="/timeline/w/all"' in resp.text
    assert 'href="/timeline?days=' not in resp.text
    # The active (default 30-day) chip renders as an inert span, not a link.
    assert '<span class="chip chip-active">30 days</span>' in resp.text
    assert 'href="/timeline/w/30"' not in resp.text


def test_timeline_window_all_shows_all_time_label(sample_config, temp_db):
    # "all" spans back to the archive's true oldest story, clamped to 730 days
    # (D6) — 400 days ago safely clears the 30-day default without hitting
    # that cap.
    _seed_story(
        temp_db,
        "old",
        "An old-archive signal",
        category="policy_regulatory",
        published=datetime.utcnow() - timedelta(days=400),
    )
    client = TestClient(create_app(sample_config, temp_db))
    assert "An old-archive signal" not in client.get("/timeline").text
    resp = client.get("/timeline/w/all")
    assert resp.status_code == 200
    assert "all time" in resp.text
    assert "An old-archive signal" in resp.text


def test_timeline_window_30_redirects_to_root(sample_config, temp_db):
    client = TestClient(create_app(sample_config, temp_db), follow_redirects=False)
    resp = client.get("/timeline/w/30")
    assert resp.status_code == 301
    assert resp.headers["location"] == "/timeline"


def test_timeline_window_unknown_token_404(sample_config, temp_db):
    client = TestClient(create_app(sample_config, temp_db))
    assert client.get("/timeline/w/nope").status_code == 404


def test_timeline_empty_window_shows_empty_state(client):
    """The 2024-dated fixture stories fall outside any recent window."""
    resp = client.get("/timeline")
    assert resp.status_code == 200
    assert "0 signals in the last 30 days" in resp.text
    assert "No signals in this window" in resp.text
    assert 'class="callout"' not in resp.text
    assert 'id="timeline-stories"' not in resp.text


def test_timeline_list_truncates_past_100_but_total_stays_accurate(
    sample_config, temp_db
):
    now = datetime.utcnow()
    for i in range(120):
        _seed_story(
            temp_db,
            f"burst-{i}",
            f"Rule fallout piece {i}",
            category="policy_regulatory",
            score=0.3 + (i % 50) / 100,
            published=now - timedelta(hours=i),
        )
    client = TestClient(create_app(sample_config, temp_db))
    text = client.get("/timeline").text
    assert "120 signals in the last 30 days" in text
    assert "Showing the 100 most recent of 120" in text
    assert text.count('class="story-card"') == 100


def test_story_page_links_to_scoped_timeline(client, sample_config, temp_db):
    # story-a mentions UnitedHealthcare → its payer timeline.
    assert (
        'href="/timeline/payers/unitedhealthcare"' in client.get("/story/story-a").text
    )
    # story-b is entityless with a real topic → the topic timeline.
    assert (
        'href="/timeline/topics/policy_regulatory"' in client.get("/story/story-b").text
    )
    # An uncategorized, entityless story gets no timeline link.
    _seed_story(temp_db, "bare", "A bare signal", category="uncategorized")
    assert "View related coverage" not in client.get("/story/bare").text


def _seed_recent(store, item_id, title, *, category, entities=None):
    """Seed a story dated now so it lands inside the timeline's default window."""
    _seed_story(
        store,
        item_id,
        title,
        category=category,
        entities=entities,
        published=datetime.utcnow(),
    )


def test_timeline_threads_lane_clusters_and_labels(sample_config, temp_db):
    # Two near-identical Star Ratings stories cluster into one emergent thread;
    # an unrelated story folds into the honest "Ungrouped signals" row.
    _seed_recent(
        temp_db,
        "th-1",
        "CMS finalizes Star Ratings methodology for Medicare Advantage",
        category="policy_regulatory",
        entities=["CMS"],
    )
    _seed_recent(
        temp_db,
        "th-2",
        "CMS Star Ratings methodology update for Medicare Advantage plans",
        category="policy_regulatory",
        entities=["CMS"],
    )
    _seed_recent(
        temp_db,
        "th-3",
        "Aetna launches value-based care partnership network",
        category="competitive_strategy",
        entities=["Aetna"],
    )
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/timeline/threads")
    assert resp.status_code == 200
    # The grouping toggle renders (Threads view), and the emergent thread is
    # named from its own distinctive language in the threads legend.
    assert 'class="timeline-views"' in resp.text
    assert "timeline-legend-threads" in resp.text
    assert re.search(r"star|ratings|methodolog", resp.text, re.I)
    assert "Ungrouped signals" in resp.text


def test_timeline_root_shows_threads_toggle(client):
    resp = client.get("/timeline")
    assert resp.status_code == 200
    assert 'class="timeline-views"' in resp.text
    assert 'href="/timeline/threads"' in resp.text


def test_timeline_threads_disabled_returns_404(sample_config, temp_db):
    cfg = dataclasses.replace(sample_config, threads_enabled=False)
    client = TestClient(create_app(cfg, temp_db))
    assert client.get("/timeline/threads").status_code == 404


def _seed_star_thread(store):
    """The two-story Star Ratings thread shared by the detail-route tests.

    th-1 and th-2 tie on relevance_score (both default to 0.6), so the
    item_id tie-break in threads.py's member ranking anchors the thread on
    "th-1" -- deterministic, so these tests can assert the exact key rather
    than discovering it by scraping the strip row's href.
    """
    _seed_recent(
        store,
        "th-1",
        "CMS finalizes Star Ratings methodology for Medicare Advantage",
        category="policy_regulatory",
        entities=["CMS"],
    )
    _seed_recent(
        store,
        "th-2",
        "CMS Star Ratings methodology update for Medicare Advantage plans",
        category="policy_regulatory",
        entities=["CMS"],
    )
    _seed_recent(
        store,
        "th-3",
        "Aetna launches value-based care partnership network",
        category="competitive_strategy",
        entities=["Aetna"],
    )


def test_timeline_thread_row_links_to_detail_page_that_resolves(sample_config, temp_db):
    _seed_star_thread(temp_db)
    client = TestClient(create_app(sample_config, temp_db))
    lane = client.get("/timeline/threads")
    assert 'href="/timeline/threads/th-1"' in lane.text

    detail = client.get("/timeline/threads/th-1")
    assert detail.status_code == 200
    assert re.search(r"star|ratings|methodolog", detail.text, re.I)
    # Both thread members show up in the detail page's story list, the
    # unrelated solo story does not.
    assert detail.text.count('class="story-card"') == 2
    assert "th-3" not in detail.text


def test_timeline_thread_unknown_key_404s(sample_config, temp_db):
    _seed_star_thread(temp_db)
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/timeline/threads/not-a-real-key")
    assert resp.status_code == 404


def test_timeline_thread_dissolved_anchor_redirects_to_story(sample_config, temp_db):
    # th-3 is a real item_id but forms no thread on its own (it's the lone
    # "Ungrouped signals" story) -- graceful degradation sends the reader to
    # the story itself rather than 404ing on a key that once could have
    # anchored something.
    _seed_star_thread(temp_db)
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/timeline/threads/th-3", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/story/th-3"


def test_timeline_threads_disabled_detail_route_also_404s(sample_config, temp_db):
    _seed_star_thread(temp_db)
    cfg = dataclasses.replace(sample_config, threads_enabled=False)
    client = TestClient(create_app(cfg, temp_db))
    assert client.get("/timeline/threads/th-1").status_code == 404


def test_timeline_thread_mixed_chip_renders(sample_config, temp_db):
    # Four near-duplicate headlines split 50/50 across two categories cluster
    # into one thread with no clear majority -- both the lane's legend and
    # the thread's own detail page must render a "Mixed" chip rather than an
    # arbitrarily-picked causal layer.
    _seed_recent(
        temp_db,
        "m1",
        "Prior authorization crackdown hits Medicare Advantage insurers nationwide",
        category="policy_regulatory",
    )
    _seed_recent(
        temp_db,
        "m2",
        "Prior authorization crackdown squeezes Medicare Advantage insurers nationwide",
        category="policy_regulatory",
    )
    _seed_recent(
        temp_db,
        "m3",
        "Prior authorization crackdown rattles Medicare Advantage insurers nationwide",
        category="financial_pressure",
    )
    _seed_recent(
        temp_db,
        "m4",
        "Prior authorization crackdown worries Medicare Advantage insurers nationwide",
        category="financial_pressure",
    )
    fillers = [
        (
            "f1",
            "Aetna launches value-based care partnership network for seniors",
            "competitive_strategy",
        ),
        (
            "f2",
            "Humana announces new leadership team amid strategic overhaul",
            "competitive_strategy",
        ),
        (
            "f3",
            "Centene reports quarterly earnings above analyst expectations",
            "financial_pressure",
        ),
        (
            "f4",
            "Molina expands footprint into three additional states",
            "membership_movement",
        ),
        (
            "f5",
            "Kaiser opens new telehealth clinics across rural regions",
            "competitive_strategy",
        ),
        (
            "f6",
            "Elevance unveils digital front door for member engagement",
            "competitive_strategy",
        ),
    ]
    for item_id, title, category in fillers:
        _seed_recent(temp_db, item_id, title, category=category)

    client = TestClient(create_app(sample_config, temp_db))
    lane = client.get("/timeline/threads")
    assert lane.status_code == 200
    assert "Mixed" in lane.text

    detail = client.get("/timeline/threads/m1")
    assert detail.status_code == 200
    assert "Mixed" in detail.text
    assert detail.text.count('class="story-card"') == 4


def _seed_dated(store, item_id, title, *, category, entities=None, days_ago=0):
    """Like _seed_recent, but with a controllable event date -- needed to
    exercise build_thread_links' temporal-precedence rule, which _seed_recent
    (always "now") can't."""
    _seed_story(
        store,
        item_id,
        title,
        category=category,
        entities=entities,
        published=datetime.utcnow() - timedelta(days=days_ago),
    )


def _seed_causal_cascade(store):
    """A Star Ratings (policy) thread followed, later in the window, by an MLR
    (financial) thread -- both mentioning Humana, so build_thread_links has a
    declared edge (policy_regulatory -> financial_pressure), temporal
    precedence, AND shared thread-level evidence (the "@humana" payer token)
    all at once, and should draw exactly one "leads to" link between them."""
    _seed_dated(
        store,
        "L1",
        "CMS finalizes Star Ratings methodology for Medicare Advantage",
        category="policy_regulatory",
        entities=["Humana"],
        days_ago=10,
    )
    _seed_dated(
        store,
        "L2",
        "CMS Star Ratings methodology update for Medicare Advantage plans",
        category="policy_regulatory",
        entities=["Humana"],
        days_ago=9,
    )
    _seed_dated(
        store,
        "L3",
        "Humana warns of rising medical loss ratio and margin pressure",
        category="financial_pressure",
        entities=["Humana"],
        days_ago=2,
    )
    _seed_dated(
        store,
        "L4",
        "Humana flags rising medical loss ratio squeezing margin",
        category="financial_pressure",
        entities=["Humana"],
        days_ago=1,
    )


def test_timeline_threads_lane_renders_band_header_and_leads_to_chip(
    sample_config, temp_db
):
    _seed_causal_cascade(temp_db)
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/timeline/threads")
    assert resp.status_code == 200
    text = resp.text

    # Causal-layer band header, reusing the existing .legend-layer chip style.
    assert 'class="strip-band' in text
    assert re.search(r'class="strip-band[^"]*">\s*<span class="legend-layer">', text)

    # The "leads to" chip: the reused angle-sep/sr-only idiom, arrow visible,
    # "leading to" for assistive tech only, and a real link to the target
    # thread's own page.
    assert 'class="strip-link"' in text
    assert re.search(
        r'class="strip-link"><span class="angle-sep" aria-hidden="true">→</span>'
        r'<span class="sr-only"> leading to </span><a href="(/timeline/threads/[^"]+)"',
        text,
    )
    hrefs = re.findall(
        r'class="strip-link">.*?<a href="(/timeline/threads/[^"]+)"', text
    )
    assert hrefs

    # The reciprocal back-link shows up on the downstream thread's own page.
    target_key = hrefs[0].rsplit("/", 1)[-1]
    detail = client.get(f"/timeline/threads/{target_key}")
    assert detail.status_code == 200
    assert re.search(
        r'<span class="angle-sep" aria-hidden="true">←</span>'
        r'<span class="sr-only"> caused by </span>',
        detail.text,
    )


def test_timeline_topics_page_has_no_band_headers(client):
    resp = client.get("/timeline")
    assert resp.status_code == 200
    assert 'class="strip-band' not in resp.text
    assert "has-bands" not in resp.text


def _seed_three_threads_and_a_solo(store):
    """Three lexically distinct 2-story thread pairs plus one unrelated
    story -- clusters (at sample_config's default threshold) into exactly
    three 2-story threads and one "Ungrouped signals" story, for exercising
    thread_max_rows without a large synthetic corpus."""
    _seed_recent(
        store,
        "th-1",
        "CMS finalizes Star Ratings methodology for Medicare Advantage",
        category="policy_regulatory",
        entities=["CMS"],
    )
    _seed_recent(
        store,
        "th-2",
        "CMS Star Ratings methodology update for Medicare Advantage plans",
        category="policy_regulatory",
        entities=["CMS"],
    )
    _seed_recent(
        store,
        "th-3",
        "Humana warns of rising medical loss ratio and margin pressure",
        category="financial_pressure",
        entities=["Humana"],
    )
    _seed_recent(
        store,
        "th-4",
        "Humana flags rising medical loss ratio squeezing margin",
        category="financial_pressure",
        entities=["Humana"],
    )
    _seed_recent(
        store,
        "th-5",
        "Aetna faces backlash over prior authorization denial rates for "
        "Medicare Advantage",
        category="policy_regulatory",
        entities=["Aetna"],
    )
    _seed_recent(
        store,
        "th-6",
        "Aetna criticized for prior authorization denial rates in Medicare "
        "Advantage plans",
        category="policy_regulatory",
        entities=["Aetna"],
    )
    _seed_recent(
        store,
        "solo",
        "Molina expands footprint into three additional states",
        category="membership_movement",
        entities=["Molina"],
    )


def test_timeline_threads_lane_caps_rows_and_folds_smaller_threads(
    sample_config, temp_db
):
    # Three 2-story threads + one ungrouped story, capped at 2 rows: the
    # chart must render exactly cap (2) + smaller-threads (1) + ungrouped
    # (1) = 4 rows, never the full uncapped 4 (3 threads + ungrouped).
    _seed_three_threads_and_a_solo(temp_db)
    cfg = dataclasses.replace(sample_config, thread_max_rows=2)
    client = TestClient(create_app(cfg, temp_db))
    resp = client.get("/timeline/threads")
    assert resp.status_code == 200
    text = resp.text

    assert text.count('class="strip-label') == 4  # cap + smaller-threads + ungrouped
    assert "+1 smaller threads" in text
    assert "Ungrouped signals" in text

    # The aggregate row is a plain label, never a link -- it names no single
    # thread's page.
    assert re.search(r"<span>\+1 smaller threads</span>", text)
    assert not re.search(r"<a[^>]*>\+1 smaller threads</a>", text)

    # Both aggregate rows get the muted modifier on both their label and plot
    # cells (2 rows x 2 cells each); the geometry classes (.strip-label,
    # .strip-plot) are untouched, only the extra modifier is added.
    assert text.count("strip-row-muted") == 4


def test_timeline_threads_lane_no_smaller_threads_row_under_cap(sample_config, temp_db):
    # Same fixture, default cap (25) -- well above 3 threads, so nothing
    # folds and the aggregate row never appears at all.
    _seed_three_threads_and_a_solo(temp_db)
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/timeline/threads")
    assert resp.status_code == 200
    assert "smaller threads" not in resp.text
    assert resp.text.count('class="strip-label') == 4  # 3 threads + ungrouped
    # The muted modifier still applies to the (always-present-here) ungrouped
    # row's label+plot cells -- just not to any "+N smaller threads" row,
    # since none exists under the cap.
    assert resp.text.count("strip-row-muted") == 2


def test_timeline_threads_lane_leads_to_chip_never_points_at_a_folded_thread(
    sample_config, temp_db
):
    # A causal cascade (policy -> financial, both mentioning Humana) plus
    # enough extra threads to push the cap below the total, with the linked
    # pair deliberately the SMALLEST two threads so they're the ones folded.
    _seed_dated(
        temp_db,
        "L1",
        "CMS finalizes Star Ratings methodology for Medicare Advantage",
        category="policy_regulatory",
        entities=["Humana"],
        days_ago=10,
    )
    _seed_dated(
        temp_db,
        "L2",
        "CMS Star Ratings methodology update for Medicare Advantage plans",
        category="policy_regulatory",
        entities=["Humana"],
        days_ago=9,
    )
    _seed_dated(
        temp_db,
        "L3",
        "Humana warns of rising medical loss ratio and margin pressure",
        category="financial_pressure",
        entities=["Humana"],
        days_ago=2,
    )
    _seed_dated(
        temp_db,
        "L4",
        "Humana flags rising medical loss ratio squeezing margin",
        category="financial_pressure",
        entities=["Humana"],
        days_ago=1,
    )
    # A bigger, unrelated thread (3 stories) that must outrank the 2-story
    # linked pair on size and so gets kept while the linked pair folds.
    _seed_recent(
        temp_db,
        "big-1",
        "Aetna faces backlash over prior authorization denial rates for "
        "Medicare Advantage plans nationwide",
        category="policy_regulatory",
        entities=["Aetna"],
    )
    _seed_recent(
        temp_db,
        "big-2",
        "Aetna criticized for prior authorization denial rates in Medicare "
        "Advantage plans nationwide",
        category="policy_regulatory",
        entities=["Aetna"],
    )
    _seed_recent(
        temp_db,
        "big-3",
        "Aetna under fire over prior authorization denial rates across "
        "Medicare Advantage plans nationwide",
        category="policy_regulatory",
        entities=["Aetna"],
    )
    cfg = dataclasses.replace(sample_config, thread_max_rows=1)
    client = TestClient(create_app(cfg, temp_db))
    resp = client.get("/timeline/threads")
    assert resp.status_code == 200
    text = resp.text
    assert "+2 smaller threads" in text
    # No "leads to" chip at all: both ends of the only declared link folded.
    assert 'class="strip-link"' not in text

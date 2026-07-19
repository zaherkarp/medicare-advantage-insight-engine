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
    # Caption names the canonical payer group and links to its page.
    assert 'href="/payers/unitedhealthcare"' in resp.text
    assert "last 30 days" in resp.text


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
    assert 'href="/topics/policy_regulatory"' in resp.text
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

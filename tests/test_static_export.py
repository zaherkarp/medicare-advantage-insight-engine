"""Tests for the static GitHub Pages export."""

import json
from datetime import datetime, timedelta

from ma_signal_monitor.digest import generate_digest
from ma_signal_monitor.models import NormalizedItem, ScoredItem
from ma_signal_monitor.payers import PAYER_GROUPS
from ma_signal_monitor.static_export import _map_path, build_site


def _seed(store, item_id, title, *, category, states=None):
    item = NormalizedItem(
        item_id=item_id,
        source_name="Healthcare Dive",
        source_type="rss",
        source_priority=3,
        source_tags=["industry"],
        title=title,
        link=f"https://example.com/{item_id}",
        published_date=datetime(2024, 1, 1, 12, 0),
        summary=f"{title} summary.",
    )
    store.upsert_story(
        ScoredItem(item=item, relevance_score=0.7, matched_categories=[category]),
        primary_category=category,
        states=states or [],
    )


def _seed_n(store, n, *, category="policy_regulatory", states=None, prefix="s"):
    """Seed ``n`` stories with descending timestamps (stable ordering)."""
    base = datetime(2024, 1, 1, 12, 0)
    for i in range(n):
        item = NormalizedItem(
            item_id=f"{prefix}{i}",
            source_name="Healthcare Dive",
            source_type="rss",
            source_priority=3,
            source_tags=["industry"],
            title=f"Signal {prefix}{i}",
            link=f"https://example.com/{prefix}{i}",
            published_date=base - timedelta(minutes=i),
            summary=f"Signal {prefix}{i} summary.",
        )
        store.upsert_story(
            ScoredItem(item=item, relevance_score=0.7, matched_categories=[category]),
            primary_category=category,
            states=states or [],
        )


def _build(tmp_path, sample_config, store, base="/myrepo"):
    out = tmp_path / "site"
    counts = build_site(store, sample_config, out, base_path=base)
    return out, counts


def test_build_creates_expected_pages(tmp_path, sample_config, temp_db):
    _seed(temp_db, "a1", "CMS Star Ratings rule", category="policy_regulatory")
    _seed(
        temp_db,
        "b2",
        "UHC enrollment in Texas",
        category="membership_movement",
        states=["TX"],
    )
    generate_digest(sample_config, temp_db, now=datetime(2024, 1, 1, 12, 0), send=False)

    out, counts = _build(tmp_path, sample_config, temp_db)

    assert (out / "index.html").exists()
    assert (out / "sources.html").exists()
    assert (out / "states.html").exists()
    assert (out / "status.html").exists()
    assert (out / "briefing.html").exists()
    assert (out / "search.html").exists()
    assert (out / "search-index.json").exists()
    assert (out / "static" / "style.css").exists()
    assert (out / ".nojekyll").exists()
    assert (out / "story" / "a1.html").exists()
    assert (out / "story" / "b2.html").exists()
    assert (out / "topics" / "policy_regulatory.html").exists()
    assert (out / "states" / "TX.html").exists()
    assert (out / "briefing" / "2024-01-01.html").exists()
    assert counts["stories"] == 2


def test_export_omits_sub_floor_stories(tmp_path, sample_config, temp_db):
    """Sub-floor noise isn't rendered as a page, counted, or in the search index."""
    _seed(temp_db, "keep", "CMS Star Ratings rule", category="policy_regulatory")
    # A pure source-priority "noise" item (matched nothing): kept in the DB but
    # never published.
    noise = NormalizedItem(
        item_id="noise",
        source_name="Virginia Mercury",
        source_type="rss",
        source_priority=2,
        source_tags=["state"],
        title="Man enters plea agreement in threat case",
        link="https://example.com/noise",
        published_date=datetime(2024, 1, 1, 12, 0),
        summary="Unrelated local news.",
    )
    temp_db.upsert_story(
        ScoredItem(item=noise, relevance_score=0.04, matched_categories=[]),
        primary_category="uncategorized",
        states=["VA"],
    )

    out, counts = _build(tmp_path, sample_config, temp_db)

    assert (out / "story" / "keep.html").exists()
    assert not (out / "story" / "noise.html").exists()  # noise page not generated
    assert counts["stories"] == 1
    # No state page for a state that only had noise.
    assert not (out / "states" / "VA.html").exists()
    # And the client-side search index excludes it.
    data = json.loads((out / "search-index.json").read_text())
    assert {e["url"] for e in data} == {"/myrepo/story/keep.html"}


def test_links_are_rewritten_with_base_path(tmp_path, sample_config, temp_db):
    _seed(temp_db, "a1", "CMS Star Ratings rule", category="policy_regulatory")
    out, _ = _build(tmp_path, sample_config, temp_db, base="/myrepo")

    index = (out / "index.html").read_text()
    # Internal links and assets are base-prefixed and point at .html files.
    assert "/myrepo/story/a1.html" in index
    assert "/myrepo/static/style.css" in index
    # No bare server-style root links remain.
    assert 'href="/story/' not in index
    assert 'href="/static/' not in index
    # External source links are untouched.
    story = (out / "story" / "a1.html").read_text()
    assert "https://example.com/a1" in story


def test_search_index_contents(tmp_path, sample_config, temp_db):
    _seed(temp_db, "a1", "CMS Star Ratings rule", category="policy_regulatory")
    out, _ = _build(tmp_path, sample_config, temp_db, base="/myrepo")

    data = json.loads((out / "search-index.json").read_text())
    assert len(data) == 1
    entry = data[0]
    assert entry["title"] == "CMS Star Ratings rule"
    assert entry["url"] == "/myrepo/story/a1.html"
    assert entry["category"] == "Policy / Regulatory Changes"
    # The client-side search page references its index.
    assert "search-index.json" in (out / "search.html").read_text()


def test_root_base_path(tmp_path, sample_config, temp_db):
    _seed(temp_db, "a1", "A story", category="policy_regulatory")
    out, _ = _build(tmp_path, sample_config, temp_db, base="")
    index = (out / "index.html").read_text()
    assert "/story/a1.html" in index
    assert "/static/style.css" in index


def test_static_story_omits_post_widget(tmp_path, sample_config, temp_db):
    _seed(temp_db, "a1", "A story", category="policy_regulatory")
    out, _ = _build(tmp_path, sample_config, temp_db, base="")
    story = (out / "story" / "a1.html").read_text()
    # Static export drops the interactive POST widget (no server to post to).
    assert "fetch('/feedback'" not in story
    # The explainer page is exported too.
    assert (out / "about-feedback.html").exists()


def test_static_feed_omits_card_rating(tmp_path, sample_config, temp_db):
    _seed(temp_db, "a1", "A story", category="policy_regulatory")
    out, _ = _build(tmp_path, sample_config, temp_db, base="")
    index = (out / "index.html").read_text()
    # No dead POST controls on the static feed.
    assert "card-feedback" not in index
    assert '"/feedback"' not in index


def test_static_story_mounts_giscus_when_configured(tmp_path, sample_config, temp_db):
    _seed(temp_db, "a1", "A story", category="policy_regulatory")
    sample_config.giscus_repo = "owner/repo"
    sample_config.giscus_repo_id = "R_kgABC"
    sample_config.giscus_category_id = "DIC_kwABC"
    out, _ = _build(tmp_path, sample_config, temp_db, base="")
    story = (out / "story" / "a1.html").read_text()
    # giscus binds to the story's stable item_id via mapping: specific.
    assert "giscus.app/client.js" in story
    assert 'data-mapping="specific"' in story
    assert 'data-term="a1"' in story
    assert 'data-repo="owner/repo"' in story


def test_map_path_pagination():
    # Page 1 (or no page) keeps the canonical filename.
    assert _map_path("/", "/r") == "/r/index.html"
    assert _map_path("/?page=1", "/r") == "/r/index.html"
    # Pages 2+ get a -<page> suffix that matches the generated files.
    assert _map_path("/?page=2", "/r") == "/r/index-2.html"
    assert _map_path("/topics/x?page=3", "/r") == "/r/topics/x-3.html"
    assert _map_path("/states/TX?page=2", "") == "/states/TX-2.html"
    assert _map_path("/candidates?page=2", "/r") == "/r/candidates-2.html"
    # Non-paginated routes are untouched by the page suffix.
    assert _map_path("/static/style.css", "/r") == "/r/static/style.css"
    assert _map_path("/story/a1", "/r") == "/r/story/a1.html"
    # Angles is a single page; any ?days= preset collapses to it.
    assert _map_path("/angles", "/r") == "/r/angles.html"
    assert _map_path("/angles?days=14", "/r") == "/r/angles.html"
    # The legacy /post-ideas alias maps to the same static Angles file (a
    # separate meta-refresh stub catches direct hits on post-ideas.html).
    assert _map_path("/post-ideas", "/r") == "/r/angles.html"
    assert _map_path("/post-ideas?days=14", "/r") == "/r/angles.html"


def test_angles_exported(tmp_path, sample_config, temp_db):
    _seed(temp_db, "a1", "CMS Star Ratings rule", category="policy_regulatory")
    out, _ = _build(tmp_path, sample_config, temp_db)

    page = (out / "angles.html").read_text()
    assert "Angles" in page
    # The live-only period picker is dropped from the static page.
    assert "?days=" not in page
    # Every page's nav link to it is rewritten to the static Angles file.
    index = (out / "index.html").read_text()
    assert "/myrepo/angles.html" in index
    # No bare server-style route link to the old or new path survives.
    assert 'href="/angles"' not in index
    assert 'href="/post-ideas"' not in index


def test_legacy_post_ideas_redirect_stub(tmp_path, sample_config, temp_db):
    """The renamed page leaves a meta-refresh stub at the old /post-ideas URL so
    static-host bookmarks bounce to angles.html (no server to issue a 301)."""
    _seed(temp_db, "a1", "CMS Star Ratings rule", category="policy_regulatory")
    out, _ = _build(tmp_path, sample_config, temp_db)

    stub = (out / "post-ideas.html").read_text()
    assert 'http-equiv="refresh"' in stub
    assert "url=/myrepo/angles.html" in stub
    # A canonical link and a plain fallback anchor for clients that don't refresh.
    assert 'rel="canonical" href="/myrepo/angles.html"' in stub
    assert 'href="/myrepo/angles.html"' in stub
    # It's a self-contained stub, not a rendered Angles page.
    assert "Causal chains in motion" not in stub


def test_angles_causal_model_survives_export(tmp_path, sample_config, temp_db):
    """A causal-chain card plus the declared-model explainer render into flat
    HTML. The About-this-model panel is JS-free (a <details>), so it survives the
    static export intact."""
    # Two recent stories that each match both policy and financial → the
    # policy→financial edge forms a causal-chain card in the current window.
    recent = datetime.now() - timedelta(days=1)
    for i in range(2):
        item = NormalizedItem(
            item_id=f"chain{i}",
            source_name="Healthcare Dive",
            source_type="rss",
            source_priority=3,
            source_tags=["industry"],
            title=f"CMS rate notice squeezes Medicare Advantage margins {i}",
            link=f"https://example.com/chain{i}",
            published_date=recent - timedelta(minutes=i),
            summary="CMS rate notice pressures Medicare Advantage plan margins.",
        )
        temp_db.upsert_story(
            ScoredItem(
                item=item,
                relevance_score=0.7,
                matched_categories=["policy_regulatory", "financial_pressure"],
            ),
            primary_category="policy_regulatory",
            states=[],
        )

    out, _ = _build(tmp_path, sample_config, temp_db)

    page = (out / "angles.html").read_text()
    # The causal partition heading and a chain card's model-evidence line.
    assert "Causal chains in motion" in page
    assert "Model evidence" in page
    # The declared-model explainer survives export as a JS-free <details> panel.
    assert "<details" in page


def test_static_search_page_has_streamlined_nav(tmp_path, sample_config, temp_db):
    """Pins the hand-maintained _SEARCH_HTML nav to the streamlined layout."""
    _seed(temp_db, "a1", "A story", category="policy_regulatory")
    out, _ = _build(tmp_path, sample_config, temp_db)
    search = (out / "search.html").read_text()
    for tail in (
        "index.html",
        "briefing.html",
        "angles.html",
        "sources.html",
        "candidates.html",
        "status.html",
    ):
        assert f"/myrepo/{tail}" in search
    assert "System ▾" in search
    # The System ▾ trigger stays keyboard-focusable on the static search page.
    assert 'class="nav-label" tabindex="0"' in search


def test_static_angles_empty_state_omits_period_advice(
    tmp_path, sample_config, temp_db
):
    """The static export has no period picker, so its empty state can't tell
    readers to 'widen the period' when the window is empty."""
    # A 2024-dated story archives (so the page builds) but falls outside the
    # default recent window, leaving the Angles page in its empty state.
    _seed(temp_db, "old", "Aged signal", category="policy_regulatory")
    out, _ = _build(tmp_path, sample_config, temp_db)
    page = (out / "angles.html").read_text()
    normalized = " ".join(page.split())
    assert "No signals in this period yet." in normalized
    assert "Widen the period" not in normalized


def test_feed_paginates_into_numbered_files(tmp_path, sample_config, temp_db):
    sample_config.web_page_size = 2
    _seed_n(temp_db, 5)

    out, _ = _build(tmp_path, sample_config, temp_db, base="/myrepo")

    # 5 stories / 2 per page = 3 pages.
    assert (out / "index.html").exists()
    assert (out / "index-2.html").exists()
    assert (out / "index-3.html").exists()
    assert not (out / "index-4.html").exists()

    index = (out / "index.html").read_text()
    assert "Page 1 of 3" in index
    # Page 1's pager points forward to page 2's static file.
    assert "/myrepo/index-2.html" in index
    # No raw server-style pager links survive the rewrite.
    assert 'href="/?page=' not in index

    page2 = (out / "index-2.html").read_text()
    # Page 2 links back to page 1 (canonical index.html) and on to page 3.
    assert "/myrepo/index.html" in page2
    assert "/myrepo/index-3.html" in page2
    assert "Page 2 of 3" in page2


def test_feed_single_page_omits_pager(tmp_path, sample_config, temp_db):
    sample_config.web_page_size = 25
    _seed_n(temp_db, 3)

    out, _ = _build(tmp_path, sample_config, temp_db, base="/myrepo")

    assert (out / "index.html").exists()
    assert not (out / "index-2.html").exists()
    # The pager nav only renders when there's more than one page.
    assert '<nav class="pagination">' not in (out / "index.html").read_text()


def test_topic_and_state_feeds_paginate(tmp_path, sample_config, temp_db):
    sample_config.web_page_size = 2
    _seed_n(temp_db, 3, category="policy_regulatory", states=["TX"], prefix="t")

    out, _ = _build(tmp_path, sample_config, temp_db, base="/myrepo")

    # 3 stories in the topic and state → 2 pages each.
    assert (out / "topics" / "policy_regulatory.html").exists()
    assert (out / "topics" / "policy_regulatory-2.html").exists()
    assert (out / "states" / "TX.html").exists()
    assert (out / "states" / "TX-2.html").exists()

    topic = (out / "topics" / "policy_regulatory.html").read_text()
    assert "/myrepo/topics/policy_regulatory-2.html" in topic
    state = (out / "states" / "TX.html").read_text()
    assert "/myrepo/states/TX-2.html" in state


def test_payer_pages_exported(tmp_path, sample_config, temp_db):
    item = NormalizedItem(
        item_id="p1",
        source_name="Healthcare Dive",
        source_type="rss",
        source_priority=3,
        source_tags=["industry"],
        title="Humana flags Medicare Advantage margin pressure",
        link="https://example.com/p1",
        published_date=datetime(2024, 1, 1, 12, 0),
        summary="Humana margin summary.",
    )
    temp_db.upsert_story(
        ScoredItem(
            item=item,
            relevance_score=0.7,
            matched_categories=["financial_pressure"],
            matched_entities=["Humana"],
        ),
        primary_category="financial_pressure",
        states=[],
    )

    out, counts = _build(tmp_path, sample_config, temp_db, base="/myrepo")

    assert (out / "payers.html").exists()
    assert (out / "payers" / "humana.html").exists()
    detail = (out / "payers" / "humana.html").read_text()
    assert "margin pressure" in detail
    overview = (out / "payers.html").read_text()
    assert "/myrepo/payers/humana.html" in overview
    assert counts["payers"] == len(PAYER_GROUPS)
    # Route-to-file mapping covers payer paths.
    assert _map_path("/payers", "/myrepo") == "/myrepo/payers.html"
    assert (
        _map_path("/payers/humana?page=2", "/myrepo") == "/myrepo/payers/humana-2.html"
    )


def test_status_sparkline_survives_export(tmp_path, sample_config, temp_db):
    """The inline-SVG trend renders to static HTML (no JS, no rewritten attrs)."""
    _seed(temp_db, "a1", "CMS Star Ratings rule", category="policy_regulatory")

    out, _ = _build(tmp_path, sample_config, temp_db, base="/myrepo")

    status = (out / "status.html").read_text()
    assert "Signal volume" in status
    assert "<polyline" in status and 'class="spark-line"' in status
    # The polyline geometry uses `points`, not href/src/action, so the export
    # link-rewriter leaves it untouched (no /myrepo prefix injected into it).
    assert "points=" in status


def test_export_hides_duplicate_stories(tmp_path, sample_config, temp_db):
    """The static feed shows the representative; the duplicate is not a feed row."""
    _seed(
        temp_db, "rep", "UnitedHealth insulin settlement", category="policy_regulatory"
    )
    # A duplicate of the representative (different source), marked via upsert.
    dup_item = NormalizedItem(
        item_id="dup",
        source_name="Beckers",
        source_type="rss",
        source_priority=3,
        source_tags=[],
        title="UnitedHealth insulin settlement reached",
        link="https://example.com/dup",
        published_date=datetime(2024, 1, 1, 12, 0),
        summary="dup summary.",
    )
    temp_db.upsert_story(
        ScoredItem(
            item=dup_item, relevance_score=0.7, matched_categories=["policy_regulatory"]
        ),
        primary_category="policy_regulatory",
        duplicate_of="rep",
    )

    out, counts = _build(tmp_path, sample_config, temp_db, base="/myrepo")

    index = (out / "index.html").read_text()
    assert "Healthcare Dive" in index  # representative in the feed
    assert "Beckers" not in index  # duplicate hidden from the feed
    assert counts["stories"] == 1  # only representatives are page-rendered as feed
    # The representative's own page lists the duplicate.
    rep_page = (out / "story" / "rep.html").read_text()
    assert "Also reported by" in rep_page and "Beckers" in rep_page


def test_feed_timelines_survive_export(tmp_path, sample_config, temp_db):
    """Per-card coverage timelines render into static HTML; the picker does not."""
    item = NormalizedItem(
        item_id="uhc-recent",
        source_name="Healthcare Dive",
        source_type="rss",
        source_priority=3,
        source_tags=["industry"],
        title="UnitedHealthcare expands service area",
        link="https://example.com/uhc-recent",
        published_date=datetime.utcnow(),  # inside the default window
        summary="Recent UHC signal.",
    )
    temp_db.upsert_story(
        ScoredItem(
            item=item,
            relevance_score=0.7,
            matched_categories=["membership_movement"],
            matched_entities=["UnitedHealthcare"],
        ),
        primary_category="membership_movement",
    )

    out, _ = _build(tmp_path, sample_config, temp_db, base="/myrepo")

    index = (out / "index.html").read_text()
    # The inline-SVG timeline (geometry only) survives the export untouched.
    assert "card-timeline" in index and 'class="spark-line"' in index
    # The reader control needs a live server, so it's frozen out of the export.
    assert "period-picker" not in index
    assert "?days=" not in index
    # The caption's payer link is rewritten to the static payer page.
    assert "payers/unitedhealthcare.html" in index

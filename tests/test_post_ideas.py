"""Tests for the Post Ideas (LinkedIn post topics) view-model builder."""

from ma_signal_monitor.post_ideas import build_post_ideas


def _story(
    item_id,
    category,
    *,
    score=0.5,
    entities=None,
    states=None,
    draft=None,
    title=None,
):
    """A minimal ``_story_view``-shaped dict."""
    return {
        "item_id": item_id,
        "title": title or f"Story {item_id}",
        "link": f"https://example.com/{item_id}",
        "source_name": "Test Feed",
        "summary": "",
        "display_date": "2026-07-10 12:00",
        "relevance_score": score,
        "primary_category": category,
        "categories": [category] if category else [],
        "entities": entities or [],
        "states": states or [],
        "public_draft": draft,
    }


def test_themes_ranked_by_volume_then_score(sample_config):
    current = [
        _story("a", "policy_regulatory", score=0.9),
        _story("b", "financial_pressure", score=0.4),
        _story("c", "financial_pressure", score=0.6),
        _story("d", "membership_movement", score=0.95),
    ]
    ideas = build_post_ideas(current, [], sample_config)
    keys = [t["key"] for t in ideas["themes"]]
    # financial_pressure leads on volume; the singles rank by top score.
    assert keys == ["financial_pressure", "membership_movement", "policy_regulatory"]


def test_momentum_labels(sample_config):
    current = [
        _story("a", "policy_regulatory"),
        _story("b", "policy_regulatory"),
        _story("c", "financial_pressure"),
        _story("d", "membership_movement"),
        _story("e", "competitive_strategy"),
    ]
    previous = [
        _story("p1", "policy_regulatory"),
        _story("p2", "financial_pressure"),
        _story("p3", "financial_pressure"),
        _story("p4", "competitive_strategy"),
    ]
    ideas = build_post_ideas(current, previous, sample_config)
    momentum = {t["key"]: t["momentum"] for t in ideas["themes"]}
    assert momentum == {
        "policy_regulatory": "up",
        "financial_pressure": "down",
        "membership_movement": "new",
        "competitive_strategy": "steady",
    }


def test_hook_and_hashtags_from_strongest_draft(sample_config):
    current = [
        _story(
            "weak",
            "policy_regulatory",
            score=0.4,
            draft={"opening_hook": "Weak hook", "suggested_hashtags": ["#Weak"]},
        ),
        _story(
            "strong",
            "policy_regulatory",
            score=0.8,
            draft={"opening_hook": "Strong hook", "suggested_hashtags": ["#Strong"]},
        ),
        _story("undrafted", "policy_regulatory", score=0.9),
    ]
    theme = build_post_ideas(current, [], sample_config)["themes"][0]
    # The highest-scoring *drafted* story supplies the hook and hashtags,
    # even when an undrafted story outranks it.
    assert theme["hook"] == "Strong hook"
    assert theme["hashtags"] == ["#Strong"]


def test_hook_and_hashtag_fallbacks_without_drafts(sample_config):
    """Most archived stories carry no draft — the fallbacks are the norm."""
    current = [
        _story("a", "policy_regulatory", score=0.6, title="CMS drops a big rule"),
        _story("b", "policy_regulatory", score=0.2),
    ]
    theme = build_post_ideas(current, [], sample_config)["themes"][0]
    assert "CMS drops a big rule" in theme["hook"]
    assert "2 signals" in theme["hook"]
    assert theme["hashtags"] == ["#MedicareAdvantage"]


def test_uncategorized_never_forms_a_theme(sample_config):
    current = [
        _story("a", "uncategorized"),
        _story("b", None),
        _story("c", "policy_regulatory"),
    ]
    ideas = build_post_ideas(current, [], sample_config)
    assert [t["key"] for t in ideas["themes"]] == ["policy_regulatory"]
    # ...but uncategorized stories still count toward the window total.
    assert ideas["highlights"]["total"] == 3


def test_entity_fold_uses_canonical_groups_and_skips_unknown(sample_config):
    current = [
        _story("a", "policy_regulatory", entities=["UnitedHealth", "UHC", "CMS"]),
        _story("b", "policy_regulatory", entities=["Humana"], states=["TX"]),
    ]
    ideas = build_post_ideas(current, [], sample_config)
    payers = {p["slug"]: p for p in ideas["highlights"]["payers"]}
    # Two aliases of one org count once per story; CMS has no payer group.
    assert set(payers) == {"unitedhealthcare", "humana"}
    assert payers["unitedhealthcare"]["count"] == 1
    assert payers["unitedhealthcare"]["name"] == "UnitedHealthcare"
    assert ideas["highlights"]["states"] == [{"code": "TX", "count": 1}]


def test_theme_stories_capped_and_score_ordered(sample_config):
    current = [_story(f"s{i}", "policy_regulatory", score=0.1 * i) for i in range(1, 6)]
    theme = build_post_ideas(current, [], sample_config)["themes"][0]
    assert [s["item_id"] for s in theme["stories"]] == ["s5", "s4", "s3"]
    assert theme["count"] == 5


def test_authoritative_counts_override_truncated_lists(sample_config):
    """Counts/total/momentum follow the count maps, not the (possibly
    truncated) story lists — so a scan-limited window still reports true
    volumes."""
    current = [_story("a", "policy_regulatory"), _story("b", "financial_pressure")]
    previous = [_story("p", "policy_regulatory")]
    ideas = build_post_ideas(
        current,
        previous,
        sample_config,
        # The DB says far more stories exist than the sampled lists show.
        current_counts={"policy_regulatory": 600, "financial_pressure": 50},
        previous_counts={"policy_regulatory": 900, "financial_pressure": 0},
    )
    themes = {t["key"]: t for t in ideas["themes"]}
    assert themes["policy_regulatory"]["count"] == 600
    assert themes["policy_regulatory"]["prev_count"] == 900
    # True volume fell 900 -> 600 even though the sampled list grew: momentum
    # must read "down", which the len-based path could not have detected.
    assert themes["policy_regulatory"]["momentum"] == "down"
    assert themes["financial_pressure"]["momentum"] == "new"
    # The window total is the sum of the authoritative counts, not len(current).
    assert ideas["highlights"]["total"] == 650


def test_theme_link_flag_tracks_configured_categories(sample_config):
    """Configured categories link to /topics/{key}; a stale key does not."""
    current = [
        _story("a", "policy_regulatory"),
        _story("b", "star_ratings_legacy"),  # not in the config taxonomy
    ]
    themes = {
        t["key"]: t for t in build_post_ideas(current, [], sample_config)["themes"]
    }
    assert themes["policy_regulatory"]["linkable"] is True
    assert themes["star_ratings_legacy"]["linkable"] is False

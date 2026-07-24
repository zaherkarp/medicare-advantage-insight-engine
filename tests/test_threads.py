"""Tests for emergent story-thread clustering (threads.py)."""

from ma_signal_monitor.threads import _dominant_category, build_threads


def _story(item_id, title, *, category=None, entities=None, score=0.5, summary=None):
    """A minimal ``_story_view``-shaped dict for the thread builder."""
    return {
        "item_id": item_id,
        "title": title,
        "summary": summary if summary is not None else title,
        "source_name": "Test Feed",
        "relevance_score": score,
        "primary_category": category or "uncategorized",
        "categories": [category] if category else [],
        "entities": entities or [],
        "states": [],
    }


# Two near-identical Star Ratings stories (policy) form one thread.
_STAR_A = _story(
    "a",
    "CMS finalizes Star Ratings methodology for Medicare Advantage",
    category="policy_regulatory",
    entities=["CMS"],
)
_STAR_B = _story(
    "b",
    "CMS Star Ratings methodology update for Medicare Advantage plans",
    category="policy_regulatory",
    entities=["CMS"],
)
# Two medical-loss-ratio stories (financial) form another.
_MLR_A = _story(
    "c",
    "Humana warns of rising medical loss ratio and margin pressure",
    category="financial_pressure",
    entities=["Humana"],
)
_MLR_B = _story(
    "d",
    "Humana flags rising medical loss ratio squeezing margin",
    category="financial_pressure",
    entities=["Humana"],
)
# A lone, unrelated story.
_SOLO = _story(
    "e",
    "Aetna launches value-based care partnership network",
    category="competitive_strategy",
    entities=["Aetna"],
)


def _threads(config, stories, *, threshold=0.28, min_stories=2):
    return build_threads(stories, config, threshold=threshold, min_stories=min_stories)


def test_related_stories_cluster_unrelated_stay_apart(sample_config):
    threads, ungrouped = _threads(sample_config, [_STAR_A, _STAR_B, _SOLO])
    assert len(threads) == 1
    assert {s["item_id"] for s in threads[0].stories} == {"a", "b"}
    assert [s["item_id"] for s in ungrouped] == ["e"]


def test_singletons_below_min_stories_are_ungrouped(sample_config):
    threads, ungrouped = _threads(sample_config, [_STAR_A, _SOLO])
    # The lone star story (its partner absent) and the solo story both drop out.
    assert threads == []
    assert {s["item_id"] for s in ungrouped} == {"a", "e"}


def test_every_story_lands_exactly_once(sample_config):
    stories = [_STAR_A, _STAR_B, _MLR_A, _MLR_B, _SOLO]
    threads, ungrouped = _threads(sample_config, stories)
    seen = [s["item_id"] for t in threads for s in t.stories]
    seen += [s["item_id"] for s in ungrouped]
    assert sorted(seen) == ["a", "b", "c", "d", "e"]


def test_clustering_is_order_independent(sample_config):
    stories = [_STAR_A, _STAR_B, _MLR_A, _MLR_B, _SOLO]
    forward, _ = _threads(sample_config, stories)
    reverse, _ = _threads(sample_config, list(reversed(stories)))
    as_sets = {frozenset(s["item_id"] for s in t.stories) for t in forward}
    bs_sets = {frozenset(s["item_id"] for s in t.stories) for t in reverse}
    assert as_sets == bs_sets == {frozenset({"a", "b"}), frozenset({"c", "d"})}


def test_threads_ordered_along_causal_cascade(sample_config):
    # Input order puts the downstream thread first; output must still cascade.
    threads, _ = _threads(sample_config, [_MLR_A, _MLR_B, _STAR_A, _STAR_B])
    assert [t.layer_short for t in threads] == ["Drivers", "Pressure"]
    assert [t.layer_order for t in threads] == [1, 2]


def test_thread_placed_on_dominant_category_layer(sample_config):
    threads, _ = _threads(sample_config, [_STAR_A, _STAR_B])
    assert threads[0].dominant_category == "policy_regulatory"
    assert threads[0].layer_key == "drivers"
    assert threads[0].layer_label == "Structural & Policy Drivers"


def test_thread_labeled_from_distinctive_terms(sample_config):
    threads, _ = _threads(sample_config, [_STAR_A, _STAR_B, _MLR_A, _MLR_B])
    star = next(t for t in threads if t.dominant_category == "policy_regulatory")
    assert any(w in star.label.lower() for w in ("star", "rating", "methodolog", "cms"))


def test_label_falls_back_to_category_when_not_nameable(sample_config):
    # With no distinctive multi-doc vocabulary, the label is the dominant
    # taxonomy label rather than an empty or noisy string.
    threads, _ = _threads(sample_config, [_SOLO, _STAR_A], min_stories=1)
    solo = next(t for t in threads if t.dominant_category == "competitive_strategy")
    assert solo.label == "Competitive / Operational Strategy"


def test_dominant_category_is_most_common_real_category(sample_config):
    # 2 policy, 1 financial -> policy dominates; unlabeled stories don't count.
    stories = [_STAR_A, _STAR_B, _MLR_A]
    assert _dominant_category(stories, sample_config) == "policy_regulatory"


def test_dominant_category_empty_when_all_uncategorized(sample_config):
    plain = _story("x", "An unremarkable headline", category=None)
    assert _dominant_category([plain], sample_config) == ""


def test_empty_window_returns_no_threads(sample_config):
    assert build_threads([], sample_config, threshold=0.28, min_stories=2) == ([], [])

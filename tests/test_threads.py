"""Tests for emergent story-thread clustering (threads.py)."""

from ma_signal_monitor.threads import (
    _cluster,
    _dominant_category,
    _story_terms,
    build_threads,
)


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
# Same company, different watched-entity aliases from the same payer group
# ("UnitedHealthcare" and "UnitedHealth" both map to payers.PAYER_GROUPS'
# "unitedhealthcare" group). Titles deliberately share too little vocabulary
# to cluster on title tokens alone (title-only Jaccard 0.25, below the 0.28
# default threshold) -- clustering must come from the canonical entity token.
# Note: only "UnitedHealthcare" is in sample_config.watched_entities (see
# conftest.py); "UnitedHealth" isn't. That's fine here -- build_threads reads
# whatever is already on story["entities"], independent of the watch list
# that gated real detection, so this still exercises the fix directly.
_UHC_A = _story(
    "f",
    "UnitedHealthcare billing practices draw scrutiny",
    category="competitive_strategy",
    entities=["UnitedHealthcare"],
)
_UHC_B = _story(
    "g",
    "UnitedHealth billing practices anger providers",
    category="competitive_strategy",
    entities=["UnitedHealth"],
)


# NOTE: this default is on the IDF-weighted-cosine scale (similarity.py),
# unrelated to config/app.yaml's thread_similarity_threshold -- see that
# file's comment (set by scripts/calibrate_threads.py) for the production
# value. 0.1 is picked by hand against exactly the hand-built fixtures below:
# every "must merge" pair here scores >= 0.12 (the tightest is _UHC_A/_UHC_B,
# folded through canonical payer-group tokens rather than raw title overlap),
# and every "must stay apart" pair scores 0.0 (these fixtures share no
# vocabulary at all across categories/companies), so the exact value only
# needs to sit anywhere in (0.0, 0.12].
def _threads(config, stories, *, threshold=0.1, min_stories=2):
    return build_threads(stories, config, threshold=threshold, min_stories=min_stories)


def test_related_stories_cluster_unrelated_stay_apart(sample_config):
    threads, ungrouped = _threads(sample_config, [_STAR_A, _STAR_B, _SOLO])
    assert len(threads) == 1
    assert {s["item_id"] for s in threads[0].stories} == {"a", "b"}
    assert [s["item_id"] for s in ungrouped] == ["e"]


def test_story_terms_folds_aliases_to_one_group_token():
    # "UnitedHealthcare" and "UnitedHealth" are different watched-entity
    # aliases of the same payers.PAYER_GROUPS group -- _story_terms must fold
    # both to the same opaque "@<slug>" token rather than keeping them as two
    # distinct raw-string tokens (the fragmentation bug this step fixes).
    assert "@unitedhealthcare" in _story_terms(_UHC_A)
    assert "@unitedhealthcare" in _story_terms(_UHC_B)
    # An alias with no payer group (e.g. "CMS") still falls back to the
    # previous lowercased-string behavior.
    assert "cms" in _story_terms(_STAR_A)


def test_different_aliases_of_same_payer_group_cluster_together(sample_config):
    # Same fix, at the build_threads level: two stories about the same
    # company under different aliases share too little title vocabulary to
    # cluster on title tokens alone (title-only Jaccard 0.25, below the 0.28
    # default threshold) but must still land in one thread once the aliases
    # are folded to a shared canonical entity token.
    threads, ungrouped = _threads(sample_config, [_UHC_A, _UHC_B, _SOLO])
    assert len(threads) == 1
    assert {s["item_id"] for s in threads[0].stories} == {"f", "g"}
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


def test_average_linkage_merge_guard_blocks_chaining():
    # A and B share two window-rare terms; B and C share two different
    # window-rare terms; A and C share nothing. Single-linkage would chain
    # A -> B -> C into one cluster purely through the B bridge, even though
    # A and C have nothing in common -- the exact failure this step fixes
    # (see threads._cluster's docstring). Both A-B and B-C individually clear
    # the threshold on their own, but once A merges with B (or B with C),
    # the merged cluster's AVERAGE similarity to the third story drops below
    # threshold (each bridging pair's score is roughly halved once spread
    # across a 2-story cluster), so the merge guard blocks the second merge.
    a = {"alpha", "bravo", "common1", "common2"}
    b = {"common1", "common2", "delta", "echo"}
    c = {"delta", "echo", "foxtrot", "golf"}
    groups = _cluster([a, b, c], threshold=0.2)
    as_sets = {frozenset(g) for g in groups}
    # A and C must never land in the same group, however B ends up grouped.
    assert not any({0, 2} <= s for s in as_sets)
    # Sanity: this is a real anti-chaining case, not merge-guard-blocks-all --
    # exactly one of the two bridging pairs still merges.
    assert {0, 1} in as_sets or {1, 2} in as_sets


def test_threads_ordered_along_causal_cascade(sample_config):
    # Input order puts the downstream thread first; output must still cascade.
    threads, _ = _threads(sample_config, [_MLR_A, _MLR_B, _STAR_A, _STAR_B])
    assert [t.layer_short for t in threads] == ["Drivers", "Pressure"]
    assert [t.layer_order for t in threads] == [1, 2]


def test_thread_placed_on_dominant_category_layer(sample_config):
    # _SOLO is window context only (unrelated, stays ungrouped): with only
    # two documents in the whole window, IDF-weighted cosine would otherwise
    # be a degenerate case -- every term STAR_A/STAR_B share appears in 100%
    # of a 2-doc window, so its IDF collapses to log(2/2) == 0 and the pair's
    # similarity would score 0.0 regardless of how similar the headlines
    # read. A third, unrelated story keeps the shared terms' document
    # frequency below the window size, which is the realistic case IDF
    # assumes (see similarity.idf_weights).
    threads, _ = _threads(sample_config, [_STAR_A, _STAR_B, _SOLO])
    assert len(threads) == 1
    assert threads[0].dominant_category == "policy_regulatory"
    assert threads[0].layer_key == "drivers"
    assert threads[0].layer_label == "Structural & Policy Drivers"


def test_thread_labeled_from_distinctive_terms(sample_config):
    threads, _ = _threads(sample_config, [_STAR_A, _STAR_B, _MLR_A, _MLR_B])
    star = next(t for t in threads if t.dominant_category == "policy_regulatory")
    assert any(w in star.label.lower() for w in ("star", "rating", "methodolog", "cms"))


def test_thread_label_avoids_ubiquitous_window_phrase(sample_config):
    # Two near-duplicate stories share "market conditions" with three unrelated
    # filler stories elsewhere in the window (in-thread share 2/5 = 0.4, below
    # the 0.5 floor) -- so that phrase must not become the thread's label, even
    # though every headline in the thread contains it. The thread's own
    # "special needs plan expansion" wording appears nowhere else in the
    # window (share 1.0), so that's what should surface instead.
    target_a = _story(
        "t1",
        "Elevance unveils special needs plan expansion amid market conditions",
        category="competitive_strategy",
    )
    target_b = _story(
        "t2",
        "Elevance special needs plan expansion accelerates as market conditions shift",
        category="competitive_strategy",
    )
    filler_1 = _story(
        "f1",
        "Centene warns of margin pressure amid market conditions this quarter",
        category="financial_pressure",
    )
    filler_2 = _story(
        "f2",
        "Molina flags enrollment softness as market conditions weigh on growth",
        category="financial_pressure",
    )
    filler_3 = _story(
        "f3",
        "Kaiser broker commissions face scrutiny as market conditions evolve statewide",
        category="competitive_strategy",
    )
    threads, _ = _threads(
        sample_config, [target_a, target_b, filler_1, filler_2, filler_3]
    )
    target = next(
        t for t in threads if {"t1", "t2"} <= {s["item_id"] for s in t.stories}
    )
    assert "market conditions" not in target.label.lower()
    assert any(w in target.label.lower() for w in ("special needs", "plan expansion"))


def test_build_threads_gives_colliding_threads_distinct_labels(sample_config):
    # Two unrelated single-story clusters (min_stories=1) in the same category
    # with no shared vocabulary both fall back to the same dominant-category
    # label ("Competitive / Operational Strategy") when labeled independently
    # -- the exact collision the diagnosis found on the real page. build_threads
    # must still hand back distinct labels.
    solo_x = _story(
        "x",
        "UnitedHealthcare pilots new digital front door for member service today",
        category="competitive_strategy",
        entities=["UnitedHealthcare"],
    )
    solo_y = _story(
        "y",
        "Aetna trials revamped virtual concierge tool for policyholders statewide",
        category="competitive_strategy",
        entities=["Aetna"],
    )
    threads, _ = _threads(sample_config, [solo_x, solo_y], min_stories=1)
    assert len(threads) == 2
    labels = [t.label for t in threads]
    assert len(set(labels)) == 2
    assert all(
        label.startswith("Competitive / Operational Strategy") for label in labels
    )


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


def test_anchor_key_is_order_independent(sample_config):
    # _STAR_A/_STAR_B tie on relevance_score (both 0.5); the item_id tie-break
    # (threads.py's member ranking) makes the anchor -- and so the thread's
    # key -- fully determined by content, never by which order build_threads
    # happened to see the input list in (mirroring
    # test_clustering_is_order_independent, but for identity rather than
    # membership).
    forward, _ = _threads(sample_config, [_STAR_A, _STAR_B, _SOLO])
    reverse, _ = _threads(sample_config, [_SOLO, _STAR_B, _STAR_A])
    assert forward[0].key == reverse[0].key == "a"


def test_anchor_key_survives_new_low_relevance_story_joining(sample_config):
    # A third, much-lower-relevance near-duplicate joining the thread on a
    # later ingest cycle must not change its key -- threads aren't persisted,
    # so build_threads reclusters from scratch every request, and the whole
    # point of anchoring on the top member is that a URL keeps working across
    # that. Only a *higher*-relevance story joining, or the anchor aging out
    # of the window, should ever change it (see Thread.key's docstring).
    before, _ = _threads(sample_config, [_STAR_A, _STAR_B, _SOLO])
    star_c = _story(
        "h",
        "CMS finalizes Star Ratings methodology change for Medicare Advantage",
        category="policy_regulatory",
        entities=["CMS"],
        score=0.05,
    )
    after, _ = _threads(sample_config, [_STAR_A, _STAR_B, star_c, _SOLO])
    after_thread = next(
        t for t in after if {"a", "b"} <= {s["item_id"] for s in t.stories}
    )
    assert "h" in {s["item_id"] for s in after_thread.stories}
    assert before[0].key == after_thread.key == "a"


def test_mixed_category_thread_has_no_layer(sample_config):
    # Four near-duplicate headlines about the same event, split 50/50 across
    # two categories -- no category clears the >50% majority bar, so the
    # thread must be placed nowhere on the causal model rather than guessing
    # from a coin-flip tie-break (see threads._category_split). A pile of
    # unrelated filler keeps the shared vocabulary's document frequency below
    # the candidate-generation blocking cap (_DF_BLOCK_FRACTION) so the four
    # target stories still cluster together despite writing near-identically.
    mix_a1 = _story(
        "m1",
        "Prior authorization crackdown hits Medicare Advantage insurers nationwide",
        category="policy_regulatory",
    )
    mix_a2 = _story(
        "m2",
        "Prior authorization crackdown squeezes Medicare Advantage insurers nationwide",
        category="policy_regulatory",
    )
    mix_b1 = _story(
        "m3",
        "Prior authorization crackdown rattles Medicare Advantage insurers nationwide",
        category="financial_pressure",
    )
    mix_b2 = _story(
        "m4",
        "Prior authorization crackdown worries Medicare Advantage insurers nationwide",
        category="financial_pressure",
    )
    fillers = [
        _story(
            "f1",
            "Aetna launches value-based care partnership network for seniors",
            category="competitive_strategy",
        ),
        _story(
            "f2",
            "Humana announces new leadership team amid strategic overhaul",
            category="competitive_strategy",
        ),
        _story(
            "f3",
            "Centene reports quarterly earnings above analyst expectations",
            category="financial_pressure",
        ),
        _story(
            "f4",
            "Molina expands footprint into three additional states",
            category="membership_movement",
        ),
        _story(
            "f5",
            "Kaiser opens new telehealth clinics across rural regions",
            category="competitive_strategy",
        ),
        _story(
            "f6",
            "Elevance unveils digital front door for member engagement",
            category="competitive_strategy",
        ),
    ]
    threads, _ = _threads(sample_config, [mix_a1, mix_a2, mix_b1, mix_b2, *fillers])
    mixed_thread = next(
        t
        for t in threads
        if {"m1", "m2", "m3", "m4"} <= {s["item_id"] for s in t.stories}
    )
    assert mixed_thread.mixed is True
    assert mixed_thread.dominant_category == ""
    assert mixed_thread.layer_key == ""
    assert mixed_thread.layer_short == ""
    assert mixed_thread.layer_label == ""
    from ma_signal_monitor.threads import _NO_LAYER_ORDER

    assert mixed_thread.layer_order == _NO_LAYER_ORDER

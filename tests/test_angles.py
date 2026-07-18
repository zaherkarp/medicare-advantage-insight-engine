"""Tests for the Angles (lens-intersection) view-model builder."""

import dataclasses

from ma_signal_monitor.angles import (
    MAX_ANGLES,
    _bucket_pairs,
    _causal_model_view,
    _edge_map,
    _fact_line,
    _lookup_edge,
    _sequence_consistent,
    _story_lenses,
    _suppress_subsets,
    build_angles,
)
from ma_signal_monitor.config import CausalLayerConfig


def _story(
    item_id,
    *,
    categories=None,
    primary=None,
    score=0.5,
    entities=None,
    states=None,
    source="Test Feed",
    title=None,
):
    """A minimal facet-shaped dict (as ``_facet_view`` produces).

    ``primary`` defaults to the first category so a normal story always carries
    a primary_category (as real archived rows do); pass ``categories=[]`` with a
    ``primary`` to exercise the old-row topic fallback.
    """
    cats = list(categories) if categories is not None else []
    if primary is None and cats:
        primary = cats[0]
    return {
        "item_id": item_id,
        "title": title or f"Story {item_id}",
        "link": f"https://example.com/{item_id}",
        "source_name": source,
        "display_date": "2026-07-10 12:00",
        "relevance_score": score,
        "primary_category": primary,
        "categories": cats,
        "entities": entities or [],
        "states": states or [],
    }


def _find(view, card_type):
    """First card of ``card_type`` in the view, or None."""
    return next((c for c in view["angles"] if c["type"] == card_type), None)


# --- Lens extraction & bucketing ---


def test_story_lenses_split_topics_payers_states(sample_config):
    lenses = _story_lenses(
        _story(
            "a",
            categories=["policy_regulatory", "financial_pressure", "uncategorized"],
            entities=["Humana", "CMS"],
            states=["TX", "TX"],
        ),
        sample_config,
    )
    # uncategorized is not a topic; TX de-dupes; CMS has no payer group.
    assert lenses["topics"] == ["policy_regulatory", "financial_pressure"]
    assert lenses["payers"] == ["humana"]
    assert lenses["states"] == ["TX"]


def test_story_lenses_falls_back_to_primary_for_old_rows(sample_config):
    # An old row with no categories list still carries a topic via primary.
    lenses = _story_lenses(
        _story("old", categories=[], primary="membership_movement"), sample_config
    )
    assert lenses["topics"] == ["membership_movement"]


def test_payer_lens_folds_aliases(sample_config):
    lenses = _story_lenses(
        _story("a", entities=["UnitedHealth", "UHC", "Optum"]), sample_config
    )
    assert lenses["payers"] == ["unitedhealthcare"]  # three aliases, one group


def test_people_entities_excluded_from_payer_lens(sample_config):
    lenses = _story_lenses(
        _story("a", entities=["Mark Bertolini", "Humana"]), sample_config
    )
    assert lenses["payers"] == ["humana"]  # the named person is dropped
    view = build_angles(
        [
            _story("a", entities=["Mark Bertolini", "Humana"]),
            _story("b", entities=["Mark Bertolini", "Humana"]),
        ],
        [],
        sample_config,
    )
    assert not any(c["type"] == "payer_payer" for c in view["angles"])


def test_bucket_pairs_form_each_type(sample_config):
    buckets = _bucket_pairs(
        [
            _story(
                "s1",
                entities=["Humana", "UnitedHealthcare"],
                categories=["financial_pressure", "membership_movement"],
                states=["FL"],
            )
        ],
        sample_config,
    )
    keys = set(buckets)
    assert ("payer_topic", "humana", "financial_pressure") in keys
    assert ("payer_topic", "unitedhealthcare", "membership_movement") in keys
    assert ("topic_topic", "financial_pressure", "membership_movement") in keys
    assert ("topic_state", "financial_pressure", "FL") in keys
    assert ("payer_payer", "humana", "unitedhealthcare") in keys


def test_same_lens_pairs_are_canonicalized(sample_config):
    buckets = _bucket_pairs(
        [
            _story("s1", categories=["membership_movement", "financial_pressure"]),
            _story("s2", categories=["financial_pressure", "membership_movement"]),
        ],
        sample_config,
    )
    topic_keys = [k for k in buckets if k[0] == "topic_topic"]
    # Both orderings collapse into one canonical (sorted) key with both stories.
    assert topic_keys == [("topic_topic", "financial_pressure", "membership_movement")]
    assert len(buckets[topic_keys[0]]) == 2


def test_overlap_needs_two_stories(sample_config):
    one = build_angles(
        [_story("s1", entities=["Humana"], categories=["financial_pressure"])],
        [],
        sample_config,
    )
    assert not any(c["type"] == "payer_topic" for c in one["angles"])
    two = build_angles(
        [
            _story("s1", entities=["Humana"], categories=["financial_pressure"]),
            _story("s2", entities=["Humana"], categories=["financial_pressure"]),
        ],
        [],
        sample_config,
    )
    pt = _find(two, "payer_topic")
    assert pt is not None and pt["count"] == 2


# --- Ranking, momentum, suppression, cap ---


def test_rank_score_arithmetic(sample_config):
    # Plain payer × topic: boost 0, rank == count.
    plain = build_angles(
        [
            _story(f"p{i}", entities=["Humana"], categories=["membership_movement"])
            for i in range(6)
        ],
        [],
        sample_config,
    )
    assert _find(plain, "payer_topic")["rank_score"] == 6.0

    # Causal chain policy → financial (edge weight 1.0), 2 stories: 2 × 1.5.
    chain = build_angles(
        [
            _story("c1", categories=["policy_regulatory", "financial_pressure"]),
            _story("c2", categories=["policy_regulatory", "financial_pressure"]),
        ],
        [],
        sample_config,
    )
    ch = _find(chain, "causal_chain")
    assert ch["rank_score"] == 3.0
    assert ch["causal"]["weight"] == 1.0

    # Weaker edge policy → competitive (0.7), 2 stories: 2 × 1.35.
    weak = build_angles(
        [
            _story("w1", categories=["policy_regulatory", "competitive_strategy"]),
            _story("w2", categories=["policy_regulatory", "competitive_strategy"]),
        ],
        [],
        sample_config,
    )
    assert _find(weak, "causal_chain")["rank_score"] == 2.7  # w=1.0 (3.0) outranks it


def test_global_ranking_order(sample_config):
    view = build_angles(
        [
            # Humana cascade policy → financial (3 stories → 5.25).
            _story(
                "h1", entities=["Humana"], categories=["policy_regulatory"], score=0.9
            ),
            _story(
                "h2", entities=["Humana"], categories=["policy_regulatory"], score=0.85
            ),
            _story(
                "h3", entities=["Humana"], categories=["financial_pressure"], score=0.8
            ),
            # A plain topic × state overlap (2 stories → 2.0).
            _story("s1", categories=["membership_movement"], states=["TX"], score=0.6),
            _story("s2", categories=["membership_movement"], states=["TX"], score=0.5),
        ],
        [],
        sample_config,
    )
    assert view["angles"][0]["type"] == "payer_cascade"  # rank 5.25 leads


def test_sort_momentum_tiebreak(sample_config):
    current = [
        _story("s1", categories=["financial_pressure"], states=["FL"], score=0.9),
        _story("s2", categories=["financial_pressure"], states=["FL"], score=0.8),
        _story("s3", categories=["financial_pressure"], states=["FL"], score=0.7),
        _story("m1", categories=["membership_movement"], states=["TX"], score=0.9),
        _story("m2", categories=["membership_movement"], states=["TX"], score=0.8),
        _story("m3", categories=["membership_movement"], states=["TX"], score=0.7),
    ]
    previous = [_story("p1", categories=["financial_pressure"], states=["FL"])]
    view = build_angles(current, previous, sample_config)
    state_cards = [c for c in view["angles"] if c["type"] == "topic_state"]
    # Equal rank (3.0 each); the "new" overlap sorts above the "up" one.
    assert state_cards[0]["momentum"] == "new"
    assert state_cards[1]["momentum"] == "up"


def test_suppress_subsets_greedy():
    def card(label, ids, rank):
        return {"label": label, "rank_score": rank, "item_ids": frozenset(ids)}

    cards = [
        card("A", {1, 2, 3}, 3.0),  # accepted
        card("F", {1, 2, 3}, 2.5),  # identical set → dropped
        card("B", {1, 2}, 2.0),  # strict subset of A → dropped
        card("C", {2, 3}, 2.0),  # subset of A, dropped — and can't suppress D
        card("D", {4, 5}, 2.0),  # disjoint → accepted
        card("E", {4}, 1.0),  # subset of D → dropped
    ]
    assert [c["label"] for c in _suppress_subsets(cards)] == ["A", "D"]


def test_intersections_capped_at_max(sample_config):
    payers = [
        "Humana",
        "Cigna",
        "Molina",
        "Kaiser",
        "SCAN",
        "Centene",
        "Aetna",
        "Elevance",
        "CareSource",
        "Oscar Health",
    ]
    stories = []
    for i, p in enumerate(payers):
        stories.append(
            _story(f"{i}a", entities=[p], categories=["membership_movement"])
        )
        stories.append(
            _story(f"{i}b", entities=[p], categories=["membership_movement"])
        )
    view = build_angles(stories, [], sample_config)
    # 10 disjoint payer × topic overlaps form; the list is capped at MAX_ANGLES.
    assert len([c for c in view["angles"] if c["type"] == "payer_topic"]) == MAX_ANGLES


# --- Causal chains ---


def test_lookup_edge_is_order_independent(sample_config):
    em = _edge_map(sample_config)
    e1 = _lookup_edge(em, "policy_regulatory", "financial_pressure")
    e2 = _lookup_edge(em, "financial_pressure", "policy_regulatory")
    assert e1 is e2 is not None
    assert e1.source == "policy_regulatory" and e1.target == "financial_pressure"
    # A non-declared pair returns nothing.
    assert _lookup_edge(em, "membership_movement", "financial_pressure") is None


def test_edge_overlap_becomes_directional_chain(sample_config):
    view = build_angles(
        [
            _story(
                "a",
                categories=["financial_pressure", "policy_regulatory"],
                title="CMS rate cut",
                score=0.9,
            ),
            _story(
                "b", categories=["policy_regulatory", "financial_pressure"], score=0.7
            ),
        ],
        [],
        sample_config,
    )
    chain = _find(view, "causal_chain")
    # Direction follows the edge (policy → financial), not the canonical sort.
    assert [s["label"] for s in chain["sides"]] == [
        "Policy / Regulatory Changes",
        "Financial / Operating Pressure",
    ]
    assert (
        chain["label"] == "Policy / Regulatory Changes → Financial / Operating Pressure"
    )
    assert chain["causal"]["source"] == "policy_regulatory"
    assert chain["causal"]["target"] == "financial_pressure"
    assert chain["causal"]["weight"] == 1.0
    assert "CMS final-rule impact analyses" in chain["causal"]["evidence"]
    assert chain["sides"][0]["href"] == "/topics/policy_regulatory"
    # One combined layer badge spanning the two layers.
    assert [ly["short"] for ly in chain["layers"]] == ["Drivers", "Pressure"]


def test_non_edge_overlap_stays_plain(sample_config):
    # financial_pressure ∩ membership_movement has no declared edge.
    view = build_angles(
        [
            _story("a", categories=["financial_pressure", "membership_movement"]),
            _story("b", categories=["financial_pressure", "membership_movement"]),
        ],
        [],
        sample_config,
    )
    tt = _find(view, "topic_topic")
    assert tt["causal"] is None
    assert tt["rank_score"] == 2.0
    assert "∩" in tt["label"]
    # Two distinct layers → two badges (cross-layer, non-edge).
    assert [ly["short"] for ly in tt["layers"]] == ["Pressure", "Outcomes"]


def test_layer_badges_single_and_payer_payer_empty(sample_config):
    single = build_angles(
        [
            _story("s1", entities=["Humana"], categories=["financial_pressure"]),
            _story("s2", entities=["Humana"], categories=["financial_pressure"]),
        ],
        [],
        sample_config,
    )
    assert [ly["short"] for ly in _find(single, "payer_topic")["layers"]] == [
        "Pressure"
    ]

    pp = build_angles(
        [
            _story("s1", entities=["Humana", "UnitedHealthcare"]),
            _story("s2", entities=["Humana", "UnitedHealthcare"]),
        ],
        [],
        sample_config,
    )
    assert _find(pp, "payer_payer")["layers"] == []  # no topic lens → no layers


def test_layer_badges_shared_layer(sample_config):
    # Two categories folded into one layer render as a single shared badge.
    cfg = dataclasses.replace(
        sample_config,
        causal_layers=[
            CausalLayerConfig(
                key="drivers",
                label="Drivers",
                short="Drivers",
                order=1,
                categories=["policy_regulatory", "financial_pressure"],
            ),
        ],
        causal_edges=[],
    )
    view = build_angles(
        [
            _story("a", categories=["policy_regulatory", "financial_pressure"]),
            _story("b", categories=["policy_regulatory", "financial_pressure"]),
        ],
        [],
        cfg,
    )
    assert [ly["short"] for ly in _find(view, "topic_topic")["layers"]] == ["Drivers"]


# --- Cascades ---


def test_cascade_derivation_and_constituent_suppression(sample_config):
    view = build_angles(
        [
            _story(
                "a", entities=["Humana"], categories=["policy_regulatory"], score=0.9
            ),
            _story(
                "b", entities=["Humana"], categories=["policy_regulatory"], score=0.8
            ),
            _story(
                "c", entities=["Humana"], categories=["financial_pressure"], score=0.7
            ),
        ],
        [],
        sample_config,
    )
    cascade = _find(view, "payer_cascade")
    assert cascade["count"] == 3  # union of {a, b} (policy) and {c} (financial)
    assert cascade["rank_score"] == 5.25  # 3 × (1 + 0.75 × 1.0)
    assert cascade["causal"]["source"] == "policy_regulatory"
    assert cascade["causal"]["target"] == "financial_pressure"
    assert [s["label"] for s in cascade["sides"]] == [
        "Humana",
        "Policy / Regulatory Changes",
        "Financial / Operating Pressure",
    ]
    assert (
        cascade["label"]
        == "Humana: Policy / Regulatory Changes → Financial / Operating Pressure"
    )
    # The P × policy constituent is a subset of the cascade → absorbed.
    assert not any(
        c["type"] == "payer_topic" and "Policy" in c["label"] for c in view["angles"]
    )


def test_cascade_union_dedupes_double_matching_story(sample_config):
    view = build_angles(
        [
            _story(
                "dual",
                entities=["Humana"],
                categories=["policy_regulatory", "financial_pressure"],
                score=0.9,
            ),
            _story(
                "fin", entities=["Humana"], categories=["financial_pressure"], score=0.8
            ),
        ],
        [],
        sample_config,
    )
    cascade = _find(view, "payer_cascade")
    # `dual` sits on both ends but counts once in the union.
    assert cascade is not None and cascade["count"] == 2


# --- Sequence consistency ---


def test_sequence_consistent_truth_table(sample_config):
    edge = sample_config.causal_edges[0]  # policy_regulatory -> financial_pressure
    # No previous window → unknowable.
    assert _sequence_consistent(edge, {"financial_pressure": 2}, {}, True) is None
    # Source present last period AND target rising now → consistent.
    assert (
        _sequence_consistent(
            edge,
            {"financial_pressure": 3},
            {"policy_regulatory": 1, "financial_pressure": 1},
            False,
        )
        is True
    )
    # Source absent last period → not consistent.
    assert (
        _sequence_consistent(
            edge, {"financial_pressure": 3}, {"financial_pressure": 1}, False
        )
        is False
    )
    # Source present but target flat → not consistent.
    assert (
        _sequence_consistent(
            edge,
            {"financial_pressure": 1},
            {"policy_regulatory": 1, "financial_pressure": 1},
            False,
        )
        is False
    )
    # Target newly appearing counts as rising.
    assert (
        _sequence_consistent(
            edge, {"financial_pressure": 2}, {"policy_regulatory": 1}, False
        )
        is True
    )


def test_empty_previous_all_new_and_consistency_none(sample_config):
    view = build_angles(
        [
            _story("a", categories=["policy_regulatory", "financial_pressure"]),
            _story("b", categories=["policy_regulatory", "financial_pressure"]),
        ],
        [],
        sample_config,
    )
    chain = _find(view, "causal_chain")
    assert chain["momentum"] == "new"
    assert chain["prev_count"] == 0
    assert chain["causal"]["sequence_consistent"] is None


def test_sequence_consistent_annotation_on_chain(sample_config):
    current = [
        _story("c1", categories=["policy_regulatory", "financial_pressure"]),
        _story("c2", categories=["policy_regulatory", "financial_pressure"]),
    ]
    previous = [_story("p1", categories=["policy_regulatory"])]
    chain = _find(build_angles(current, previous, sample_config), "causal_chain")
    assert chain["causal"]["sequence_consistent"] is True


# --- Fallback (sparse windows) ---


def test_fallback_when_sparse(sample_config):
    view = build_angles(
        [
            _story("m1", categories=["membership_movement"], score=0.9),
            _story("m2", categories=["membership_movement"], score=0.8),
            _story("m3", categories=["membership_movement"], score=0.7),
            _story("p1", categories=["policy_regulatory"], score=0.6),
            _story("p2", categories=["policy_regulatory"], score=0.5),
        ],
        [],
        sample_config,
    )
    topic_cards = [c for c in view["angles"] if c["type"] == "topic"]
    labels = {c["label"] for c in topic_cards}
    assert "Membership Movement" in labels
    assert "Policy / Regulatory Changes" in labels
    assert all(c["fallback"] for c in topic_cards)
    mem = next(c for c in topic_cards if c["label"] == "Membership Movement")
    assert [ly["short"] for ly in mem["layers"]] == ["Outcomes"]


def test_fallback_excludes_uncategorized_and_payer_payer(sample_config):
    view = build_angles(
        [
            _story("x1", entities=["Humana", "Cigna"]),
            _story("x2", entities=["Humana", "Cigna"]),
            _story("u1", categories=[]),
            _story("u2", categories=[]),
        ],
        [],
        sample_config,
    )
    assert any(c["type"] == "payer_payer" for c in view["angles"])
    # payer × payer stays an intersection; uncategorized never forms a fallback.
    assert not any(c["type"] == "topic" for c in view["angles"])


# --- Text, links, highlights, degradation ---


def test_fact_line_strings():
    assert (
        _fact_line(3, 2, "up", 1, "Big News")
        == "3 signals from 2 sources, up from 1 last period. Strongest: “Big News”."
    )
    assert (
        _fact_line(1, 1, "new", 0, "Solo")
        == "1 signal from 1 source, first showing this period. Strongest: “Solo”."
    )
    assert (
        _fact_line(2, 3, "down", 5, "Fade")
        == "2 signals from 3 sources, down from 5 last period. Strongest: “Fade”."
    )
    assert (
        _fact_line(4, 2, "steady", 4, "Flat")
        == "4 signals from 2 sources, steady vs. last period. Strongest: “Flat”."
    )


def test_stale_topic_key_has_no_link(sample_config):
    view = build_angles(
        [
            _story("a", categories=["star_ratings_legacy", "financial_pressure"]),
            _story("b", categories=["star_ratings_legacy", "financial_pressure"]),
        ],
        [],
        sample_config,
    )
    tt = _find(view, "topic_topic")
    stale = next(s for s in tt["sides"] if s["label"] == "star_ratings_legacy")
    assert stale["href"] is None  # no dead /topics link for a removed category
    live = next(
        s for s in tt["sides"] if s["label"] == "Financial / Operating Pressure"
    )
    assert live["href"] == "/topics/financial_pressure"


def test_highlights_fold_payers_and_states(sample_config):
    view = build_angles(
        [
            _story(
                "a",
                entities=["UnitedHealth", "UHC", "CMS"],
                categories=["policy_regulatory"],
            ),
            _story(
                "b",
                entities=["Humana"],
                categories=["policy_regulatory"],
                states=["TX"],
            ),
        ],
        [],
        sample_config,
    )
    payers = {p["slug"]: p for p in view["highlights"]["payers"]}
    assert set(payers) == {"unitedhealthcare", "humana"}
    assert payers["unitedhealthcare"]["count"] == 1  # two aliases, one story
    assert view["highlights"]["states"] == [{"code": "TX", "count": 1}]
    assert view["highlights"]["total"] == 2


def test_empty_inputs(sample_config):
    view = build_angles([], [], sample_config)
    assert view["angles"] == []
    assert view["highlights"] == {"total": 0, "payers": [], "states": []}


def test_empty_causal_model_degrades_to_plain(sample_config):
    cfg = dataclasses.replace(sample_config, causal_layers=[], causal_edges=[])
    view = build_angles(
        [
            _story("a", categories=["policy_regulatory", "financial_pressure"]),
            _story("b", categories=["policy_regulatory", "financial_pressure"]),
        ],
        [],
        cfg,
    )
    tt = _find(view, "topic_topic")
    assert tt["causal"] is None
    assert tt["layers"] == []
    assert not any(
        c["type"] in ("causal_chain", "payer_cascade") for c in view["angles"]
    )
    assert _causal_model_view(cfg) is None


def test_causal_model_view_shape(sample_config):
    view = _causal_model_view(sample_config)
    assert view is not None
    assert [ly["short"] for ly in view["layers"]] == [
        "Drivers",
        "Pressure",
        "Response",
        "Outcomes",
    ]
    assert view["layers"][0]["categories"] == [
        {"key": "policy_regulatory", "label": "Policy / Regulatory Changes"}
    ]
    e0 = view["edges"][0]
    assert e0["source_label"] == "Policy / Regulatory Changes"
    assert e0["target_label"] == "Financial / Operating Pressure"
    assert e0["weight"] == 1.0
    assert e0["evidence"]  # non-empty

"""Tests for the shared causal-model helpers (causal.py)."""

from ma_signal_monitor import angles, causal


def test_edge_map_keys_by_direction(sample_config):
    em = causal.edge_map(sample_config)
    assert ("policy_regulatory", "financial_pressure") in em
    # Downstream-only: the reverse direction is never a key.
    assert ("financial_pressure", "policy_regulatory") not in em


def test_lookup_edge_is_order_independent(sample_config):
    em = causal.edge_map(sample_config)
    fwd = causal.lookup_edge(em, "policy_regulatory", "financial_pressure")
    rev = causal.lookup_edge(em, "financial_pressure", "policy_regulatory")
    assert fwd is not None and fwd is rev
    # Two categories with no declared edge between them return None.
    assert causal.lookup_edge(em, "membership_movement", "policy_regulatory") is None


def test_layer_map_places_every_category(sample_config):
    lm = causal.layer_map(sample_config)
    assert lm["policy_regulatory"].key == "drivers"
    assert lm["financial_pressure"].key == "pressure"
    assert lm["competitive_strategy"].key == "response"
    assert lm["membership_movement"].key == "outcomes"


def test_layers_for_topics_returns_causal_order(sample_config):
    lm = causal.layer_map(sample_config)
    layers = causal.layers_for_topics(
        # Deliberately out of causal order on input.
        ["membership_movement", "policy_regulatory", "financial_pressure"],
        lm,
    )
    assert [ly["short"] for ly in layers] == ["Drivers", "Pressure", "Outcomes"]


def test_layers_for_topics_ignores_unknown_keys(sample_config):
    lm = causal.layer_map(sample_config)
    assert causal.layers_for_topics(["not_a_category"], lm) == []


def test_angles_reexports_causal_helpers():
    # angles.py keeps the helpers under their private names via causal.py, so the
    # extraction is transparent to importers (e.g. tests/test_angles.py).
    assert angles._edge_map is causal.edge_map
    assert angles._lookup_edge is causal.lookup_edge
    assert angles._layer_map is causal.layer_map
    assert angles._layers_for_topics is causal.layers_for_topics

"""Tests for the declared causal-layer model (config/causal_model.yaml).

Two tiers, mirroring decision D7:

* Coverage/soundness of the REAL shipped model is asserted against the on-disk
  config, the same pattern ``test_payers.py`` uses for watched_entities
  (``test_payers.py:59-63``). This is where full taxonomy coverage lives — the
  runtime validator deliberately does not enforce it.
* The runtime validator ``_validate_config`` is exercised directly with small
  in-memory models, one per soundness violation class.
"""

import logging
from pathlib import Path

import pytest
import yaml

from ma_signal_monitor.config import (
    AppConfig,
    CategoryConfig,
    CausalEdgeConfig,
    CausalLayerConfig,
    SourceConfig,
    _validate_config,
    load_config,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CAUSAL_PATH = _PROJECT_ROOT / "config/causal_model.yaml"
_TAXONOMY_PATH = _PROJECT_ROOT / "config/taxonomy.yaml"


def _shipped_model() -> dict:
    return yaml.safe_load(_CAUSAL_PATH.read_text())


def _taxonomy_categories() -> set[str]:
    return set(yaml.safe_load(_TAXONOMY_PATH.read_text())["categories"])


def _shipped_layer_order() -> dict[str, int]:
    """Category -> layer order (1-based), reading dict order as the causal order."""
    return {
        cat: order
        for order, layer in enumerate(_shipped_model()["layers"].values(), start=1)
        for cat in layer["categories"]
    }


# --- Shipped config invariants (enforced against the real file) ---


def test_shipped_causal_model_exists():
    assert _CAUSAL_PATH.exists(), "config/causal_model.yaml must ship with the repo"


def test_every_taxonomy_category_in_exactly_one_layer():
    """Adding a taxonomy category requires placing it in exactly one causal layer."""
    categories = _taxonomy_categories()
    placements = [
        cat
        for layer in _shipped_model()["layers"].values()
        for cat in layer["categories"]
    ]
    # No category placed twice...
    assert len(placements) == len(set(placements)), (
        f"a category appears in more than one layer: {sorted(placements)}"
    )
    # ...and the placed set is exactly the taxonomy set (full coverage, no strays).
    assert set(placements) == categories, (
        "layers must cover every taxonomy category exactly once; "
        f"missing={sorted(categories - set(placements))} "
        f"unknown={sorted(set(placements) - categories)}"
    )


def test_shipped_layer_categories_are_known():
    categories = _taxonomy_categories()
    for key, layer in _shipped_model()["layers"].items():
        for cat in layer["categories"]:
            assert cat in categories, (
                f"layer {key!r} references unknown category {cat!r}"
            )


def test_shipped_edges_are_sound():
    """Every shipped edge is known-key, non-self, strictly downstream, in range,
    evidenced, and unique."""
    order = _shipped_layer_order()
    seen: set[tuple[str, str]] = set()
    for edge in _shipped_model()["edges"]:
        src, tgt = edge["source"], edge["target"]
        assert src in order, f"unknown edge source {src!r}"
        assert tgt in order, f"unknown edge target {tgt!r}"
        assert src != tgt, f"self-loop on {src!r}"
        assert order[src] < order[tgt], f"edge {src}->{tgt} is not strictly downstream"
        assert 0.0 <= edge["weight"] <= 1.0, f"edge {src}->{tgt} weight out of [0,1]"
        assert str(edge["evidence"]).strip(), f"edge {src}->{tgt} has blank evidence"
        pair = (src, tgt)
        assert pair not in seen, f"duplicate edge {src}->{tgt}"
        seen.add(pair)


def test_load_config_enables_causal_model():
    """load_config on the real project root loads a valid, enabled 8-edge model."""
    config = load_config(_PROJECT_ROOT)
    assert config.causal_model_enabled is True
    assert len(config.causal_edges) == 8
    assert len(config.causal_layers) == 4
    # Layer order comes from the YAML dict order, 1-based and gap-free.
    assert [layer.order for layer in config.causal_layers] == [1, 2, 3, 4]
    first = config.causal_layers[0]
    assert first.key == "structural_policy_drivers"
    assert first.label == "Structural & Policy Drivers"
    assert first.short == "Drivers"
    # Every edge carries non-empty evidence once parsed into dataclasses.
    assert all(e.evidence.strip() for e in config.causal_edges)


def test_missing_causal_model_disables_feature(project_root_with_config, caplog):
    """No causal_model.yaml -> warning, empty model, feature disabled (not an error)."""
    with caplog.at_level(logging.WARNING, logger="ma_signal_monitor.config"):
        config = load_config(project_root_with_config)
    assert config.causal_model_enabled is False
    assert config.causal_layers == []
    assert config.causal_edges == []
    assert "Angles causal features disabled" in caplog.text


# --- Runtime validator (_validate_config) soundness checks ---

_CATEGORY_KEYS = (
    "policy_regulatory",
    "demographic_shifts",
    "financial_pressure",
    "competitive_strategy",
    "brokerage_distribution",
    "membership_movement",
)


def _base_config() -> AppConfig:
    """A minimal AppConfig that passes non-causal validation and carries a sound
    causal model, so individual causal violations can be injected in isolation."""
    return AppConfig(
        webhook_mode="test",
        sources=[
            SourceConfig(name="S", type="rss", url="https://example.com", enabled=True)
        ],
        categories=[
            CategoryConfig(key=k, label=k, description="", weight=1.0, keywords=[])
            for k in _CATEGORY_KEYS
        ],
        causal_layers=[
            CausalLayerConfig(
                key="drivers",
                label="Drivers",
                short="Drivers",
                order=1,
                categories=["policy_regulatory", "demographic_shifts"],
            ),
            CausalLayerConfig(
                key="pressure",
                label="Pressure",
                short="Pressure",
                order=2,
                categories=["financial_pressure"],
            ),
            CausalLayerConfig(
                key="response",
                label="Response",
                short="Response",
                order=3,
                categories=["competitive_strategy", "brokerage_distribution"],
            ),
            CausalLayerConfig(
                key="outcomes",
                label="Outcomes",
                short="Outcomes",
                order=4,
                categories=["membership_movement"],
            ),
        ],
        causal_edges=[
            CausalEdgeConfig("policy_regulatory", "financial_pressure", 1.0, "e"),
            CausalEdgeConfig("competitive_strategy", "membership_movement", 0.9, "e"),
        ],
    )


def test_base_config_is_valid():
    """The unmodified base model passes — proving later failures are the injected
    violation and nothing else."""
    _validate_config(_base_config())  # must not raise


def test_category_in_two_layers_raises():
    config = _base_config()
    # policy_regulatory already lives in layer 1; also claim it in layer 2.
    config.causal_layers[1].categories.append("policy_regulatory")
    with pytest.raises(ValueError, match="more than one layer"):
        _validate_config(config)


def test_layer_category_not_in_taxonomy_raises():
    config = _base_config()
    config.causal_layers[0].categories.append("not_a_real_category")
    with pytest.raises(ValueError, match="unknown category"):
        _validate_config(config)


def test_edge_unknown_endpoint_raises():
    config = _base_config()
    config.causal_edges.append(
        CausalEdgeConfig("ghost_category", "membership_movement", 0.5, "e")
    )
    with pytest.raises(ValueError, match="not assigned to any layer"):
        _validate_config(config)


def test_edge_self_loop_raises():
    config = _base_config()
    config.causal_edges.append(
        CausalEdgeConfig("financial_pressure", "financial_pressure", 0.5, "e")
    )
    with pytest.raises(ValueError, match="self-loop"):
        _validate_config(config)


def test_edge_upstream_raises():
    config = _base_config()
    # membership_movement (layer 4) -> policy_regulatory (layer 1) runs upstream.
    config.causal_edges.append(
        CausalEdgeConfig("membership_movement", "policy_regulatory", 0.5, "e")
    )
    with pytest.raises(ValueError, match="downstream"):
        _validate_config(config)


def test_edge_within_same_layer_raises():
    config = _base_config()
    # Both endpoints are layer 1: not strictly downstream.
    config.causal_edges.append(
        CausalEdgeConfig("policy_regulatory", "demographic_shifts", 0.5, "e")
    )
    with pytest.raises(ValueError, match="downstream"):
        _validate_config(config)


def test_edge_weight_out_of_range_raises():
    config = _base_config()
    config.causal_edges.append(
        CausalEdgeConfig("policy_regulatory", "competitive_strategy", 1.5, "e")
    )
    with pytest.raises(ValueError, match="weight"):
        _validate_config(config)


def test_edge_blank_evidence_raises():
    config = _base_config()
    config.causal_edges.append(
        CausalEdgeConfig("policy_regulatory", "brokerage_distribution", 0.5, "   ")
    )
    with pytest.raises(ValueError, match="evidence"):
        _validate_config(config)


def test_duplicate_edge_pair_raises():
    config = _base_config()
    # (policy_regulatory, financial_pressure) is already in the base model.
    config.causal_edges.append(
        CausalEdgeConfig("policy_regulatory", "financial_pressure", 0.8, "e")
    )
    with pytest.raises(ValueError, match="[Dd]uplicate"):
        _validate_config(config)

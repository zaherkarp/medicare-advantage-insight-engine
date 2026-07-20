"""Tests for per-topic color assignment (topic_colors.py)."""

from ma_signal_monitor.config import CategoryConfig
from ma_signal_monitor.topic_colors import (
    DEFAULT_TOPIC_PALETTE,
    FALLBACK_TOPIC_COLOR,
    topic_color,
    topic_color_map,
)


def _cat(key: str, color: str = "") -> CategoryConfig:
    return CategoryConfig(
        key=key,
        label=key.replace("_", " ").title(),
        description="",
        weight=1.0,
        keywords=[],
        color=color,
    )


def test_positional_assignment_follows_config_order():
    """Colors are assigned by position, matching taxonomy config order."""
    cats = [_cat("a"), _cat("b"), _cat("c")]
    color_map = topic_color_map(cats)
    assert color_map == {
        "a": DEFAULT_TOPIC_PALETTE[0],
        "b": DEFAULT_TOPIC_PALETTE[1],
        "c": DEFAULT_TOPIC_PALETTE[2],
    }


def test_yaml_color_override_wins():
    """A category's YAML `color` beats its positional default."""
    cats = [_cat("a", color="#123456"), _cat("b")]
    color_map = topic_color_map(cats)
    assert color_map["a"] == "#123456"
    assert color_map["b"] == DEFAULT_TOPIC_PALETTE[1]


def test_unknown_key_falls_back():
    """A key absent from the map resolves to the fallback grey."""
    color_map = topic_color_map([_cat("a")])
    assert topic_color("not_a_category", color_map) == FALLBACK_TOPIC_COLOR


def test_uncategorized_falls_back():
    """`uncategorized` is never a real taxonomy category, so it falls back."""
    color_map = topic_color_map([_cat("a")])
    assert topic_color("uncategorized", color_map) == FALLBACK_TOPIC_COLOR


def test_other_grouping_row_falls_back():
    """The synthetic "Other" grouping row falls back, not a series hue."""
    color_map = topic_color_map([_cat("a")])
    assert topic_color("Other", color_map) == FALLBACK_TOPIC_COLOR


def test_none_or_empty_key_falls_back():
    color_map = topic_color_map([_cat("a")])
    assert topic_color(None, color_map) == FALLBACK_TOPIC_COLOR
    assert topic_color("", color_map) == FALLBACK_TOPIC_COLOR


def test_category_beyond_palette_length_falls_back():
    """A 7th category (palette only has 6 hues) gets the fallback color."""
    cats = [_cat(f"c{i}") for i in range(len(DEFAULT_TOPIC_PALETTE) + 1)]
    color_map = topic_color_map(cats)
    for i, cat in enumerate(cats[: len(DEFAULT_TOPIC_PALETTE)]):
        assert color_map[cat.key] == DEFAULT_TOPIC_PALETTE[i]
    overflow_key = cats[-1].key
    assert color_map[overflow_key] == FALLBACK_TOPIC_COLOR


def test_topic_color_map_empty_list_returns_empty_dict():
    assert topic_color_map([]) == {}

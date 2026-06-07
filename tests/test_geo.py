"""Tests for U.S. state detection (State Intelligence)."""

from datetime import datetime

from ma_signal_monitor.geo import (
    detect_states,
    detect_states_in_text,
    state_name,
)
from ma_signal_monitor.models import NormalizedItem, ScoredItem


def _scored(title: str, summary: str = "") -> ScoredItem:
    item = NormalizedItem(
        item_id="t1",
        source_name="Test",
        source_type="rss",
        source_priority=3,
        source_tags=[],
        title=title,
        link="https://example.com/1",
        published_date=datetime(2024, 1, 1),
        summary=summary,
    )
    return ScoredItem(item=item, relevance_score=0.5)


def test_detects_full_state_name():
    assert detect_states_in_text("Humana exits 13 California counties") == ["CA"]


def test_detects_multiple_states():
    found = detect_states_in_text("Plans in Texas and Florida saw growth")
    assert set(found) == {"TX", "FL"}


def test_longer_name_wins_over_substring():
    # "West Virginia" must not also register as "Virginia".
    assert detect_states_in_text("New rules in West Virginia") == ["WV"]


def test_word_boundary_avoids_false_positives():
    # "Indiana" contains "Indiana"; ensure we don't match inside other words.
    assert detect_states_in_text("The organization restructured") == []


def test_no_match_returns_empty():
    assert detect_states_in_text("CMS finalizes national Star Ratings rule") == []


def test_detect_states_uses_title_and_summary():
    scored = _scored("CMS rule update", summary="Impact felt across Ohio markets")
    assert detect_states(scored) == ["OH"]


def test_state_name_lookup():
    assert state_name("CO") == "Colorado"
    assert state_name("ZZ") == "ZZ"  # unknown code falls back to itself

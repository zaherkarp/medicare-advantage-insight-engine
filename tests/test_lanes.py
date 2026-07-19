"""Tests for swimlane bucketing and placement geometry (/timeline page)."""

from datetime import datetime, timedelta

from ma_signal_monitor.lanes import (
    DOT_PITCH,
    DOT_R,
    LANE_PAD_Y,
    MAX_DOTS_PER_CELL,
    MIN_LANE_H,
    OVERFLOW_R,
    axis_ticks,
    build_lane,
    cell_pct,
)

NOW = datetime(2024, 3, 20, 12, 0)


def _story(item_id, *, days_ago=0, score=0.5, title=None):
    """A minimal _story_view-shaped dict, event-dated ``days_ago`` before NOW."""
    return {
        "item_id": item_id,
        "title": title or f"Story {item_id}",
        "source_name": "Test Feed",
        "event_date": (NOW - timedelta(days=days_ago)).isoformat(),
        "relevance_score": score,
    }


# --- Axis ticks ---


def test_cell_pct_centers_day_columns():
    assert cell_pct(0, 1) == 50.0  # a one-day window centers its only column
    assert cell_pct(0, 10) == 5.0
    assert cell_pct(9, 10) == 95.0


def test_axis_ticks_short_window_labels_every_day():
    ticks = axis_ticks(7, NOW)
    assert len(ticks) == 7
    assert [t.index for t in ticks] == list(range(7))
    assert ticks[-1].label == "Mar 20"  # today is always labeled
    assert ticks[0].label == "Mar 14"


def test_axis_ticks_strides_stay_scannable():
    # 5-8 labels for every preset window, always anchored on today.
    for days, expected in ((7, 7), (14, 7), (30, 5), (90, 7)):
        ticks = axis_ticks(days, NOW)
        assert len(ticks) == expected
        assert ticks[-1].index == days - 1  # newest day carries a label
    # 30d window strides weekly: consecutive labels are 7 days apart.
    monthly = axis_ticks(30, NOW)
    assert [t.index for t in monthly] == [1, 8, 15, 22, 29]


def test_axis_ticks_percentages_ascend_within_bounds():
    ticks = axis_ticks(90, NOW)
    pcts = [t.pct for t in ticks]
    assert pcts == sorted(pcts)
    assert all(0 < p < 100 for p in pcts)


# --- Lane building ---


def test_build_lane_places_single_story_on_bottom_row():
    lane = build_lane("k", "Label", "/x", [_story("a", days_ago=2)], days=7, now=NOW)
    assert lane.total == 1
    assert lane.height == MIN_LANE_H
    (m,) = lane.markers
    assert m.pct == cell_pct(4, 7)  # 2 days ago = index 4 in a 7-day window
    assert m.bottom == LANE_PAD_Y + DOT_R  # bottom row
    assert m.size == 2 * DOT_R
    assert m.item_id == "a"
    assert m.overflow == 0
    assert m.day == (NOW - timedelta(days=2)).date().isoformat()


def test_build_lane_stacks_same_day_stories_and_grows():
    stories = [_story(f"s{i}", days_ago=1, score=0.5 + i / 10) for i in range(3)]
    lane = build_lane("k", "Label", None, stories, days=7, now=NOW)
    assert lane.total == 3
    assert len(lane.markers) == 3
    assert len({m.pct for m in lane.markers}) == 1  # same day → same column
    bottoms = [m.bottom for m in lane.markers]
    assert bottoms == [
        LANE_PAD_Y + DOT_R + i * DOT_PITCH for i in range(3)
    ]  # stacked bottom-up by pitch
    # The strongest signal sits on the bottom row.
    assert lane.markers[0].item_id == "s2"
    assert lane.height == 2 * LANE_PAD_Y + 2 * DOT_R + 2 * DOT_PITCH


def test_build_lane_collapses_deep_stacks_into_overflow_dot():
    stories = [_story(f"s{i}", days_ago=1, score=i / 10) for i in range(6)]
    lane = build_lane("k", "Label", None, stories, days=7, now=NOW)
    assert lane.total == 6  # collapsed stories still count
    assert len(lane.markers) == MAX_DOTS_PER_CELL
    *dots, more = lane.markers
    # The strongest three survive as dots; the rest collapse into one marker.
    assert {m.item_id for m in dots} == {"s5", "s4", "s3"}
    assert more.overflow == 3
    assert more.item_id == ""
    assert more.size == 2 * OVERFLOW_R
    assert more.bottom == LANE_PAD_Y + DOT_R + 3 * DOT_PITCH  # on top of the stack
    # Height fits the visible stack (never deeper than MAX_DOTS_PER_CELL).
    assert lane.height == 2 * LANE_PAD_Y + 2 * DOT_R + (MAX_DOTS_PER_CELL - 1) * (
        DOT_PITCH
    )


def test_build_lane_drops_out_of_window_and_bad_dates():
    stories = [
        _story("past", days_ago=30),  # before a 7-day window
        _story("future", days_ago=-3),  # future-dated / misparsed
        {
            "item_id": "bad",
            "title": "t",
            "source_name": "s",
            "event_date": "not-a-date",
        },
        {"item_id": "none", "title": "t", "source_name": "s", "event_date": ""},
    ]
    lane = build_lane("k", "Label", None, stories, days=7, now=NOW)
    assert lane.total == 0
    assert lane.markers == ()
    assert lane.height == MIN_LANE_H


def test_build_lane_window_start_boundary_plots_at_first_column():
    lane = build_lane("k", "Label", None, [_story("edge", days_ago=6)], days=7, now=NOW)
    (m,) = lane.markers
    assert m.pct == cell_pct(0, 7)  # oldest in-window day → first column


def test_build_lane_empty():
    lane = build_lane("k", "Label", None, [], days=7, now=NOW)
    assert lane.total == 0
    assert lane.markers == ()
    assert lane.height == MIN_LANE_H
    assert lane.key == "k" and lane.label == "Label" and lane.href is None

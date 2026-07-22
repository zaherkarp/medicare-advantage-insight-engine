"""Tests for layered-timeline geometry (callout band + topic strip)."""

from datetime import datetime, timedelta

from ma_signal_monitor.timeline_layout import (
    BUBBLE_D_MAX,
    BUBBLE_D_MIN,
    CALLOUT_ROW_H,
    CALLOUT_W_PCT,
    STEM_BASE,
    axis_ticks,
    bubble_size,
    build_callout_band,
    build_strip,
    cell_pct,
)

NOW = datetime(2024, 3, 20, 12, 0)
FALLBACK = "#687385"  # a fallback color literal, supplied like routes would


def _story(item_id, *, days_ago=0, score=0.5, category=None, title=None):
    """A minimal _story_view-shaped dict, event-dated ``days_ago`` before NOW."""
    return {
        "item_id": item_id,
        "title": title or f"Story {item_id}",
        "source_name": "Test Feed",
        "event_date": (NOW - timedelta(days=days_ago)).isoformat(),
        "relevance_score": score,
        "primary_category": category,
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


def test_axis_ticks_preset_windows_stay_scannable():
    # 5-8 labels for every preset window (incl. the long "all" strides), and
    # the newest day always carries a label.
    for days, expected in ((7, 7), (30, 5), (90, 7), (180, 6), (365, 7), (730, 7)):
        ticks = axis_ticks(days, NOW)
        assert len(ticks) == expected, (days, len(ticks))
        assert 5 <= len(ticks) <= 8
        assert ticks[-1].index == days - 1  # today labeled
        indices = [t.index for t in ticks]
        assert indices == sorted(indices)  # oldest → newest
        assert all(0 < t.pct < 100 for t in ticks)


def test_axis_ticks_30d_strides_weekly():
    ticks = axis_ticks(30, NOW)
    assert [t.index for t in ticks] == [1, 8, 15, 22, 29]


# --- bubble_size (area ∝ count) ---


def test_bubble_size_area_proportional():
    # 4× the count → 2× the diameter (4× the area) against the same max.
    assert bubble_size(4, 4) == BUBBLE_D_MAX  # 22
    assert bubble_size(1, 4) == 11  # round(22 * 0.5)
    assert bubble_size(4, 4) == 2 * bubble_size(1, 4)


def test_bubble_size_floor_and_zero_cases():
    assert bubble_size(1, 100) == BUBBLE_D_MIN  # round(22*0.1)=2 → floored to 6
    assert bubble_size(0, 4) == 0
    assert bubble_size(-3, 5) == 0
    assert bubble_size(5, 0) == 0  # no max (all-empty strip) → 0, no ZeroDivision


# --- Callout band: per-day winner + selection ---


def test_callout_per_day_keeps_highest_relevance():
    stories = [
        _story("weak", days_ago=3, score=0.2),
        _story("strong", days_ago=3, score=0.8),
        _story("mid", days_ago=3, score=0.5),
    ]
    band = build_callout_band(
        stories, days=7, now=NOW, color_map={}, fallback_color=FALLBACK
    )
    assert len(band.callouts) == 1  # one winner per day
    assert band.callouts[0].item_id == "strong"
    assert band.callouts[0].title == "Story strong"


def test_callout_top_limit_ranks_by_relevance_then_newer_day():
    # Equal scores → the two newest days win the ties; the oldest is excluded.
    stories = [
        _story("new", days_ago=0),
        _story("mid", days_ago=15),
        _story("old", days_ago=29),
    ]
    band = build_callout_band(
        stories, days=30, now=NOW, color_map={}, fallback_color=FALLBACK, limit=2
    )
    assert {c.item_id for c in band.callouts} == {"new", "mid"}
    assert band.dropped == 0


def test_callout_top_limit_relevance_beats_recency():
    stories = [
        _story("new", days_ago=0, score=0.1),
        _story("mid", days_ago=15, score=0.9),
        _story("old", days_ago=29, score=0.5),
    ]
    band = build_callout_band(
        stories, days=30, now=NOW, color_map={}, fallback_color=FALLBACK, limit=2
    )
    assert {c.item_id for c in band.callouts} == {"mid", "old"}


def test_callout_selection_capped_at_limit():
    # Nine spaced day-winners, default limit 8: exactly 8 go through layout
    # (placed or dropped), and the oldest day-winner is never selected.
    stories = [_story(f"d{a}", days_ago=a) for a in (0, 10, 20, 30, 40, 50, 60, 70, 80)]
    band = build_callout_band(
        stories, days=90, now=NOW, color_map={}, fallback_color=FALLBACK
    )
    assert len(band.callouts) + band.dropped == 8
    assert "d80" not in {c.item_id for c in band.callouts}


# --- Callout band: greedy row packing ---


def test_callout_adjacent_labels_bump_to_next_row():
    # Two adjacent days: both label cards clamp to the right edge and overlap,
    # so they land in different rows.
    stories = [_story("a", days_ago=0), _story("b", days_ago=1)]
    band = build_callout_band(
        stories, days=30, now=NOW, color_map={}, fallback_color=FALLBACK
    )
    assert len(band.callouts) == 2
    assert {c.row for c in band.callouts} == {0, 1}
    assert band.rows_used == 2
    assert band.dropped == 0
    assert band.height == 2 * CALLOUT_ROW_H + STEM_BASE  # 130


def test_callout_overlap_fills_all_rows_then_drops():
    # Four mutually-overlapping labels: rows 0/1/2 fill, the fourth is dropped.
    stories = [_story(f"d{a}", days_ago=a) for a in (0, 1, 2, 3)]
    band = build_callout_band(
        stories, days=30, now=NOW, color_map={}, fallback_color=FALLBACK
    )
    assert len(band.callouts) == 3
    assert {c.row for c in band.callouts} == {0, 1, 2}
    assert band.rows_used == 3
    assert band.dropped == 1
    assert band.height == 3 * CALLOUT_ROW_H + STEM_BASE  # 188


def test_callout_stem_h_tracks_row():
    stories = [_story(f"d{a}", days_ago=a) for a in (0, 1, 2)]
    band = build_callout_band(
        stories, days=30, now=NOW, color_map={}, fallback_color=FALLBACK
    )
    by_row = {c.row: c for c in band.callouts}
    for row, c in by_row.items():
        assert c.stem_h == row * CALLOUT_ROW_H + STEM_BASE


# --- Callout band: label clamping ---


def test_callout_label_clamped_at_left_edge():
    band = build_callout_band(
        [_story("old", days_ago=29)],
        days=30,
        now=NOW,
        color_map={},
        fallback_color=FALLBACK,
    )
    (c,) = band.callouts
    assert c.label_left_pct == 0.0
    assert c.pct == cell_pct(0, 30)


def test_callout_label_clamped_at_right_edge():
    band = build_callout_band(
        [_story("new", days_ago=0)],
        days=30,
        now=NOW,
        color_map={},
        fallback_color=FALLBACK,
    )
    (c,) = band.callouts
    assert c.label_left_pct == 100.0 - CALLOUT_W_PCT  # 76.0
    assert c.pct == cell_pct(29, 30)


# --- Callout band: colors + empty/garbage handling ---


def test_callout_color_resolution():
    color_map = {"policy_regulatory": "#e87ba4"}
    stories = [
        _story("colored", days_ago=0, category="policy_regulatory"),
        _story("uncat", days_ago=15, category=None),
        _story("unknown", days_ago=29, category="not_a_topic"),
    ]
    band = build_callout_band(
        stories, days=30, now=NOW, color_map=color_map, fallback_color=FALLBACK
    )
    by_id = {c.item_id: c for c in band.callouts}
    assert by_id["colored"].color == "#e87ba4"
    assert by_id["colored"].category_key == "policy_regulatory"
    assert by_id["uncat"].color == FALLBACK  # None category → fallback
    assert by_id["uncat"].category_key == ""
    assert by_id["unknown"].color == FALLBACK  # unknown key → fallback


def test_callout_empty_band_is_stem_base_tall():
    band = build_callout_band(
        [], days=7, now=NOW, color_map={}, fallback_color=FALLBACK
    )
    assert band.callouts == ()
    assert band.rows_used == 0
    assert band.dropped == 0
    assert band.height == STEM_BASE  # 14


def test_callout_drops_out_of_window_and_bad_dates():
    stories = [
        _story("past", days_ago=30),  # before a 7-day window
        _story("future", days_ago=-3),  # future-dated
        {"item_id": "bad", "event_date": "not-a-date"},
        {"item_id": "none", "event_date": ""},
        _story("ok", days_ago=1),
    ]
    band = build_callout_band(
        stories, days=7, now=NOW, color_map={}, fallback_color=FALLBACK
    )
    assert [c.item_id for c in band.callouts] == ["ok"]


# --- Topic strip ---


def test_build_strip_shares_max_count_across_rows():
    # The 9-story day sets a shared max; 3-story days in different rows render
    # identically, and smaller than the 9-story bubble.
    big = [_story(f"big{i}", days_ago=1) for i in range(9)]
    a = [_story(f"a{i}", days_ago=2) for i in range(3)]
    b = [_story(f"b{i}", days_ago=3) for i in range(3)]
    strip = build_strip(
        [
            ("big", "Big", "/big", "#111", big),
            ("a", "A", "/a", "#222", a),
            ("b", "B", "/b", "#333", b),
        ],
        days=7,
        now=NOW,
    )
    assert [r.key for r in strip] == ["big", "a", "b"]
    big_row, a_row, b_row = strip
    assert big_row.total == 9
    (big_bub,) = big_row.bubbles
    assert big_bub.count == 9
    assert big_bub.size == BUBBLE_D_MAX  # the shared max → full diameter
    (a_bub,) = a_row.bubbles
    (b_bub,) = b_row.bubbles
    assert a_bub.size == b_bub.size == bubble_size(3, 9)  # shared max, not per-row
    assert a_bub.size < BUBBLE_D_MAX


def test_build_strip_small_bubble_against_global_max():
    # A lone-story row measured against a global max of 9, not its own max of 1.
    big = [_story(f"big{i}", days_ago=1) for i in range(9)]
    one = [_story("one", days_ago=2)]
    strip = build_strip(
        [("big", "Big", None, "#111", big), ("one", "One", None, "#222", one)],
        days=7,
        now=NOW,
    )
    _, one_row = strip
    (one_bub,) = one_row.bubbles
    assert one_bub.size == bubble_size(1, 9)  # small, not BUBBLE_D_MAX


def test_build_strip_keeps_empty_rows_and_skips_zero_days():
    strip = build_strip(
        [
            ("a", "A", "/a", "#111", [_story("a1", days_ago=1)]),
            ("empty", "Empty", "/e", "#222", []),
        ],
        days=7,
        now=NOW,
    )
    assert len(strip) == 2
    a_row, empty_row = strip
    assert a_row.total == 1
    assert len(a_row.bubbles) == 1
    assert empty_row.total == 0
    assert empty_row.bubbles == ()


def test_build_strip_places_bubble_per_day():
    stories = [_story("a", days_ago=0), _story("b", days_ago=6)]
    strip = build_strip([("a", "A", "/a", "#111", stories)], days=7, now=NOW)
    (row,) = strip
    assert row.total == 2
    assert sorted(bub.pct for bub in row.bubbles) == [cell_pct(0, 7), cell_pct(6, 7)]
    days = {bub.day for bub in row.bubbles}
    assert (NOW - timedelta(days=6)).date().isoformat() in days


def test_build_strip_drops_out_of_window_and_bad_dates():
    stories = [
        _story("past", days_ago=30),
        _story("future", days_ago=-3),
        {"item_id": "bad", "event_date": "nope"},
        {"item_id": "none", "event_date": ""},
        _story("ok", days_ago=1),
    ]
    strip = build_strip([("a", "A", "/a", "#111", stories)], days=7, now=NOW)
    (row,) = strip
    assert row.total == 1
    (bub,) = row.bubbles
    assert bub.count == 1


def test_build_strip_all_empty_groups_have_no_bubbles():
    strip = build_strip(
        [("a", "A", None, "#111", []), ("b", "B", None, "#222", [])],
        days=7,
        now=NOW,
    )
    assert all(r.total == 0 and r.bubbles == () for r in strip)

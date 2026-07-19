"""Tests for weekly-trend bucketing and sparkline geometry."""

from datetime import date, datetime

from ma_signal_monitor.trends import (
    SPARK_HEIGHT,
    SPARK_WIDTH,
    daily_series,
    marker_point,
    sparkline,
    sparkline_points,
    week_start,
    weekly_series,
)


def test_week_start_is_monday():
    assert week_start(datetime(2024, 1, 3, 15, 0)) == date(2024, 1, 1)  # Wed → Mon
    assert week_start(date(2024, 1, 1)) == date(2024, 1, 1)  # Monday → itself
    assert week_start(datetime(2024, 1, 7)) == date(2024, 1, 1)  # Sun → prior Mon


def test_weekly_series_zero_fills_and_orders():
    now = datetime(2024, 1, 31)  # Wednesday
    dates = [
        datetime(2024, 1, 30),  # current week
        datetime(2024, 1, 29),  # current week (Monday)
        datetime(2024, 1, 15),  # two weeks back
    ]
    series = weekly_series(dates, weeks=4, now=now)
    assert len(series) == 4
    assert [c for _, c in series] == [0, 1, 0, 2]  # oldest → newest, zero-filled
    starts = [s for s, _ in series]
    assert starts == sorted(starts)  # strictly ascending


def test_weekly_series_ignores_out_of_window_dates():
    now = datetime(2024, 1, 31)
    series = weekly_series([datetime(2020, 1, 1)], weeks=4, now=now)
    assert sum(c for _, c in series) == 0


def test_sparkline_points_invert_y_and_scale():
    pts = sparkline_points([0, 4], width=100, height=40)  # pad=4 → inner 32
    coords = [tuple(map(float, p.split(","))) for p in pts.split()]
    assert coords[0] == (0.0, 36.0)  # zero → baseline (height - pad)
    assert coords[1] == (100.0, 4.0)  # max → top inset (pad)


def test_sparkline_points_all_zero_is_flat_baseline():
    pts = sparkline_points([0, 0, 0], width=120, height=40)
    ys = {float(p.split(",")[1]) for p in pts.split()}
    assert ys == {36.0}  # every point on the baseline


def test_sparkline_points_single_value_is_flat_line():
    pts = sparkline_points([5], width=200, height=40)
    coords = pts.split()
    assert len(coords) == 2  # a line needs two points
    assert coords[0].startswith("0,") and coords[1].startswith("200,")


def test_sparkline_points_empty():
    assert sparkline_points([]) == ""


def test_sparkline_view_model():
    sp = sparkline([1, 2, 3])
    assert sp.total == 6 and sp.latest == 3 and sp.weeks == 3
    assert sp.width == SPARK_WIDTH and sp.height == SPARK_HEIGHT
    # area polygon closes down to the baseline at both ends
    assert sp.area_points.startswith(f"0,{SPARK_HEIGHT} ")
    assert sp.area_points.endswith(f"{SPARK_WIDTH},{SPARK_HEIGHT}")
    # end marker sits on the last data point
    assert sp.end_x == SPARK_WIDTH


def test_sparkline_empty_series():
    sp = sparkline([])
    assert sp.total == 0 and sp.latest == 0 and sp.points == ""
    assert sp.area_points == ""


# --- Daily bucketing (per-card timelines) ---


def test_daily_series_zero_fills_and_orders():
    now = datetime(2024, 3, 20, 12, 0)
    dates = [
        datetime(2024, 3, 20, 8, 0),  # today (any time of day)
        datetime(2024, 3, 19, 23, 0),  # yesterday
        datetime(2024, 3, 19, 1, 0),  # yesterday
        datetime(2024, 3, 14, 12, 0),  # 6 days back — oldest bucket in a 7d window
    ]
    series = daily_series(dates, days=7, now=now)
    assert len(series) == 7
    assert [c for _, c in series] == [1, 0, 0, 0, 0, 2, 1]  # oldest → newest
    starts = [d for d, _ in series]
    assert starts == sorted(starts)  # strictly ascending
    assert starts[-1] == date(2024, 3, 20)  # window ends today


def test_daily_series_ignores_out_of_window_and_future_dates():
    now = datetime(2024, 3, 20, 12, 0)
    dates = [datetime(2024, 1, 1), datetime(2024, 3, 25)]  # far past + future
    series = daily_series(dates, days=7, now=now)
    assert sum(c for _, c in series) == 0


def test_daily_series_single_day_window():
    now = datetime(2024, 3, 20, 12, 0)
    series = daily_series([datetime(2024, 3, 20, 6, 0)], days=1, now=now)
    assert series == [(date(2024, 3, 20), 1)]


def test_marker_point_lands_on_the_sparkline():
    # A marker at index i must sit exactly on the i-th sparkline vertex.
    values = [0, 4, 2]
    pts = [tuple(map(float, p.split(","))) for p in sparkline_points(values).split()]
    for i in range(len(values)):
        assert marker_point(values, i) == pts[i]


def test_marker_point_scale():
    assert marker_point([0, 4], 0) == (0.0, 36.0)  # zero → baseline
    assert marker_point([0, 4], 1) == (SPARK_WIDTH, 4.0)  # max → top inset


def test_marker_point_single_value_sits_at_end():
    # A one-day series is a flat full-width line; its point is the right edge.
    x, y = marker_point([5], 0)
    assert x == float(SPARK_WIDTH)
    assert y == 4.0

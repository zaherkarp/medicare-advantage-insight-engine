"""Tests for weekly-trend bucketing and sparkline geometry."""

from datetime import date, datetime

from ma_signal_monitor.trends import (
    SPARK_HEIGHT,
    SPARK_WIDTH,
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

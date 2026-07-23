"""Tests for the Medicare Advantage regulatory calendar."""

from datetime import date

from ma_signal_monitor import ma_calendar


def test_aep_active_in_november():
    keys = {w.key for w in ma_calendar.active_windows(date(2024, 11, 15))}
    assert "aep" in keys


def test_february_sits_in_oep_and_advance_notice():
    keys = {w.key for w in ma_calendar.active_windows(date(2024, 2, 5))}
    assert "oep" in keys
    assert "advance_notice" in keys


def test_first_monday_june_is_bid_window():
    first_monday = ma_calendar._first_monday(2024, 6)
    assert first_monday == date(2024, 6, 3)
    keys = {w.key for w in ma_calendar.active_windows(first_monday)}
    assert "bid_submission" in keys


def test_summer_has_no_active_window():
    assert ma_calendar.active_windows(date(2024, 7, 23)) == []


def test_expected_categories_dampen_only_seasonal_topics():
    cats = ma_calendar.expected_categories_on(date(2024, 11, 15))  # AEP
    assert "membership_movement" in cats
    assert "policy_regulatory" not in cats  # off-cycle during AEP


def test_next_window_from_summer_is_star_ratings():
    window, start = ma_calendar.next_window(date(2024, 7, 23))
    assert window.key == "star_ratings"
    assert start > date(2024, 7, 23)


def test_next_window_crosses_year_boundary():
    window, start = ma_calendar.next_window(date(2024, 12, 20))
    assert start.year == 2025
    assert window.key in {"oep", "plan_year_start"}


def test_window_range_final_rate_brackets_first_monday_april():
    final_rate = next(w for w in ma_calendar.WINDOWS if w.key == "final_rate")
    start, end = ma_calendar.window_range(final_rate, 2024)
    assert start <= ma_calendar._first_monday(2024, 4) <= end


def test_span_label_reads_as_a_date_range():
    aep = next(w for w in ma_calendar.WINDOWS if w.key == "aep")
    assert ma_calendar.span_label(aep, 2024) == "Oct 15–Dec 7"


def test_active_windows_is_deterministic():
    d = date(2024, 11, 1)
    assert [w.key for w in ma_calendar.active_windows(d)] == [
        w.key for w in ma_calendar.active_windows(d)
    ]

"""The Medicare Advantage regulatory calendar — approximate cycle windows.

MA activity is strongly seasonal: enrollment marketing spikes during the Annual
Enrollment Period, rate and bid vocabulary clusters around the CMS rate cycle,
and Star Ratings land every October. Those spikes are *expected* — a flurry of
enrollment press releases in November is the season, not a step-change. This
module gives the briefing synthesis a way to say so: it maps a date to the
cycle windows active on it, and to the taxonomy categories whose elevated
volume is seasonal in each window, so the narrative can frame routine seasonal
volume as routine and reserve emphasis for genuinely off-cycle activity.

The dates are deliberately *approximate framing aids, not compliance dates* —
CMS shifts exact deadlines year to year and the synthesis only needs to know
roughly which season it is. They are kept as module data (like
``geo.STATE_NAMES`` and ``payers.PAYER_GROUPS``) rather than config: two windows
are rules ("the first Monday of June") that a static YAML cannot express, and no
per-deployment tuning is expected. Pure and dependency-free, so it is
unit-testable in isolation.
"""

from dataclasses import dataclass
from datetime import date, timedelta

# Category keys mirror config/taxonomy.yaml. Kept as bare strings (not imported)
# so this module stays dependency-free.


@dataclass(frozen=True)
class CalendarWindow:
    """One recurring Medicare-cycle window.

    ``start`` / ``end`` are *specs* resolved per-year by :func:`_resolve_spec`
    (so "the first Monday of June" stays a rule, not a hardcoded 2024 date):

    - ``("md", month, day)`` — a fixed month/day.
    - ``("first_monday", month, offset_days)`` — the first Monday of ``month``,
      shifted by ``offset_days`` (may be negative).

    ``short`` is a sentence-friendly subject ("AEP", "The MA bid window") used
    when the window is active; ``label`` is a noun phrase used in lists and the
    off-season "next milestone" note. ``expected_categories`` are the taxonomy
    keys whose elevated volume is seasonal — and therefore un-remarkable — while
    the window is active.
    """

    key: str
    label: str
    short: str
    start: tuple
    end: tuple
    expected_categories: frozenset[str]


# Ordered by first appearance in the calendar year. Every window resolves to a
# span inside a single year (none wrap December -> January), which keeps
# :func:`window_range` a plain ``(start, end)`` and lets :func:`next_window`
# handle the year boundary by scanning this year and next.
WINDOWS: tuple[CalendarWindow, ...] = (
    CalendarWindow(
        key="oep",
        label="Open Enrollment Period",
        short="OEP",
        start=("md", 1, 1),
        end=("md", 3, 31),
        expected_categories=frozenset(
            {"membership_movement", "brokerage_distribution"}
        ),
    ),
    CalendarWindow(
        key="plan_year_start",
        label="Plan-year start",
        short="The new plan year",
        start=("md", 1, 1),
        end=("md", 1, 7),
        expected_categories=frozenset({"membership_movement"}),
    ),
    CalendarWindow(
        key="advance_notice",
        label="Advance Notice release",
        short="The Advance Notice cycle",
        start=("md", 1, 25),
        end=("md", 2, 15),
        expected_categories=frozenset({"policy_regulatory", "financial_pressure"}),
    ),
    CalendarWindow(
        key="final_rate",
        label="Final Rate Announcement",
        short="The Final Rate cycle",
        start=("first_monday", 4, -7),
        end=("first_monday", 4, 7),
        expected_categories=frozenset({"policy_regulatory", "financial_pressure"}),
    ),
    CalendarWindow(
        key="bid_submission",
        label="Bid submission deadline",
        short="The MA bid window",
        start=("first_monday", 6, -10),
        end=("first_monday", 6, 1),
        expected_categories=frozenset(
            {"financial_pressure", "competitive_strategy", "policy_regulatory"}
        ),
    ),
    CalendarWindow(
        key="star_ratings",
        label="Star Ratings release",
        short="Star Ratings season",
        start=("md", 10, 1),
        end=("md", 10, 15),
        expected_categories=frozenset({"policy_regulatory", "competitive_strategy"}),
    ),
    CalendarWindow(
        key="aep",
        label="Annual Enrollment Period",
        short="AEP",
        start=("md", 10, 15),
        end=("md", 12, 7),
        expected_categories=frozenset(
            {"membership_movement", "brokerage_distribution"}
        ),
    ),
)


def _first_monday(year: int, month: int) -> date:
    """The first Monday on or after the 1st of ``month``."""
    first = date(year, month, 1)
    # Monday is weekday() == 0.
    return first + timedelta(days=(7 - first.weekday()) % 7)


def _resolve_spec(spec: tuple, year: int) -> date:
    """Resolve a date spec (see :class:`CalendarWindow`) to a concrete date."""
    kind = spec[0]
    if kind == "md":
        _, month, day = spec
        return date(year, month, day)
    if kind == "first_monday":
        _, month, offset = spec
        return _first_monday(year, month) + timedelta(days=offset)
    raise ValueError(f"Unknown date spec: {spec!r}")


def window_range(window: CalendarWindow, year: int) -> tuple[date, date]:
    """Resolve ``window`` to concrete ``(start, end)`` dates for ``year``."""
    return _resolve_spec(window.start, year), _resolve_spec(window.end, year)


def span_label(window: CalendarWindow, year: int) -> str:
    """Human span like ``"Oct 15–Dec 7"`` for ``window`` in ``year``."""
    start, end = window_range(window, year)
    return f"{start.strftime('%b %-d')}–{end.strftime('%b %-d')}"


def active_windows(d: date) -> list[CalendarWindow]:
    """Every window whose resolved ``[start, end]`` span contains ``d``.

    Windows are returned in calendar order (see :data:`WINDOWS`); a date can sit
    in more than one (e.g. the Advance Notice cycle inside OEP).
    """
    out = []
    for w in WINDOWS:
        start, end = window_range(w, d.year)
        if start <= d <= end:
            out.append(w)
    return out


def expected_categories_on(d: date) -> frozenset[str]:
    """Union of the seasonal categories across every window active on ``d``."""
    cats: set[str] = set()
    for w in active_windows(d):
        cats |= w.expected_categories
    return frozenset(cats)


def next_window(d: date) -> tuple[CalendarWindow, date] | None:
    """The soonest window that *starts* after ``d``, and its resolved start date.

    Scans this year and next so a late-December date correctly points at next
    year's January windows. Returns ``None`` only if :data:`WINDOWS` is empty.
    """
    best: tuple[date, CalendarWindow] | None = None
    for year in (d.year, d.year + 1):
        for w in WINDOWS:
            start, _ = window_range(w, year)
            if start > d and (best is None or start < best[0]):
                best = (start, w)
    if best is None:
        return None
    return best[1], best[0]

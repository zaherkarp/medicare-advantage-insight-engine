"""Swimlane geometry for the /timeline page.

Pure bucketing + placement math — no I/O and no config imports, modeled on
:mod:`ma_signal_monitor.trends` — so it is fully unit-testable. Markers are
placed on a shared day axis as (percentage x, pixel y) pairs and rendered as
absolutely-positioned HTML anchors rather than an SVG viewBox: a stretched
viewBox would squash circles into ellipses at narrow widths, while
percentage-positioned dots stay round at every viewport. Plain anchors with
``title`` tooltips need no scripting, so the chart ships to the static GitHub
Pages build untouched.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

DOT_R = 4  # story dot radius (px)
OVERFLOW_R = 5  # slightly larger stand-in dot for a collapsed stack (px)
DOT_PITCH = 10  # vertical spacing between stacked same-day dot centers (px)
LANE_PAD_Y = 7  # clearance between the outermost dots and the lane edges (px)
MAX_DOTS_PER_CELL = 4  # dots per lane/day cell before collapsing into "+N"
MIN_LANE_H = 30  # lane strip height with nothing (or one row) plotted (px)


@dataclass(frozen=True)
class LaneMarker:
    """One plotted dot: a story, or an overflow stand-in for several."""

    pct: float  # horizontal center as a percentage of the lane width
    bottom: float  # vertical center in px above the lane's bottom edge
    size: int  # rendered diameter (px)
    item_id: str
    title: str
    source_name: str
    day: str  # ISO date, for the tooltip
    overflow: int = 0  # 0 = a story dot; >0 = stands in for N hidden stories


@dataclass(frozen=True)
class Lane:
    """One swimlane: its label/link plus markers on the shared day axis."""

    key: str
    label: str
    href: str | None
    height: int  # lane strip height (px), sized to the tallest day stack
    markers: tuple[LaneMarker, ...]
    total: int  # stories plotted (incl. those collapsed behind overflow dots)


@dataclass(frozen=True)
class AxisTick:
    """One labeled day on the shared x axis (gridline + label position)."""

    index: int  # day index in the window (0 = oldest)
    pct: float  # horizontal center as a percentage of the lane width
    label: str  # e.g. "Jul 12"


def cell_pct(index: int, days: int) -> float:
    """Horizontal center (%) of the day column at ``index`` (0 = oldest)."""
    return round((index + 0.5) * 100.0 / days, 2)


def _tick_stride(days: int) -> int:
    """Days between axis labels — keeps every preset window at 5-8 ticks."""
    if days <= 10:
        return 1
    if days <= 20:
        return 2
    if days <= 70:
        return 7
    return 14


def axis_ticks(days: int, now: datetime) -> list[AxisTick]:
    """Labeled day ticks for a window ending on ``now``, oldest → newest.

    Anchored on the newest day and stepping back by the stride, so "today" is
    always labeled and the label cadence stays even for every preset window.
    """
    end = now.date()
    stride = _tick_stride(days)
    ticks: list[AxisTick] = []
    for index in range(days - 1, -1, -stride):
        day = end - timedelta(days=days - 1 - index)
        ticks.append(
            AxisTick(
                index=index,
                pct=cell_pct(index, days),
                label=f"{day.strftime('%b')} {day.day}",
            )
        )
    ticks.reverse()
    return ticks


def _lane_height(max_stack: int) -> int:
    """Lane strip height fitting the tallest visible same-day stack."""
    if max_stack <= 0:
        return MIN_LANE_H
    return max(MIN_LANE_H, 2 * LANE_PAD_Y + 2 * DOT_R + (max_stack - 1) * DOT_PITCH)


def build_lane(
    key: str,
    label: str,
    href: str | None,
    stories: list[dict],
    *,
    days: int,
    now: datetime,
) -> Lane:
    """Bucket ``stories`` into day cells and stack their markers bottom-up.

    Expects story dicts in the ``_story_view`` shape (``item_id``, ``title``,
    ``source_name``, ``event_date``, ``relevance_score``). Out-of-window and
    unparseable event dates are dropped — the same bucketing rule as
    :func:`ma_signal_monitor.trends.daily_series`. Same-day stories stack
    bottom-up sorted by relevance (strongest at the bottom); a cell deeper than
    ``MAX_DOTS_PER_CELL`` keeps its strongest stories and collapses the rest
    into one slightly larger overflow marker on top.
    """
    window_start = now.date() - timedelta(days=days - 1)
    cells: dict[int, list[dict]] = {}
    for s in stories:
        try:
            event_day = datetime.fromisoformat(s.get("event_date") or "").date()
        except (ValueError, TypeError):
            continue
        index = (event_day - window_start).days
        if 0 <= index < days:
            cells.setdefault(index, []).append(s)

    total = sum(len(bucket) for bucket in cells.values())
    max_stack = max(
        (min(len(bucket), MAX_DOTS_PER_CELL) for bucket in cells.values()),
        default=0,
    )
    height = _lane_height(max_stack)

    markers: list[LaneMarker] = []
    for index in sorted(cells):
        bucket = sorted(
            cells[index],
            key=lambda s: s.get("relevance_score") or 0.0,
            reverse=True,
        )
        pct = cell_pct(index, days)
        day_iso = (window_start + timedelta(days=index)).isoformat()
        visible = bucket
        overflow = 0
        if len(bucket) > MAX_DOTS_PER_CELL:
            visible = bucket[: MAX_DOTS_PER_CELL - 1]
            overflow = len(bucket) - len(visible)
        for i, s in enumerate(visible):
            markers.append(
                LaneMarker(
                    pct=pct,
                    bottom=float(LANE_PAD_Y + DOT_R + i * DOT_PITCH),
                    size=2 * DOT_R,
                    item_id=s.get("item_id", ""),
                    title=s.get("title", ""),
                    source_name=s.get("source_name", ""),
                    day=day_iso,
                )
            )
        if overflow:
            markers.append(
                LaneMarker(
                    pct=pct,
                    bottom=float(LANE_PAD_Y + DOT_R + len(visible) * DOT_PITCH),
                    size=2 * OVERFLOW_R,
                    item_id="",
                    title="",
                    source_name="",
                    day=day_iso,
                    overflow=overflow,
                )
            )

    return Lane(
        key=key,
        label=label,
        href=href,
        height=height,
        markers=tuple(markers),
        total=total,
    )

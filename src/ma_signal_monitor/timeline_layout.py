"""Layered-timeline geometry for the /timeline page.

Pure bucketing + placement math — no I/O, no config imports, and no color
literals (topic colors arrive via a ``color_map`` parameter) — so the whole
module is deterministic and unit-testable. It computes two stacked layers over
one shared day axis:

* a **callout band** — the window's strongest stories as topic-colored labeled
  cards along the top, each on a stem dropping to a dot on the axis; and
* a **topic strip** — one row per topic, one bubble per day, bubble *area*
  proportional to that day's story count.

Both layers are rendered as absolutely-positioned HTML (percentage x, pixel y)
rather than an SVG viewBox — the same choice, and for the same reasons, as the
swimlane chart this replaces: a stretched viewBox squashes circles into
ellipses at narrow widths, while percentage-anchored dots and bubbles stay
round at every viewport, and plain anchors with ``title`` tooltips need no
scripting, so the chart ships to the static GitHub Pages build untouched. The
callout band leans on the same property from the other direction: an HTML
label card wraps and ellipsizes its title/meta natively (``-webkit-line-clamp``,
``text-overflow``), where SVG ``<text>`` can do neither — so readable, variable
-length story labels are only practical in positioned DOM.

Layout is deterministic: at most one callout per day (highest relevance wins),
the top few day-winners by relevance, then a greedy pack into a fixed number of
rows with any non-fitting callout dropped and its count surfaced to the reader.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import sqrt

# --- Callout band ---
CALLOUT_ROWS = 3  # label rows stacked above the axis
# Label card width (%). COUPLED to `.callout { width: 24% }` in web/static/style.css —
# the layout math clamps/packs against this width, so the two MUST stay in sync.
CALLOUT_W_PCT = 24.0
CALLOUT_GAP_PCT = 2.0  # minimum horizontal gap (%) between two labels in a row
CALLOUT_ROW_H = 58  # vertical pitch (px) between callout rows
STEM_BASE = 14  # stem length (px) for row 0, and the empty-band height
MAX_CALLOUTS = 8  # most day-winners ever labeled

# --- Topic strip ---
STRIP_ROW_H = 26  # per-topic plot strip height (px)
BUBBLE_D_MAX = 22  # diameter (px) of a bubble at the shared max daily count
BUBBLE_D_MIN = 6  # floor diameter (px) so a one-story day stays visible

# --- Axis ---
AXIS_H = 24  # shared date-axis strip height (px)


@dataclass(frozen=True)
class AxisTick:
    """One labeled day on the shared x axis (gridline + label position)."""

    index: int  # day index in the window (0 = oldest)
    pct: float  # horizontal center as a percentage of the plot width
    label: str  # e.g. "Jul 12"


@dataclass(frozen=True)
class Callout:
    """One labeled story card: a stem at ``pct`` and a card at ``label_left_pct``."""

    pct: float  # stem/dot horizontal center (%)
    label_left_pct: float  # left edge of the label card (%), clamped into view
    row: int  # 0 = nearest the axis, growing upward
    stem_h: (
        int  # precomputed row * CALLOUT_ROW_H + STEM_BASE (px); see build_callout_band
    )
    item_id: str
    title: str
    source_name: str
    day: str  # ISO date, for the tooltip and meta line
    category_key: str  # primary_category (or "" when absent) — for debugging/tests
    color: str  # resolved topic color (via color_map / fallback)


@dataclass(frozen=True)
class CalloutBand:
    """The assembled callout layer: placed cards plus band metrics."""

    callouts: tuple[Callout, ...]
    rows_used: int  # highest occupied row + 1 (0 when empty)
    height: int  # rows_used * CALLOUT_ROW_H + STEM_BASE (STEM_BASE when empty)
    dropped: int  # day-winners that fit no row — surfaced as "+N more not labeled"


@dataclass(frozen=True)
class Bubble:
    """One day's bubble in a topic strip row (area ∝ count)."""

    pct: float  # horizontal center (%)
    size: int  # rendered diameter (px)
    count: int  # stories on this day in this topic
    day: str  # ISO date, for the tooltip


@dataclass(frozen=True)
class StripRow:
    """One topic's strip: its label/link plus per-day bubbles on the day axis."""

    key: str
    label: str
    href: str | None
    color: str  # topic color for every bubble in this row
    total: int  # stories in-window for this topic
    bubbles: tuple[Bubble, ...]


def cell_pct(index: int, days: int) -> float:
    """Horizontal center (%) of the day column at ``index`` (0 = oldest)."""
    return round((index + 0.5) * 100.0 / days, 2)


def _tick_stride(days: int) -> int:
    """Days between axis labels — keeps every preset window at 5-8 ticks.

    Presets 7/30/90/180/365 (and "all", clamped up to ~730) each land in the
    5-8 range: 7→7, 30→5, 90→7, 180→6, 365→7, 730→7.
    """
    if days <= 10:
        return 1
    if days <= 20:
        return 2
    if days <= 70:
        return 7
    if days <= 160:
        return 14
    if days <= 250:
        return 30
    if days <= 550:
        return 60
    return 120


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


def bubble_size(count: int, max_count: int) -> int:
    """Bubble diameter (px) for ``count`` against the strip's shared ``max_count``.

    Area — not diameter — is proportional to count: diameter scales with the
    square root of the count fraction, so a 4-story day renders twice the
    diameter (4× the area) of a 1-story day. Floored at ``BUBBLE_D_MIN`` so a
    lone story never vanishes; 0 for an empty/absent day.
    """
    if count <= 0 or max_count <= 0:
        return 0
    return max(BUBBLE_D_MIN, round(BUBBLE_D_MAX * sqrt(count / max_count)))


def _bucket_days(
    stories: list[dict], *, days: int, now: datetime
) -> dict[int, list[dict]]:
    """Bucket ``stories`` into day-index cells across the window.

    Expects the ``_story_view`` shape (``event_date`` an ISO string). Out-of-
    window and unparseable/absent dates are dropped — the same rule the
    swimlane chart this module replaces used.
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
    return cells


def build_callout_band(
    stories: list[dict],
    *,
    days: int,
    now: datetime,
    color_map: dict[str, str],
    fallback_color: str,
    limit: int = MAX_CALLOUTS,
) -> CalloutBand:
    """Pick and place the window's strongest stories as labeled callout cards.

    Story dicts are the ``_story_view`` shape (``item_id``, ``title``,
    ``source_name``, ``event_date``, ``relevance_score``, ``primary_category`` —
    any may be missing/None). Topic colors are looked up in ``color_map`` with
    ``fallback_color`` for absent/unknown keys, keeping this module free of any
    color literal.

    Pipeline:
      1. Bucket by day (out-of-window/garbage dates dropped).
      2. One winner per day: highest ``relevance_score`` (first wins on a tie).
      3. Rank winners by (higher relevance, then newer day); keep the top ``limit``.
      4. Greedy pack, sorted by stem x: each label card is ``CALLOUT_W_PCT`` wide,
         left-anchored and clamped into ``[0, 100 - CALLOUT_W_PCT]``; it takes the
         first row whose previous card ends ``CALLOUT_GAP_PCT`` before it, else it
         is dropped (``dropped`` count, surfaced as "+N more not labeled").

    ``Callout.stem_h`` (= ``row * CALLOUT_ROW_H + STEM_BASE``) is precomputed here
    so the template — which cannot import these constants — stays arithmetic-free
    and drives both the stem height and the card's ``bottom`` offset from it.
    """
    window_start = now.date() - timedelta(days=days - 1)
    cells = _bucket_days(stories, days=days, now=now)

    # One winner per day: highest relevance, first-wins on a tie (max keeps the
    # first maximal element in insertion order).
    winners: list[tuple[int, dict]] = [
        (index, max(bucket, key=lambda s: s.get("relevance_score") or 0.0))
        for index, bucket in cells.items()
    ]

    # Rank: higher relevance first, newer day (larger index) breaks ties.
    winners.sort(key=lambda iw: (-(iw[1].get("relevance_score") or 0.0), -iw[0]))
    selected = winners[:limit]

    # Greedy row packing, left → right by stem x (day index is monotonic in pct).
    selected.sort(key=lambda iw: iw[0])
    last_right = [float("-inf")] * CALLOUT_ROWS
    callouts: list[Callout] = []
    rows_used = 0
    dropped = 0
    for index, story in selected:
        pct = cell_pct(index, days)
        label_left = round(
            min(max(pct - CALLOUT_W_PCT / 2, 0.0), 100.0 - CALLOUT_W_PCT), 2
        )
        placed_row: int | None = None
        for row in range(CALLOUT_ROWS):
            if label_left >= last_right[row] + CALLOUT_GAP_PCT:
                placed_row = row
                last_right[row] = label_left + CALLOUT_W_PCT
                break
        if placed_row is None:
            dropped += 1
            continue
        rows_used = max(rows_used, placed_row + 1)
        key = story.get("primary_category")
        callouts.append(
            Callout(
                pct=pct,
                label_left_pct=label_left,
                row=placed_row,
                stem_h=placed_row * CALLOUT_ROW_H + STEM_BASE,
                item_id=story.get("item_id", ""),
                title=story.get("title", ""),
                source_name=story.get("source_name", ""),
                day=(window_start + timedelta(days=index)).isoformat(),
                category_key=key or "",
                color=color_map.get(key or "", fallback_color),
            )
        )

    return CalloutBand(
        callouts=tuple(callouts),
        rows_used=rows_used,
        height=rows_used * CALLOUT_ROW_H + STEM_BASE,
        dropped=dropped,
    )


def build_strip(
    groups: list[tuple[str, str, str | None, str, list[dict]]],
    *,
    days: int,
    now: datetime,
) -> list[StripRow]:
    """Build the per-topic bubble strip, with bubble areas comparable across rows.

    ``groups`` is ``[(key, label, href, color, stories)]`` in display order.
    Two passes: first bucket every group into per-day counts and find the
    ``max_count`` shared across all groups/days, then emit a :class:`StripRow`
    per group whose :class:`Bubble` sizes are all scaled to that shared max — so
    a 3-story day looks identical whichever topic it lands in, and topic volumes
    read comparably. Zero-count days emit no bubble; empty topics still render as
    a flat row (``total`` 0, no bubbles) because a quiet topic is itself signal.
    """
    window_start = now.date() - timedelta(days=days - 1)

    per_group_counts: list[dict[int, int]] = []
    max_count = 0
    for _key, _label, _href, _color, stories in groups:
        counts = {
            index: len(bucket)
            for index, bucket in _bucket_days(stories, days=days, now=now).items()
        }
        per_group_counts.append(counts)
        max_count = max(max_count, *counts.values()) if counts else max_count

    rows: list[StripRow] = []
    for (key, label, href, color, _stories), counts in zip(groups, per_group_counts):
        bubbles = tuple(
            Bubble(
                pct=cell_pct(index, days),
                size=bubble_size(counts[index], max_count),
                count=counts[index],
                day=(window_start + timedelta(days=index)).isoformat(),
            )
            for index in sorted(counts)
            if counts[index] > 0
        )
        rows.append(
            StripRow(
                key=key,
                label=label,
                href=href,
                color=color,
                total=sum(counts.values()),
                bubbles=bubbles,
            )
        )
    return rows

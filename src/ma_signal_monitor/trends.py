"""Weekly signal-volume trends rendered as inline-SVG sparklines.

Pure geometry + bucketing — no I/O, so it is fully unit-testable and safe to
reuse from both the web routes and (transitively) the static export. The SVG
these produce is plain ``<polyline>``/``<polygon>``/``<circle>`` with no
scripting, so it ships to the static GitHub Pages build untouched.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

# Sparkline canvas (viewBox units). Kept here so the storage/route layer and
# the template agree on geometry.
SPARK_WIDTH = 240
SPARK_HEIGHT = 40
_PAD = 4  # top/bottom inset so the 2px line and end-dot ring aren't clipped


def week_start(d: date | datetime) -> date:
    """The Monday of the ISO week containing ``d`` (as a naive date)."""
    base = d.date() if isinstance(d, datetime) else d
    return base - timedelta(days=base.weekday())


def weekly_series(
    dates: list[datetime], weeks: int, now: datetime
) -> list[tuple[date, int]]:
    """Bucket ``dates`` into the last ``weeks`` weekly buckets, oldest→newest.

    Every week in the window gets an entry (zero-filled), so a quiet week reads
    as a dip rather than vanishing. Dates outside the window are ignored.
    """
    end = week_start(now)
    starts = [end - timedelta(weeks=k) for k in range(weeks - 1, -1, -1)]
    counts: dict[date, int] = {s: 0 for s in starts}
    for d in dates:
        ws = week_start(d)
        if ws in counts:
            counts[ws] += 1
    return [(s, counts[s]) for s in starts]


def daily_series(
    dates: list[datetime], days: int, now: datetime
) -> list[tuple[date, int]]:
    """Bucket ``dates`` into the last ``days`` daily buckets, oldest→newest.

    The per-day counterpart to :func:`weekly_series`: every day in the trailing
    window ending on ``now`` gets an entry (zero-filled), so a quiet day reads
    as a dip rather than vanishing. Dates outside the window — including
    future-dated / misparsed ones — are ignored because their day key isn't a
    bucket.
    """
    end = now.date()
    starts = [end - timedelta(days=k) for k in range(days - 1, -1, -1)]
    counts: dict[date, int] = {s: 0 for s in starts}
    for d in dates:
        key = d.date() if isinstance(d, datetime) else d
        if key in counts:
            counts[key] += 1
    return [(s, counts[s]) for s in starts]


def _y_for(v: float, hi: int, height: int) -> float:
    """Invert + scale a value onto the padded canvas (SVG y grows downward)."""
    inner = height - 2 * _PAD
    return round(height - _PAD - (v / hi) * inner, 1)


def sparkline_points(
    values: list[int], width: int = SPARK_WIDTH, height: int = SPARK_HEIGHT
) -> str:
    """An SVG ``points`` string for a polyline over ``values`` (oldest→newest).

    Y is inverted (SVG y grows downward) and scaled to the series max, so the
    tallest week touches the top inset and an all-zero series is a flat line on
    the baseline. A single value renders as a flat line across the full width.
    """
    n = len(values)
    if n == 0:
        return ""
    hi = max(values) or 1
    if n == 1:
        y = _y_for(values[0], hi, height)
        return f"0,{y} {width},{y}"
    step = width / (n - 1)
    return " ".join(
        f"{round(i * step, 1)},{_y_for(v, hi, height)}" for i, v in enumerate(values)
    )


def marker_point(
    values: list[int],
    index: int,
    width: int = SPARK_WIDTH,
    height: int = SPARK_HEIGHT,
) -> tuple[float, float]:
    """The ``(x, y)`` of the point at ``index`` on the :func:`sparkline_points`
    line over ``values`` — so a marker drawn here lands exactly on the line.

    Callers guarantee ``0 <= index < len(values)``. A single-value series is a
    flat full-width line, so its only point sits at the right edge (coinciding
    with the end dot).
    """
    hi = max(values) or 1
    y = _y_for(values[index], hi, height)
    if len(values) == 1:
        return float(width), y
    step = width / (len(values) - 1)
    return round(index * step, 1), y


@dataclass(frozen=True)
class Sparkline:
    """Everything the ``_sparkline.html`` partial needs to render one trend."""

    points: str  # polyline points
    area_points: str  # polygon points (line + baseline corners) for the fill wash
    width: int
    height: int
    end_x: float  # last point — where the current-week marker sits
    end_y: float
    weeks: int
    total: int  # total signals across the window
    latest: int  # count in the most recent week


def sparkline(
    values: list[int], width: int = SPARK_WIDTH, height: int = SPARK_HEIGHT
) -> Sparkline:
    """Build a :class:`Sparkline` view model from weekly counts (oldest→newest)."""
    points = sparkline_points(values, width, height)
    coords = points.split()
    if coords:
        end_x, end_y = (float(c) for c in coords[-1].split(","))
        area_points = f"0,{height} {points} {width},{height}"
    else:
        end_x = end_y = 0.0
        area_points = ""
    return Sparkline(
        points=points,
        area_points=area_points,
        width=width,
        height=height,
        end_x=end_x,
        end_y=end_y,
        weeks=len(values),
        total=sum(values),
        latest=values[-1] if values else 0,
    )

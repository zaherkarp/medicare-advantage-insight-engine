"""The Daily Briefing synthesis lede — a policy-desk read of the window.

Sits above the per-story digest list and answers "what's happening" at altitude:
how much moved this window versus the previous one, which topic and which payers
lead, and — via :mod:`ma_calendar` — whether the volume is the expected shape of
the season (AEP enrollment noise, the spring rate cycle) or genuinely off-cycle.
Deadline-season flurries get framed as seasonal rather than amplified, which is
the whole point: a granular per-story list makes a routine November enrollment
flurry look like a step-change, and the lede is where we say it is not.

Pure data-in / data-out like :mod:`angles`: it takes the same facet dicts the
Angles page builds (the current window plus the same-length previous window) and
returns a small value object the digest templates render. No DB or HTTP here, so
it is unit-testable in isolation.
"""

from dataclasses import dataclass
from datetime import datetime

from ma_signal_monitor import ma_calendar
from ma_signal_monitor.classify import get_category_label
from ma_signal_monitor.config import AppConfig
from ma_signal_monitor.payers import ALIAS_TO_GROUP


@dataclass
class DigestLede:
    """The synthesis block rendered atop a Daily Briefing."""

    summary: str  # throughline: window count + momentum + leading topic/payers
    momentum: str  # "up" | "down" | "steady" | "new" (vs the prior window)
    total: int  # full-window signal count (uncapped, unlike Digest.story_count)
    prev_total: int
    season_note: str | None  # calendar framing (what's seasonal, or next milestone)
    offcycle_note: str | None  # what's genuinely off-cycle vs the active season
    breakdown: str  # "Membership Movement (5) · Policy / Regulatory (2)"


def _momentum(count: int, prev_count: int) -> str:
    """Label this window's volume against the previous window's.

    Mirrors :func:`ma_signal_monitor.angles._momentum` (copied so the two
    narrative surfaces read the same without a shared import).
    """
    if prev_count == 0:
        return "new"
    if count > prev_count:
        return "up"
    if count < prev_count:
        return "down"
    return "steady"


def _fold_categories(rows: list[dict]) -> dict[str, int]:
    """Count stories per primary category (matches the digest's own sections)."""
    counts: dict[str, int] = {}
    for r in rows:
        cat = r.get("primary_category") or "uncategorized"
        counts[cat] = counts.get(cat, 0) + 1
    return counts


def _fold_payers(rows: list[dict]) -> list[tuple[str, int]]:
    """Rank canonical payer groups named across ``rows`` (once per story).

    Mirrors :func:`ma_signal_monitor.angles._fold_payers`: aliases without a
    canonical group (agencies like CMS) are skipped.
    """
    counts: dict[str, int] = {}
    names: dict[str, str] = {}
    for r in rows:
        seen: set[str] = set()
        for alias in r.get("entities") or []:
            group = ALIAS_TO_GROUP.get(alias)
            if group is None or group.slug in seen:
                continue
            seen.add(group.slug)
            counts[group.slug] = counts.get(group.slug, 0) + 1
            names[group.slug] = group.name
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(names[slug], n) for slug, n in ranked]


def _humanize(cats: list[str], config: AppConfig) -> str:
    """Join category labels (lowercased) into an English list."""
    labels = [get_category_label(c, config).lower() for c in sorted(cats)]
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return ", ".join(labels[:-1]) + f", and {labels[-1]}"


def _season_note(
    active: list["ma_calendar.CalendarWindow"],
    expected: frozenset[str],
    present: set[str],
    today,
    config: AppConfig,
) -> str | None:
    """Frame the window against the Medicare calendar: seasonal vs off-cycle."""
    if active:
        primary = active[0]
        span = ma_calendar.span_label(primary, today.year)
        seasonal = [c for c in present if c in expected]
        if seasonal:
            return (
                f"{primary.short} is underway ({span}), so elevated "
                f"{_humanize(seasonal, config)} volume is seasonal — treat it as "
                f"expected, not a step-change."
            )
        return (
            f"{primary.short} is underway ({span}); none of this window's "
            f"signals fall in its usual seasonal categories."
        )
    nxt = ma_calendar.next_window(today)
    if nxt is None:
        return None
    window, start = nxt
    days = (start - today).days
    return (
        "No enrollment or rate-cycle window is active, so this is off-cycle "
        f"volume. Next milestone: {window.label}, {start.strftime('%B')} "
        f"(~{days} days out)."
    )


def _offcycle_note(
    active: list["ma_calendar.CalendarWindow"],
    expected: frozenset[str],
    cat_counts: dict[str, int],
    config: AppConfig,
) -> str | None:
    """Call out categories present but *not* seasonal for the active window."""
    if not active:
        return None
    offcycle = [c for c in cat_counts if c not in expected and c != "uncategorized"]
    if not offcycle:
        return None
    offcycle.sort(key=lambda c: (-cat_counts[c], c))
    parts = []
    for c in offcycle[:2]:
        n = cat_counts[c]
        label = get_category_label(c, config).lower()
        parts.append(f"{n} {label} signal{'' if n == 1 else 's'}")
    return (
        f"Off-cycle to watch: {' and '.join(parts)} — outside the active "
        f"window's seasonal pattern."
    )


def build_lede(
    current: list[dict],
    previous: list[dict],
    now: datetime,
    config: AppConfig,
) -> DigestLede | None:
    """Build the synthesis lede for a digest window.

    ``current`` / ``previous`` are facet dicts (see ``routes._facet_view``):
    each carries ``primary_category``, ``entities`` and friends. ``previous`` is
    the same-length window immediately before ``current``, used only for the
    momentum comparison. Returns ``None`` for an empty window so the templates
    fall back to their existing empty state.
    """
    if not current:
        return None

    total = len(current)
    prev_total = len(previous)
    momentum = _momentum(total, prev_total)

    cat_counts = _fold_categories(current)
    ordered = sorted(cat_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    lead_cat, lead_n = ordered[0]
    lead_label = get_category_label(lead_cat, config)

    if momentum == "up":
        move = f"up from {prev_total} in the prior window"
    elif momentum == "down":
        move = f"down from {prev_total} in the prior window"
    elif momentum == "steady":
        move = f"level with the prior window ({prev_total})"
    else:  # new
        move = "with no comparable prior window"

    sig = "signal" if total == 1 else "signals"
    summary = (
        f"{total} Medicare Advantage {sig} in the last "
        f"{config.digest_lookback_hours} hours, {move}. {lead_label} leads "
        f"({lead_n})."
    )
    payers = _fold_payers(current)
    if payers:
        summary += f" Most named: {', '.join(name for name, _ in payers[:2])}."

    today = now.date()
    active = ma_calendar.active_windows(today)
    expected = ma_calendar.expected_categories_on(today)
    present = set(cat_counts)

    breakdown = " · ".join(
        f"{get_category_label(cat, config)} ({n})" for cat, n in ordered
    )

    return DigestLede(
        summary=summary,
        momentum=momentum,
        total=total,
        prev_total=prev_total,
        season_note=_season_note(active, expected, present, today, config),
        offcycle_note=_offcycle_note(active, expected, cat_counts, config),
        breakdown=breakdown,
    )

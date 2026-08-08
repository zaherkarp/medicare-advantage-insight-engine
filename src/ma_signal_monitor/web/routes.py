"""HTTP routes for the MA Signal Monitor web frontend.

All handlers read from ``request.app.state`` (config, store, templates) so the
app is easy to construct in tests with a seeded database.
"""

import json
import math
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import quote_plus

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from ma_signal_monitor.angles import _causal_model_view, build_angles
from ma_signal_monitor.causal import edge_map
from ma_signal_monitor.geo import STATE_NAMES, state_name
from ma_signal_monitor.payers import (
    ALIAS_TO_GROUP,
    KIND_LABELS,
    PAYER_GROUPS,
    PayerGroup,
    get_group,
)
from ma_signal_monitor.query_parser import parse_query
from ma_signal_monitor.storage import VALID_VERDICTS
from ma_signal_monitor.threads import (
    build_thread_links,
    build_threads,
    select_threads_for_display,
    thread_bands,
)
from ma_signal_monitor.timeline_layout import (
    axis_ticks,
    build_callout_band,
    build_strip,
)
from ma_signal_monitor.topic_colors import (
    FALLBACK_TOPIC_COLOR,
    topic_color,
    topic_color_map,
)

SEC_SOURCE_PREFIX = "SEC EDGAR"

# Feed filter bar: keep the chip rows scannable — the /states and /payers
# overview pages remain the exhaustive directories.
MAX_FILTER_STATES = 8
MAX_FILTER_PAYERS = 6

# Angles rolling window (days): default, hard cap, and the picker presets.
ANGLES_DEFAULT_DAYS = 7
ANGLES_MAX_DAYS = 90
ANGLES_DAY_OPTIONS = (7, 14, 30)

# Story-card "related coverage" timelines: rolling daily window the reader can
# widen/narrow. Same clamp/default machinery as Angles, wider presets. Also
# the default/clamp for the /timeline page itself when no window preset/token
# applies (feed + search + payer-card pickers depend on these — never mutate).
TIMELINE_DEFAULT_DAYS = 30
TIMELINE_MAX_DAYS = 90
TIMELINE_DAY_OPTIONS = (7, 14, 30, 90)

# /timeline page-specific window scheme (D5): preset chips (plus the "all"
# token, handled separately) and the page's own — wider — clamp ceiling.
TIMELINE_WINDOW_PRESETS = (7, 30, 90, 180, 365)
TIMELINE_PAGE_MAX_DAYS = 365

# /timeline layered chart: cap the windowed fetch (a safety valve, cf. the
# static export's _MAX_STORY_PAGES); cap the story list rendered below the
# chart so the "All" window stays sane; cap the payer rows shown under a
# topic filter so the strip stays scannable.
TIMELINE_MAX_STORIES = 5000
TIMELINE_LIST_MAX = 100
MAX_PAYER_ROWS = 7


class FeedbackIn(BaseModel):
    """Body of a reader-feedback submission from the live web UI."""

    item_id: str
    verdict: str
    suggested_category: str | None = None
    comment: str | None = Field(default=None, max_length=2000)


def _story_view(row: sqlite3.Row) -> dict:
    """Turn a stories row into a template-friendly dict (JSON fields parsed)."""
    published = row["published_date"]
    display_date = ""
    if published:
        # Stored as ISO8601; show the date (and time if present).
        display_date = published.replace("T", " ")[:16]
    return {
        "item_id": row["item_id"],
        "title": row["title"],
        "link": row["link"],
        "source_name": row["source_name"],
        "summary": row["summary"] or "",
        "display_date": display_date,
        # Raw canonical time key (published, else fetched) — drives the
        # timeline's own-day marker; templates ignore the extra key.
        "event_date": row["published_date"] or row["fetched_at"] or "",
        "relevance_score": row["relevance_score"] or 0.0,
        "primary_category": row["primary_category"] or "uncategorized",
        "categories": json.loads(row["categories"] or "[]"),
        "entities": json.loads(row["entities"] or "[]"),
        "states": json.loads(row["states"] or "[]"),
        "public_draft": json.loads(row["public_draft"])
        if row["public_draft"]
        else None,
    }


def _facet_view(row: sqlite3.Row) -> dict:
    """Turn a lean facet row into an Angles-ready dict (JSON lenses parsed).

    Mirrors :func:`_story_view` for the columns the intersection engine reads,
    minus ``summary``/``public_draft`` — the ``get_recent_story_facets`` query
    doesn't fetch those blobs, so this view can't reference them.
    """
    published = row["published_date"]
    display_date = published.replace("T", " ")[:16] if published else ""
    return {
        "item_id": row["item_id"],
        "title": row["title"],
        "link": row["link"],
        "source_name": row["source_name"],
        "display_date": display_date,
        "relevance_score": row["relevance_score"] or 0.0,
        "primary_category": row["primary_category"] or "uncategorized",
        "categories": json.loads(row["categories"] or "[]"),
        "entities": json.loads(row["entities"] or "[]"),
        "states": json.loads(row["states"] or "[]"),
    }


def _candidate_view(row: sqlite3.Row) -> dict:
    """Turn a candidate_sources row into a template-friendly dict."""
    return {
        "id": row["id"],
        "feed_url": row["feed_url"],
        "domain": row["domain"],
        "feed_title": row["feed_title"] or row["domain"],
        "discovery_method": row["discovery_method"] or "",
        "times_seen": row["times_seen"],
        "relevance_score": row["relevance_score"] or 0.0,
        "status": row["status"],
        "last_seen": (row["last_seen_at"] or "")[:10],
    }


def _page_param(request: Request) -> int:
    """Parse a 1-based ?page= param, clamped to >= 1."""
    try:
        return max(1, int(request.query_params.get("page", "1")))
    except ValueError:
        return 1


def _days_param(request: Request, *, default: int, max_days: int) -> int:
    """Parse a ?days= rolling-window param, clamped to ``1..max_days``.

    Garbage (non-integer) falls back to ``default``; the clamp keeps an
    over-eager reader (or a crafted URL) from asking for an unbounded window.
    """
    try:
        days = int(request.query_params.get("days", str(default)))
    except ValueError:
        return default
    return min(max(days, 1), max_days)


def _window_param(request: Request, *, default: str) -> str:
    """Parse a /timeline ``?days=`` value, tolerating the "all" token.

    Anything else (garbage included) is handed to :func:`_resolve_window`,
    which clamps/defaults it the same way :func:`_days_param` does for the
    other rolling-window pickers.
    """
    raw = request.query_params.get("days", default)
    return "all" if raw == "all" else raw


def _resolve_window(
    value: str,
    store,
    *,
    category: str | None = None,
    state: str | None = None,
    entity_aliases: list[str] | None = None,
    min_score: float = 0.0,
) -> tuple[int, str, str]:
    """Resolve a /timeline window token/day-count to ``(days, token, label)``.

    ``value`` is either the literal "all" or a digit string, sourced from
    whichever wins: the ``/timeline/w/{token}`` path (see ``_render_timeline``)
    or a ``?days=`` query value. "all" resolves to the archive's true span
    *under the same scope filters* via :meth:`StateStore.get_oldest_story_key`
    (D6), clamped to ``[7, 730]`` days and labeled "all time"; an empty
    archive/scope falls back to a 7-day span rather than erroring. A numeric
    value clamps to ``1..TIMELINE_PAGE_MAX_DAYS`` and is labeled "the last N
    days" (singular-safe: "the last 1 day"). The returned ``token`` is the
    canonical string form (`"all"` or the clamped day count) — used both to
    mark the active picker chip and to round-trip the window into scoped
    ``?days=`` links.
    """
    if value == "all":
        oldest = store.get_oldest_story_key(
            category=category,
            state=state,
            entity_aliases=entity_aliases,
            min_score=min_score,
        )
        days = 7
        if oldest:
            try:
                span = (
                    datetime.utcnow().date() - datetime.fromisoformat(oldest).date()
                ).days + 1
                days = span
            except (ValueError, TypeError):
                pass
        days = min(max(days, 7), 730)
        return days, "all", "all time"
    try:
        days = int(value)
    except (TypeError, ValueError):
        days = TIMELINE_DEFAULT_DAYS
    days = min(max(days, 1), TIMELINE_PAGE_MAX_DAYS)
    return days, str(days), f"the last {days} day{'' if days == 1 else 's'}"


def _fold_entity_groups(entities: list[str]) -> tuple[list[PayerGroup], list[str]]:
    """Fold entity aliases into canonical payer groups (first-mention order).

    Returns ``(groups, loose_aliases)``: aliases without a group (e.g. agencies
    like CMS) are returned separately so callers can decide whether they stand
    on their own.
    """
    groups: list[PayerGroup] = []
    loose_aliases: list[str] = []
    seen_slugs: set[str] = set()
    for alias in entities:
        group = ALIAS_TO_GROUP.get(alias)
        if group is not None:
            if group.slug not in seen_slugs:
                seen_slugs.add(group.slug)
                groups.append(group)
        elif alias not in loose_aliases:
            loose_aliases.append(alias)
    return groups, loose_aliases


def _attach_timelines(
    stories: list[dict], store, config, *, days: int, now: datetime
) -> None:
    """Attach a per-card ``timeline`` view model to each story in-place.

    Each story's timeline plots how much *related* coverage exists over the last
    ``days`` days — a single story is one event, so the time axis only has
    meaning across the stories sharing its subject. Scope resolves to the
    canonical payer groups behind its entities (folded from aliases exactly like
    the payer pages, then matched by the full group alias set), falling back to
    its topic when it has no entities. A story with neither entities nor a real
    category gets ``timeline = None``.

    Series are computed once per unique scope and shared across cards (a page
    holds at most ``web_page_size`` stories, usually a handful of scopes), so a
    feed page issues only a few extra queries regardless of card count.
    """
    from ma_signal_monitor.classify import get_category_label
    from ma_signal_monitor.trends import marker_point, sparkline

    floor = config.archive_min_score
    valid_categories = {c.key for c in config.categories}
    window_start = now.date() - timedelta(days=days - 1)
    # Captions open the scope on the /timeline page at the card's own window.
    scope_qs = f"?days={days}" if days != TIMELINE_DEFAULT_DAYS else ""
    cache: dict[tuple, list[int]] = {}

    for s in stories:
        # Fold this story's entity aliases into canonical payer groups (order
        # preserved); aliases without a group (e.g. agencies like CMS) stand on
        # their own so they still get a coverage series, just no payer link.
        groups, loose_aliases = _fold_entity_groups(s.get("entities", []))

        if groups or loose_aliases:
            aliases: list[str] = []
            for group in groups:
                aliases.extend(group.aliases)
            aliases.extend(loose_aliases)
            key: tuple = ("e", *sorted(aliases))
            label = " + ".join([g.name for g in groups] + loose_aliases)
            # Link only when the scope is exactly one payer group — a co-mention
            # union or a lone agency alias has no single timeline to point at.
            href = (
                f"/timeline/payers/{groups[0].slug}{scope_qs}"
                if len(groups) == 1 and not loose_aliases
                else None
            )
            query_kwargs: dict = {"entity_aliases": aliases}
        else:
            category = s.get("primary_category", "uncategorized")
            if category == "uncategorized":
                s["timeline"] = None
                continue
            key = ("c", category)
            label = get_category_label(category, config)
            href = (
                f"/timeline/topics/{category}{scope_qs}"
                if category in valid_categories
                else None
            )
            query_kwargs = {"category": category}

        if key not in cache:
            counts = store.get_daily_counts(
                days=days, min_score=floor, now=now, **query_kwargs
            )
            cache[key] = [c["count"] for c in counts]
        values = cache[key]

        # Place a marker on the story's own day when it falls in the window.
        marker_x = marker_y = None
        try:
            event_day = datetime.fromisoformat(s.get("event_date") or "").date()
        except (ValueError, TypeError):
            event_day = None
        if event_day is not None:
            idx = (event_day - window_start).days
            if 0 <= idx < days:
                marker_x, marker_y = marker_point(values, idx)

        s["timeline"] = {
            "spark": sparkline(values),
            "days": days,
            "label": label,
            "href": href,
            "marker_x": marker_x,
            "marker_y": marker_y,
        }


def _timeline_strip_groups(
    stories: list[dict],
    config,
    *,
    mode: str,
    scope_qs: str,
    color_map: dict[str, str],
    scoped_category: str | None = None,
) -> list[tuple[str, str, str | None, str, list[dict]]]:
    """Partition window stories into topic-colored strip-row groups (C4).

    Returns the ``[(key, label, href, color, stories)]`` shape
    :func:`ma_signal_monitor.timeline_layout.build_strip` expects.
    ``mode="topics"`` gives one group per configured category (config order,
    all rendered even when quiet — a flat row is signal), plus a trailing
    "Other" group (:data:`FALLBACK_TOPIC_COLOR`) only when
    uncategorized/stale-key stories exist. ``mode="payers"`` (used under a
    topic filter, where topic rows would collapse to one) ranks the canonical
    payer groups active in the window, caps them at ``MAX_PAYER_ROWS``, and
    absorbs below-cap groups, ungrouped aliases (e.g. agencies like CMS), and
    entityless stories into "Other" — every row in this mode wears
    ``scoped_category``'s single color, since the row dimension flipped to
    payers but the color identity stays the scoping topic's. Every story lands
    in exactly one group, so the chart total matches the list beneath it; a
    multi-payer co-mention plots once, in its first-mentioned group.
    """
    groups: list[tuple[str, str, str | None, str, list[dict]]] = []
    if mode == "payers":
        color = topic_color(scoped_category, color_map)
        by_group: dict[str, list[dict]] = {}
        other: list[dict] = []
        for s in stories:
            payer_groups, _loose = _fold_entity_groups(s.get("entities", []))
            if payer_groups:
                by_group.setdefault(payer_groups[0].slug, []).append(s)
            else:
                other.append(s)
        ranked = sorted(by_group.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        for slug, bucket in ranked[:MAX_PAYER_ROWS]:
            group = get_group(slug)
            groups.append(
                (
                    slug,
                    group.name if group else slug,
                    f"/timeline/payers/{slug}{scope_qs}",
                    color,
                    bucket,
                )
            )
        for _slug, bucket in ranked[MAX_PAYER_ROWS:]:
            other.extend(bucket)
        if other:
            groups.append(("other", "Other", None, color, other))
    else:
        by_cat: dict[str, list[dict]] = {}
        for s in stories:
            by_cat.setdefault(s.get("primary_category", "uncategorized"), []).append(s)
        valid = set()
        for cat in config.categories:
            valid.add(cat.key)
            groups.append(
                (
                    cat.key,
                    cat.label,
                    f"/timeline/topics/{cat.key}{scope_qs}",
                    color_map.get(cat.key, FALLBACK_TOPIC_COLOR),
                    by_cat.get(cat.key, []),
                )
            )
        leftovers = [
            s for key, bucket in by_cat.items() if key not in valid for s in bucket
        ]
        if leftovers:
            groups.append(("other", "Other", None, FALLBACK_TOPIC_COLOR, leftovers))
    return groups


def _timeline_window_stories(
    store,
    *,
    category: str | None = None,
    state: str | None = None,
    entity_aliases: list[str] | None = None,
    min_score: float,
    window_start,
    now: datetime,
    limit: int = TIMELINE_MAX_STORIES,
) -> list[dict]:
    """Fetch and date-filter one windowed story list, ``_story_view``-shaped.

    Shared by :func:`_render_timeline`, the ``/timeline/threads/{key}`` detail
    route, and ``static_export.py`` — all three need exactly the same window a
    reader would see, so the fetch + date filter live here once rather than in
    three copies that could silently drift apart. ``since`` bounds the store
    query on the left; each row's own event day is then re-checked against
    ``[window_start, now.date()]`` so a bad/future-dated story never leaks
    into the chart or the list beneath it (the same rule
    :mod:`timeline_layout`'s bucketing applies).
    """
    rows = store.get_stories(
        category=category,
        state=state,
        entity_aliases=entity_aliases,
        min_score=min_score,
        since=window_start.isoformat(),
        limit=limit,
    )
    stories = []
    for r in rows:
        s = _story_view(r)
        try:
            event_day = datetime.fromisoformat(s["event_date"]).date()
        except (ValueError, TypeError):
            continue
        if window_start <= event_day <= now.date():
            stories.append(s)
    return stories


def _default_window_threads(store, config) -> tuple[list, list[dict]]:
    """Recluster the timeline's default window into threads.

    Threads aren't persisted — :func:`threads.build_threads` recomputes them
    fresh per call — so the thread-detail route and the static export (which
    can't carry a live ``?days=`` query) both need to reproduce exactly the
    window the un-scoped ``/timeline/threads`` lane clusters at its default
    lookback (``TIMELINE_DEFAULT_DAYS``, ignoring any ``?days=`` a reader
    might have applied when they clicked through — the same reason a thread
    row's href carries no ``scope_qs``, unlike the topic/payer strip rows).
    This is the one place that resolution happens, so the two call sites
    can't drift. Returns the same ``(threads, ungrouped)`` shape as
    ``build_threads``.
    """
    now = datetime.utcnow()
    window_start = now.date() - timedelta(days=TIMELINE_DEFAULT_DAYS - 1)
    stories = _timeline_window_stories(
        store,
        min_score=config.archive_min_score,
        window_start=window_start,
        now=now,
    )
    return build_threads(
        stories,
        config,
        threshold=config.thread_similarity_threshold,
        min_stories=config.thread_min_stories,
    )


def _thread_links_view(threads: list, config) -> dict[str, dict]:
    """Row-keyed "leads to" chip data: ``{source_key: {key, label, href}}``.

    Thin view layer over :func:`threads.build_thread_links` — resolves each
    surviving target key to the thread it names (for its label) and the page
    it links to, the same ``/timeline/threads/{key}`` URL every other thread
    reference on this page uses. Shared by the lane (forward chips) and the
    thread-detail route (which inverts it for the reciprocal "caused by"
    back-link) so the link rule is computed in exactly one place.
    """
    links = build_thread_links(threads, edge_map(config))
    by_key = {t.key: t for t in threads}
    return {
        source: {
            "key": target,
            "label": by_key[target].label,
            "href": f"/timeline/threads/{target}",
        }
        for source, target in links.items()
    }


# Sentinel row keys for the threads lane's two trailing aggregate rows.
# Neither can ever collide with a real thread's key (a real story's
# ``item_id``), so equality against these is a safe way to spot them —
# shared between ``_timeline_thread_groups`` (which emits the rows) and
# ``_render_timeline`` (which uses them to mark the rows for muted styling;
# see ``style.css``'s ``.strip-row-muted``).
THREAD_SMALLER_ROWS_KEY = "smaller-threads"
THREAD_UNGROUPED_KEY = "ungrouped"


def _timeline_thread_groups(
    stories: list[dict],
    config,
    *,
    color_map: dict[str, str],
) -> tuple[
    list[tuple[str, str, str | None, str, list[dict]]],
    list[dict],
    dict[int, str],
    dict[str, dict],
]:
    """Emergent-thread strip groups for the /timeline/threads lane.

    Clusters the window into on-the-fly threads (:func:`threads.build_threads`),
    then caps how many the CHART draws (:func:`threads.select_threads_for_display`,
    ``config.thread_max_rows``) and emits the ``(key, label, href, color,
    stories)`` shape :func:`ma_signal_monitor.timeline_layout.build_strip`
    expects — one row per *kept* thread, colored by its dominant topic and
    ordered along the causal cascade, then up to two trailing aggregate rows
    so every story still lands in exactly one row and the chart total still
    matches the list:

    * ``"+N smaller threads"`` (:data:`THREAD_SMALLER_ROWS_KEY`) — only when
      the cap actually folded threads. Its ``stories`` is the union of every
      folded thread's stories, so the strip still plots them and
      ``strip-count`` still shows the right total; it is never a real
      thread's identity, so it links nowhere (``href=None``) even though
      each folded thread still has its own reachable detail page (the
      static export writes one for every thread, capped or not).
    * ``"Ungrouped signals"`` (:data:`THREAD_UNGROUPED_KEY`) — the stories
      that formed no thread at all, unchanged from before this cap existed.

    Each *kept* thread row links to its own page (``/timeline/threads/{key}``
    — see ``timeline_thread``), keyed on the thread's anchor so the link
    survives re-clustering; both trailing aggregate rows have no such
    identity, so they stay unlinked.

    Returns ``(groups, legend, strip_bands, thread_links)``:

    * ``legend`` describes each *kept* thread with its causal layer, or
      "Mixed" when the thread spans several categories with no clear
      majority (:attr:`threads.Thread.mixed`) — folded threads get no swatch,
      the same reason they get no chart row: a legend entry per folded
      thread would be exactly as unreadable as the row it stands in for.
    * ``strip_bands`` (:func:`threads.thread_bands`) is the causal-layer band
      header data, row-index-keyed to line up with ``groups``/the strip
      ``build_strip`` renders from it — threads-lane only; the topics strip
      never gets one (callers just never build/pass this for that mode).
    * ``thread_links`` (:func:`_thread_links_view`) is the row-keyed "leads
      to" chip data, computed over the *kept* set only — a chip's purpose is
      to connect two visible rows, so it must never point at a folded
      thread's row (which doesn't exist on this page) even though that
      thread's own detail page is real and reachable by URL.
    """
    threads, ungrouped = build_threads(
        stories,
        config,
        threshold=config.thread_similarity_threshold,
        min_stories=config.thread_min_stories,
    )
    kept, folded = select_threads_for_display(threads, config.thread_max_rows)
    groups: list[tuple[str, str, str | None, str, list[dict]]] = []
    legend: list[dict] = []
    for t in kept:
        color = (
            topic_color(t.dominant_category, color_map)
            if t.dominant_category
            else FALLBACK_TOPIC_COLOR
        )
        groups.append(
            (t.key, t.label, f"/timeline/threads/{t.key}", color, list(t.stories))
        )
        legend.append(
            {
                "key": t.key,
                "label": t.label,
                "color": color,
                "total": t.total,
                "layer": t.layer_label,
                "mixed": t.mixed,
            }
        )
    if folded:
        folded_stories = [s for t in folded for s in t.stories]
        groups.append(
            (
                THREAD_SMALLER_ROWS_KEY,
                f"+{len(folded)} smaller threads",
                None,
                FALLBACK_TOPIC_COLOR,
                folded_stories,
            )
        )
    if ungrouped:
        groups.append(
            (
                THREAD_UNGROUPED_KEY,
                "Ungrouped signals",
                None,
                FALLBACK_TOPIC_COLOR,
                ungrouped,
            )
        )
    strip_bands = thread_bands(
        kept, has_ungrouped=bool(ungrouped), has_folded=bool(folded)
    )
    thread_links = _thread_links_view(kept, config)
    return groups, legend, strip_bands, thread_links


def register_routes(app: FastAPI, templates: Jinja2Templates) -> None:
    """Register all frontend routes on the app."""

    def _feed_filters(
        request: Request,
        *,
        active_category: str | None = None,
        active_state: str | None = None,
        active_payer: str | None = None,
    ) -> dict:
        """Context for the feed's filter bar (topics/states/payers as tags).

        State and payer chips are trimmed to the most active so the bar stays
        scannable. The active state/payer is prepended when it falls outside
        the top slice so its highlighted chip is always visible.
        """
        store = request.app.state.store
        config = request.app.state.config
        floor = config.archive_min_score

        state_counts = store.get_state_counts(min_score=floor)
        top_states = sorted(state_counts.items(), key=lambda kv: (-kv[1], kv[0]))[
            :MAX_FILTER_STATES
        ]
        if active_state and active_state not in {code for code, _ in top_states}:
            top_states.insert(0, (active_state, state_counts.get(active_state, 0)))

        # Fold granular entity aliases into canonical payer groups; aliases
        # without a group (e.g. agencies) don't have a payer page to link.
        group_counts: dict[str, int] = {}
        for alias, n in store.get_entity_counts(min_score=floor).items():
            group = ALIAS_TO_GROUP.get(alias)
            if group is not None:
                group_counts[group.slug] = group_counts.get(group.slug, 0) + n
        top_payers = [
            {"slug": slug, "name": get_group(slug).name, "count": n}
            for slug, n in sorted(group_counts.items(), key=lambda kv: (-kv[1], kv[0]))[
                :MAX_FILTER_PAYERS
            ]
        ]
        if active_payer and active_payer not in {p["slug"] for p in top_payers}:
            group = get_group(active_payer)
            if group is not None:
                top_payers.insert(
                    0,
                    {
                        "slug": group.slug,
                        "name": group.name,
                        "count": group_counts.get(group.slug, 0),
                    },
                )

        return {
            "active_category": active_category,
            "active_state": active_state,
            "active_payer": active_payer,
            "states": [{"code": code, "count": n} for code, n in top_states],
            "payers": top_payers,
        }

    def _render_feed(
        request: Request,
        *,
        heading: str,
        subtitle: str,
        base_path: str,
        category: str | None = None,
        state: str | None = None,
    ) -> HTMLResponse:
        store = request.app.state.store
        config = request.app.state.config
        page_size = config.web_page_size
        page = _page_param(request)
        days = _days_param(
            request, default=TIMELINE_DEFAULT_DAYS, max_days=TIMELINE_MAX_DAYS
        )
        floor = config.archive_min_score

        total = store.count_stories(category=category, state=state, min_score=floor)
        total_pages = max(1, math.ceil(total / page_size)) if total else 1
        page = min(page, total_pages)
        rows = store.get_stories(
            category=category,
            state=state,
            limit=page_size,
            offset=(page - 1) * page_size,
            min_score=floor,
        )
        stories = [_story_view(r) for r in rows]
        now = datetime.utcnow()
        _attach_timelines(stories, store, config, days=days, now=now)
        return templates.TemplateResponse(
            request,
            "feed.html",
            {
                "heading": heading,
                "subtitle": subtitle,
                "stories": stories,
                "total": total,
                "page": page,
                "total_pages": total_pages,
                "base_path": base_path,
                "days": days,
                "day_options": TIMELINE_DAY_OPTIONS,
                "days_qs": f"&days={days}" if days != TIMELINE_DEFAULT_DAYS else "",
                "days_base": f"{base_path}?",
                "filters": _feed_filters(
                    request, active_category=category, active_state=state
                ),
            },
        )

    @app.get("/", response_class=HTMLResponse)
    def feed(request: Request) -> HTMLResponse:
        return _render_feed(
            request,
            heading="Medicare Advantage Signal Feed",
            subtitle="Latest scored signals across all monitored sources.",
            base_path="/",
        )

    @app.get("/topics/{category_key}", response_class=HTMLResponse)
    def topic(request: Request, category_key: str) -> HTMLResponse:
        config = request.app.state.config
        valid = {c.key for c in config.categories}
        if category_key not in valid:
            raise HTTPException(status_code=404, detail="Unknown topic")
        from ma_signal_monitor.classify import get_category_label

        label = get_category_label(category_key, config)
        return _render_feed(
            request,
            heading=label,
            subtitle="Signals classified into this topic vertical.",
            base_path=f"/topics/{category_key}",
            category=category_key,
        )

    @app.get("/search", response_class=HTMLResponse)
    def search(request: Request) -> HTMLResponse:
        store = request.app.state.store
        config = request.app.state.config
        query = (request.query_params.get("q") or "").strip()
        page = _page_param(request)
        days = _days_param(
            request, default=TIMELINE_DEFAULT_DAYS, max_days=TIMELINE_MAX_DAYS
        )
        page_size = config.web_page_size

        stories: list[dict] = []
        total = 0
        total_pages = 1
        if query:
            floor = config.archive_min_score
            total = store.count_search(query, min_score=floor)
            total_pages = max(1, math.ceil(total / page_size)) if total else 1
            page = min(page, total_pages)
            rows = store.search_stories(
                query,
                limit=page_size,
                offset=(page - 1) * page_size,
                min_score=floor,
            )
            stories = [_story_view(r) for r in rows]
            _attach_timelines(stories, store, config, days=days, now=datetime.utcnow())

        base_path = f"/search?q={quote_plus(query)}&"
        return templates.TemplateResponse(
            request,
            "search.html",
            {
                "query": query,
                "stories": stories,
                "total": total,
                "page": page,
                "total_pages": total_pages,
                "base_path": base_path,
                "days": days,
                "day_options": TIMELINE_DAY_OPTIONS,
                "days_qs": f"&days={days}" if days != TIMELINE_DEFAULT_DAYS else "",
                "days_base": base_path,
            },
        )

    @app.get("/ask", response_class=HTMLResponse)
    def ask(request: Request) -> HTMLResponse:
        """Natural-language query over the archive.

        Parses a plain question into the same structured filters /search and
        the topic/state/payer pages already use (see query_parser.py), then
        reads the archive. Read-only — never touches scoring, thresholds, or
        the ingestion pipeline.
        """
        store = request.app.state.store
        config = request.app.state.config
        question = (request.query_params.get("q") or "").strip()
        page = _page_param(request)
        page_size = config.web_page_size

        parsed = None
        stories: list[dict] = []
        total = 0
        total_pages = 1
        if question:
            parsed = parse_query(question, config)
            filter_kwargs = {
                "category": parsed.category,
                "state": parsed.state,
                "min_score": parsed.min_score,
                "entity_aliases": parsed.entity_aliases or None,
                "since": parsed.since,
            }
            if parsed.keywords:
                total = store.count_search_filtered(parsed.keywords, **filter_kwargs)
            else:
                total = store.count_stories(**filter_kwargs)
            total_pages = max(1, math.ceil(total / page_size)) if total else 1
            page = min(page, total_pages)
            offset = (page - 1) * page_size
            if parsed.keywords:
                rows = store.search_stories_filtered(
                    parsed.keywords, limit=page_size, offset=offset, **filter_kwargs
                )
            else:
                rows = store.get_stories(
                    limit=page_size, offset=offset, **filter_kwargs
                )
            stories = [_story_view(r) for r in rows]

        base_path = f"/ask?q={quote_plus(question)}&"
        return templates.TemplateResponse(
            request,
            "ask.html",
            {
                "question": question,
                "parsed": parsed,
                "stories": stories,
                "total": total,
                "page": page,
                "total_pages": total_pages,
                "base_path": base_path,
            },
        )

    @app.get("/sources", response_class=HTMLResponse)
    def sources(request: Request) -> HTMLResponse:
        config = request.app.state.config
        store = request.app.state.store
        default_cadence = f"every {config.ingest_interval_hours}h"

        from ma_signal_monitor.source_health import flag_silent_sources

        health = store.get_source_fetch_health(
            lookback_days=max(60, config.source_silent_days * 2)
        )
        silent = {f["source_name"]: f for f in flag_silent_sources(health, config)}

        # Group by the first tag for a tidy directory layout.
        groups: dict[str, list] = {}
        for s in config.sources:
            group = (s.tags[0] if s.tags else "other").title()
            view = {
                "name": s.name,
                "type": s.type,
                "enabled": s.enabled,
                "priority": s.priority,
                "tags": s.tags,
                "state": s.state,
                "geography": s.geography,
                "cadence": s.cadence or default_cadence,
                "description": s.description,
                "homepage": s.homepage or s.url,
                "silent_reason": silent.get(s.name, {}).get("reason"),
            }
            groups.setdefault(group, []).append(view)
        return templates.TemplateResponse(
            request,
            "sources.html",
            {
                "groups": groups,
                "default_cadence": default_cadence,
                "source_count": len(config.sources),
                "enabled_count": sum(1 for s in config.sources if s.enabled),
                "silent_count": len(silent),
            },
        )

    @app.get("/candidates", response_class=HTMLResponse)
    def candidates(request: Request) -> HTMLResponse:
        store = request.app.state.store
        config = request.app.state.config
        page_size = config.web_page_size
        page = _page_param(request)
        status = request.query_params.get("status") or None

        total = store.count_candidate_sources(status=status)
        total_pages = max(1, math.ceil(total / page_size)) if total else 1
        page = min(page, total_pages)
        rows = store.list_candidate_sources(
            status=status, limit=page_size, offset=(page - 1) * page_size
        )
        base_path = f"/candidates?status={status}&" if status else "/candidates?"
        return templates.TemplateResponse(
            request,
            "candidates.html",
            {
                "candidates": [_candidate_view(r) for r in rows],
                "status": status,
                "total": total,
                "page": page,
                "total_pages": total_pages,
                "base_path": base_path,
                "discovery_enabled": config.discovery_enabled,
            },
        )

    @app.get("/states", response_class=HTMLResponse)
    def states(request: Request) -> HTMLResponse:
        store = request.app.state.store
        config = request.app.state.config
        counts = store.get_state_counts(min_score=config.archive_min_score)
        # Sources explicitly tagged to a state (i.e. not national).
        state_sources: dict[str, list] = {}
        for s in config.sources:
            if s.state and s.state != "national":
                state_sources.setdefault(s.state, []).append(s.name)
        # Build a sorted list of states that have either stories or sources.
        codes = sorted(
            set(counts) | set(state_sources),
            key=lambda c: counts.get(c, 0),
            reverse=True,
        )
        rows = [
            {
                "code": c,
                "name": state_name(c),
                "story_count": counts.get(c, 0),
                "sources": state_sources.get(c, []),
            }
            for c in codes
        ]
        return templates.TemplateResponse(
            request,
            "states.html",
            {
                "states": rows,
                "all_states": STATE_NAMES,
            },
        )

    @app.get("/states/{code}", response_class=HTMLResponse)
    def state_detail(request: Request, code: str) -> HTMLResponse:
        code = code.upper()
        if code not in STATE_NAMES:
            raise HTTPException(status_code=404, detail="Unknown state")
        return _render_feed(
            request,
            heading=f"State Intelligence — {state_name(code)}",
            subtitle="Signals referencing this state across all sources.",
            base_path=f"/states/{code}",
            state=code,
        )

    @app.get("/payers", response_class=HTMLResponse)
    def payers(request: Request) -> HTMLResponse:
        store = request.app.state.store
        config = request.app.state.config
        alias_counts = store.get_entity_counts(min_score=config.archive_min_score)
        sections = []
        for kind, kind_label in KIND_LABELS.items():
            groups = [
                {
                    "slug": g.slug,
                    "name": g.name,
                    "story_count": sum(alias_counts.get(a, 0) for a in g.aliases),
                }
                for g in PAYER_GROUPS
                if g.kind == kind
            ]
            groups.sort(key=lambda g: g["story_count"], reverse=True)
            sections.append({"label": kind_label, "groups": groups})
        return templates.TemplateResponse(
            request, "payers.html", {"sections": sections}
        )

    @app.get("/payers/{slug}", response_class=HTMLResponse)
    def payer_detail(request: Request, slug: str) -> HTMLResponse:
        group = get_group(slug)
        if group is None:
            raise HTTPException(status_code=404, detail="Unknown payer")
        store = request.app.state.store
        config = request.app.state.config
        floor = config.archive_min_score
        aliases = list(group.aliases)

        page_size = config.web_page_size
        page = _page_param(request)
        days = _days_param(
            request, default=TIMELINE_DEFAULT_DAYS, max_days=TIMELINE_MAX_DAYS
        )
        total = store.count_stories(entity_aliases=aliases, min_score=floor)
        total_pages = max(1, math.ceil(total / page_size)) if total else 1
        page = min(page, total_pages)
        rows = store.get_stories(
            entity_aliases=aliases,
            limit=page_size,
            offset=(page - 1) * page_size,
            min_score=floor,
        )
        stories = [_story_view(r) for r in rows]
        _attach_timelines(stories, store, config, days=days, now=datetime.utcnow())

        from ma_signal_monitor.classify import get_category_label

        stats = store.get_entity_stats(aliases, min_score=floor)
        category_mix = sorted(
            (
                {
                    "key": key,
                    "label": get_category_label(key, config),
                    "count": count,
                }
                for key, count in stats["categories"].items()
                if key != "uncategorized"
            ),
            key=lambda c: c["count"],
            reverse=True,
        )
        state_footprint = sorted(
            (
                {"code": code, "name": state_name(code), "count": count}
                for code, count in stats["states"].items()
            ),
            key=lambda s: s["count"],
            reverse=True,
        )[:10]
        sec_filings = [
            _story_view(r)
            for r in store.get_stories(
                entity_aliases=aliases,
                source_prefix=SEC_SOURCE_PREFIX,
                limit=5,
                min_score=floor,
            )
        ]
        from ma_signal_monitor.trends import sparkline

        trend = store.get_weekly_counts(
            weeks=12, entity_aliases=aliases, min_score=floor
        )
        spark = sparkline([w["count"] for w in trend])
        return templates.TemplateResponse(
            request,
            "payer.html",
            {
                "payer": {"slug": group.slug, "name": group.name},
                "aliases": aliases,
                "stories": stories,
                "total": total,
                "page": page,
                "total_pages": total_pages,
                "base_path": f"/payers/{group.slug}",
                "days": days,
                "day_options": TIMELINE_DAY_OPTIONS,
                "days_qs": f"&days={days}" if days != TIMELINE_DEFAULT_DAYS else "",
                "days_base": f"/payers/{group.slug}?",
                "category_mix": category_mix,
                "state_footprint": state_footprint,
                "sec_filings": sec_filings,
                "spark": spark,
            },
        )

    def _render_briefing(request: Request, digest_row) -> HTMLResponse:
        store = request.app.state.store
        return templates.TemplateResponse(
            request,
            "briefing.html",
            {"digest": digest_row, "archive": store.list_digests(limit=30)},
        )

    @app.get("/briefing", response_class=HTMLResponse)
    def briefing(request: Request) -> HTMLResponse:
        store = request.app.state.store
        return _render_briefing(request, store.get_latest_digest())

    @app.get("/briefing/{digest_date}", response_class=HTMLResponse)
    def briefing_by_date(request: Request, digest_date: str) -> HTMLResponse:
        store = request.app.state.store
        row = store.get_digest(digest_date)
        if row is None:
            raise HTTPException(status_code=404, detail="Briefing not found")
        return _render_briefing(request, row)

    @app.get("/angles", response_class=HTMLResponse)
    def angles(request: Request) -> HTMLResponse:
        """Lens-intersection angles mined from a rolling signal window."""
        store = request.app.state.store
        config = request.app.state.config
        days = _days_param(
            request, default=ANGLES_DEFAULT_DAYS, max_days=ANGLES_MAX_DAYS
        )

        now = datetime.utcnow()
        window_start = now - timedelta(days=days)
        prev_start = now - timedelta(days=2 * days)
        floor = config.archive_min_score
        # Bound the current window on the right (`until=now`) so it matches the
        # previous window's shape — a future-dated story can't inflate every
        # window and permanently bias an overlap's momentum. The facet query is
        # uncapped, so counts and intersections see the whole window.
        current = [
            _facet_view(r)
            for r in store.get_recent_story_facets(
                window_start, min_score=floor, until=now
            )
        ]
        # The same-length window immediately before, for the momentum labels.
        previous = [
            _facet_view(r)
            for r in store.get_recent_story_facets(
                prev_start, min_score=floor, until=window_start
            )
        ]
        view = build_angles(current, previous, config)
        return templates.TemplateResponse(
            request,
            "angles.html",
            {
                "days": days,
                "day_options": ANGLES_DAY_OPTIONS,
                "angles": view["angles"],
                "highlights": view["highlights"],
                "causal_model": _causal_model_view(config),
            },
        )

    @app.get("/post-ideas")
    def post_ideas_redirect(request: Request) -> RedirectResponse:
        """Permanent redirect for the page's former name (now ``/angles``).

        Forwards a ``?days=`` preset only when it's all-digits — the target
        clamps it — so a garbage value can't be reflected into the new URL.
        """
        days = request.query_params.get("days")
        target = f"/angles?days={days}" if days and days.isdigit() else "/angles"
        return RedirectResponse(target, status_code=301)

    def _render_timeline(
        request: Request,
        *,
        heading: str,
        subtitle: str,
        base_path: str,
        category: str | None = None,
        state: str | None = None,
        payer_group: PayerGroup | None = None,
        window_token: str | None = None,
        threads: bool = False,
    ) -> HTMLResponse:
        """Shared renderer for the layered timeline page and its scoped twins.

        One windowed query fetches the actual story rows (not just daily
        counts): the callout band and topic strip both plot each story
        directly, and the list below shows exactly the plotted set, so the
        chart and the list always agree. Strip rows are topics, except under a
        topic filter — where they'd collapse to one row — so the row
        dimension flips to payers (C4), all sharing that one topic's color.

        ``window_token`` is set only by the ``/timeline/w/{token}`` route,
        which wins over any ``?days=`` query value (D5); every other caller
        (the root ``/timeline`` GET and the scoped topic/payer/state pages)
        resolves the window from the query string instead.
        """
        store = request.app.state.store
        config = request.app.state.config
        floor = config.archive_min_score
        is_root = (
            category is None and state is None and payer_group is None and not threads
        )
        entity_aliases = list(payer_group.aliases) if payer_group else None

        raw_window = (
            window_token
            if window_token is not None
            else _window_param(request, default=str(TIMELINE_DEFAULT_DAYS))
        )
        days, token, window_label = _resolve_window(
            raw_window,
            store,
            category=category,
            state=state,
            entity_aliases=entity_aliases,
            min_score=floor,
        )
        now = datetime.utcnow()
        window_start = now.date() - timedelta(days=days - 1)

        stories = _timeline_window_stories(
            store,
            category=category,
            state=state,
            entity_aliases=entity_aliases,
            min_score=floor,
            window_start=window_start,
            now=now,
        )

        # Round-trips the resolved window (including "all") into scoped links,
        # so a linked topic/payer/state page reopens at the same lookback.
        scope_qs = f"?days={token}" if token != str(TIMELINE_DEFAULT_DAYS) else ""
        color_map = topic_color_map(config.categories)
        band = build_callout_band(
            stories,
            days=days,
            now=now,
            color_map=color_map,
            fallback_color=FALLBACK_TOPIC_COLOR,
        )
        thread_legend = None
        strip_bands = None
        thread_links = None
        muted_rows = None
        if threads:
            mode = "threads"
            groups, thread_legend, strip_bands, thread_links = _timeline_thread_groups(
                stories, config, color_map=color_map
            )
            # Both trailing aggregate rows (when present) read as visually
            # secondary to a found thread — see style.css's .strip-row-muted.
            # Membership is checked by row.key, not by href==None, so a
            # href-less *topic* row (e.g. "Other") is never accidentally
            # muted; only the threads lane ever produces these two keys.
            muted_rows = {THREAD_SMALLER_ROWS_KEY, THREAD_UNGROUPED_KEY}
        else:
            mode = "payers" if category else "topics"
            groups = _timeline_strip_groups(
                stories,
                config,
                mode=mode,
                scope_qs=scope_qs,
                color_map=color_map,
                scoped_category=category,
            )
        strip = build_strip(groups, days=days, now=now)
        # A legend only makes sense with more than one color in play — under a
        # topic scope every row already wears the same single color (C3); the
        # threads lane brings its own causal-aware legend (thread_legend).
        legend = (
            [
                {
                    "key": row.key,
                    "label": row.label,
                    "color": row.color,
                    "total": row.total,
                }
                for row in strip
            ]
            if mode == "topics"
            else None
        )

        # Topics ↔ Threads toggle, shown on the unscoped root chart and the
        # threads lane only (the emergent lane clusters the whole window). Plain
        # canonical hrefs keep it static-export-safe.
        view_toggle = (
            [
                {"label": "Topics", "href": "/timeline", "active": not threads},
                {"label": "Threads", "href": "/timeline/threads", "active": threads},
            ]
            if is_root or threads
            else None
        )

        total = len(stories)
        list_truncated = total > TIMELINE_LIST_MAX

        if is_root:
            # Plain-path window chips (D5): survive the static export as-is.
            window_options = [
                {
                    "label": f"{d} days",
                    "href": "/timeline"
                    if d == TIMELINE_DEFAULT_DAYS
                    else f"/timeline/w/{d}",
                    "active": token == str(d),
                }
                for d in TIMELINE_WINDOW_PRESETS
            ] + [{"label": "All", "href": "/timeline/w/all", "active": token == "all"}]
            show_picker_on_static = True
        else:
            # Scoped pages keep the old ?days= query scheme (C5) — needs a
            # live server, so (like the feed's period picker) it's dropped
            # from the static export entirely rather than frozen at one value.
            window_options = [
                {
                    "label": f"{d} days",
                    "href": f"{base_path}?days={d}",
                    "active": token == str(d),
                }
                for d in TIMELINE_WINDOW_PRESETS
            ] + [
                {
                    "label": "All",
                    "href": f"{base_path}?days=all",
                    "active": token == "all",
                }
            ]
            show_picker_on_static = False

        return templates.TemplateResponse(
            request,
            "timeline.html",
            {
                "heading": heading,
                "subtitle": subtitle,
                "window_label": window_label,
                "band": band,
                "ticks": axis_ticks(days, now),
                "strip": strip,
                "legend": legend,
                "thread_legend": thread_legend,
                "muted_rows": muted_rows,
                "strip_bands": strip_bands,
                "thread_links": thread_links,
                "view_toggle": view_toggle,
                "stories": stories[:TIMELINE_LIST_MAX],
                "total": total,
                "list_truncated": list_truncated,
                "window_options": window_options,
                "show_picker_on_static": show_picker_on_static,
                # The page plots (and lists) the whole window, so it never
                # paginates — total_pages=1 keeps _story_list's pager hidden.
                "page": 1,
                "total_pages": 1,
                "base_path": base_path,
                "days": days,
                "days_qs": f"&days={token}"
                if token != str(TIMELINE_DEFAULT_DAYS)
                else "",
                "days_base": f"{base_path}?",
                "filters": _feed_filters(
                    request,
                    active_category=category,
                    active_state=state,
                    active_payer=payer_group.slug if payer_group else None,
                ),
            },
        )

    @app.get("/timeline", response_class=HTMLResponse)
    def timeline(request: Request) -> HTMLResponse:
        return _render_timeline(
            request,
            heading="Signal Timeline",
            subtitle="Related signals plotted on one shared timeline — "
            "one topic-colored bubble row apiece.",
            base_path="/timeline",
        )

    @app.get("/timeline/w/{token}", response_model=None)
    def timeline_window(
        request: Request, token: str
    ) -> HTMLResponse | RedirectResponse:
        """Explicit lookback windows for the root timeline (D5).

        ``w/30`` is the default window's own canonical URL, so it 301s back to
        ``/timeline`` (the "30 days" chip links there directly instead of
        here); any token outside the valid preset set 404s, like the other
        unknown-slug routes.
        """
        if token == "30":
            return RedirectResponse("/timeline", status_code=301)
        if token not in {"7", "90", "180", "365", "all"}:
            raise HTTPException(status_code=404, detail="Unknown timeline window")
        return _render_timeline(
            request,
            heading="Signal Timeline",
            subtitle="Related signals plotted on one shared timeline — "
            "one topic-colored bubble row apiece.",
            base_path="/timeline",
            window_token=token,
        )

    @app.get("/timeline/threads", response_class=HTMLResponse)
    def timeline_threads(request: Request) -> HTMLResponse:
        """Emergent story-thread lane: the window clustered on the fly.

        On by default (``config.threads_enabled``); disabling it 404s the lane,
        like any other unknown timeline route.
        """
        if not request.app.state.config.threads_enabled:
            raise HTTPException(status_code=404, detail="Threads lane disabled")
        return _render_timeline(
            request,
            heading="Signal Timeline — Threads",
            subtitle="The window's signals clustered into emergent threads, "
            "each named from its own language and placed on the causal cascade.",
            base_path="/timeline/threads",
            threads=True,
        )

    @app.get("/timeline/threads/{key}", response_model=None)
    def timeline_thread(request: Request, key: str) -> HTMLResponse | RedirectResponse:
        """One emergent thread's own page, keyed on its anchor story's item_id.

        Threads aren't persisted, so unlike ``/timeline/topics/{key}`` or
        ``/timeline/payers/{slug}`` there is no static registry to validate
        ``key`` against — this reclusters the same default window
        ``/timeline/threads`` itself freezes at export time
        (:func:`_default_window_threads`) and looks the key up there instead,
        mirroring ``/story/{item_id}``'s query-then-None-check shape.

        Graceful degradation: if reclustering no longer produces a thread at
        this key — the window moved on, or the thread merged/dissolved/shrank
        below ``thread_min_stories`` — but the key is still a real story's
        ``item_id`` (true whenever it was ever some thread's anchor), redirect
        to that story's own page: once a thread dissolves, its anchor story is
        the best available answer. A key that never named anything real 404s.
        """
        config = request.app.state.config
        if not config.threads_enabled:
            raise HTTPException(status_code=404, detail="Threads lane disabled")
        store = request.app.state.store
        threads, _ungrouped = _default_window_threads(store, config)
        thread = next((t for t in threads if t.key == key), None)
        if thread is None:
            if store.get_story(key) is not None:
                return RedirectResponse(f"/story/{key}", status_code=302)
            raise HTTPException(status_code=404, detail="Thread not found")
        _days, _token, window_label = _resolve_window(
            str(TIMELINE_DEFAULT_DAYS), store, min_score=config.archive_min_score
        )
        # The reciprocal of the lane's forward "leads to" chip: every thread
        # whose single outgoing link (threads.build_thread_links allows at
        # most one per SOURCE, not per target) points at this one, so the
        # cascade reads in both directions from either end.
        by_key = {t.key: t for t in threads}
        caused_by = [
            {"key": src, "label": by_key[src].label, "href": f"/timeline/threads/{src}"}
            for src, link in _thread_links_view(threads, config).items()
            if link["key"] == key
        ]
        return templates.TemplateResponse(
            request,
            "thread.html",
            {
                "thread": thread,
                "window_label": window_label,
                "stories": list(thread.stories),
                "caused_by": caused_by,
                "base_path": f"/timeline/threads/{key}",
                "days_qs": "",
                "page": 1,
                "total_pages": 1,
            },
        )

    @app.get("/timeline/topics/{category_key}", response_class=HTMLResponse)
    def timeline_topic(request: Request, category_key: str) -> HTMLResponse:
        config = request.app.state.config
        valid = {c.key for c in config.categories}
        if category_key not in valid:
            raise HTTPException(status_code=404, detail="Unknown topic")
        from ma_signal_monitor.classify import get_category_label

        label = get_category_label(category_key, config)
        return _render_timeline(
            request,
            heading=f"Timeline — {label}",
            subtitle="Signals in this topic on one timeline — "
            "one payer bubble row apiece, all in this topic's color.",
            base_path=f"/timeline/topics/{category_key}",
            category=category_key,
        )

    @app.get("/timeline/payers/{slug}", response_class=HTMLResponse)
    def timeline_payer(request: Request, slug: str) -> HTMLResponse:
        group = get_group(slug)
        if group is None:
            raise HTTPException(status_code=404, detail="Unknown payer")
        return _render_timeline(
            request,
            heading=f"Timeline — {group.name}",
            subtitle="This organization's signals on one timeline — "
            "one topic-colored bubble row apiece.",
            base_path=f"/timeline/payers/{group.slug}",
            payer_group=group,
        )

    @app.get("/timeline/states/{code}", response_class=HTMLResponse)
    def timeline_state(request: Request, code: str) -> HTMLResponse:
        code = code.upper()
        if code not in STATE_NAMES:
            raise HTTPException(status_code=404, detail="Unknown state")
        return _render_timeline(
            request,
            heading=f"Timeline — {state_name(code)}",
            subtitle="Signals referencing this state on one timeline — "
            "one topic-colored bubble row apiece.",
            base_path=f"/timeline/states/{code}",
            state=code,
        )

    @app.get("/story/{item_id}", response_class=HTMLResponse)
    def story(request: Request, item_id: str) -> HTMLResponse:
        store = request.app.state.store
        config = request.app.state.config
        row = store.get_story(item_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Story not found")
        view = _story_view(row)
        # Link into the scoped timeline the same way the card captions do:
        # exactly one payer group → its payer timeline; else a real topic →
        # its topic timeline; else no link.
        groups, loose_aliases = _fold_entity_groups(view["entities"])
        valid_categories = {c.key for c in config.categories}
        if len(groups) == 1 and not loose_aliases:
            timeline_href = f"/timeline/payers/{groups[0].slug}"
        elif view["primary_category"] in valid_categories:
            timeline_href = f"/timeline/topics/{view['primary_category']}"
        else:
            timeline_href = None
        # Other sources that carried the same story (the "also covered by" set).
        also_covered = [
            {"source_name": d["source_name"], "link": d["link"]}
            for d in store.get_duplicates(item_id)
        ]
        return templates.TemplateResponse(
            request,
            "story.html",
            {
                "story": view,
                "feedback": store.get_feedback_summary(item_id),
                "also_covered": also_covered,
                "timeline_href": timeline_href,
            },
        )

    @app.post("/feedback")
    def submit_feedback(request: Request, payload: FeedbackIn) -> dict:
        """Record an owner verdict on a story (live app only).

        Static exports can't reach this — the crowd uses giscus instead.
        """
        store = request.app.state.store
        config = request.app.state.config
        if payload.verdict not in VALID_VERDICTS:
            raise HTTPException(status_code=400, detail="Unknown verdict")
        if store.get_story(payload.item_id) is None:
            raise HTTPException(status_code=404, detail="Story not found")
        suggested = None
        if payload.verdict == "wrong_category":
            valid = {c.key for c in config.categories}
            if payload.suggested_category not in valid:
                raise HTTPException(
                    status_code=400, detail="Unknown suggested_category"
                )
            suggested = payload.suggested_category
        store.add_feedback(
            payload.item_id,
            payload.verdict,
            channel="local_web",
            suggested_category=suggested,
            comment=payload.comment,
        )
        return {"ok": True, "feedback": store.get_feedback_summary(payload.item_id)}

    @app.get("/about-feedback", response_class=HTMLResponse)
    def about_feedback(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "about_feedback.html", {})

    @app.get("/health")
    def health(request: Request) -> dict:
        store = request.app.state.store
        config = request.app.state.config
        last = store.get_last_run()
        return {
            "status": "ok",
            "stories": store.count_stories(include_duplicates=True),
            "sources": len(config.sources),
            "enabled_sources": sum(1 for s in config.sources if s.enabled),
            "fts_enabled": store.fts_enabled,
            "last_run_end": last["run_end"] if last else None,
            "last_run_errors": last["errors"] if last else None,
            "categories": store.get_category_counts(),
        }

    @app.get("/status", response_class=HTMLResponse)
    def status(request: Request) -> HTMLResponse:
        store = request.app.state.store
        config = request.app.state.config
        last = store.get_last_run()
        cat_counts = store.get_category_counts()
        category_stats = [
            {"label": c.label, "count": cat_counts.get(c.key, 0)}
            for c in config.categories
        ]
        latest_digest = store.get_latest_digest()
        from ma_signal_monitor.source_review import flag_low_yield_sources

        source_yield = store.get_source_yield(config.min_relevance_score)
        flagged = flag_low_yield_sources(source_yield, config)
        flagged_names = {f["source"] for f in flagged}
        from ma_signal_monitor.source_health import flag_silent_sources

        source_health = store.get_source_fetch_health(
            lookback_days=max(60, config.source_silent_days * 2)
        )
        silent_sources = flag_silent_sources(source_health, config)
        from ma_signal_monitor.trends import sparkline

        trend = store.get_weekly_counts(weeks=12, min_score=config.archive_min_score)
        spark = sparkline([w["count"] for w in trend])
        return templates.TemplateResponse(
            request,
            "status.html",
            {
                "total_stories": store.count_stories(include_duplicates=True),
                "spark": spark,
                "category_stats": category_stats,
                "uncategorized": cat_counts.get("uncategorized", 0),
                "source_counts": store.get_source_counts(),
                "sources": config.sources,
                "enabled_sources": sum(1 for s in config.sources if s.enabled),
                "state_count": len(store.get_state_counts()),
                "last_run": last,
                "latest_digest": latest_digest,
                "fts_enabled": store.fts_enabled,
                "ingest_interval_hours": config.ingest_interval_hours,
                "digest_enabled": config.digest_enabled,
                "source_yield": source_yield,
                "flagged_sources": flagged,
                "flagged_names": flagged_names,
                "silent_sources": silent_sources,
                "source_silent_days": config.source_silent_days,
                "min_relevance_score": config.min_relevance_score,
            },
        )

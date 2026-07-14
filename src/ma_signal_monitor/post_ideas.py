"""Ranked LinkedIn post-topic suggestions built from a recent story window.

Turns the archive's existing signals into a short list of post-worthy themes:
stories from the last N days are grouped by topic vertical, ranked by volume
and top score, compared against the previous window of the same length for
momentum, and each theme borrows its strongest story's public draft (opening
hook + hashtags) as the starting point for a post.

Pure data-in/data-out so it is unit-testable without HTTP: the ``/post-ideas``
route feeds it the web layer's ``_story_view`` dicts for the two windows.
"""

from ma_signal_monitor.classify import get_category_label
from ma_signal_monitor.config import AppConfig
from ma_signal_monitor.payers import ALIAS_TO_GROUP

# How many themes the page shows, and how many stories each theme card links.
MAX_THEMES = 8
STORIES_PER_THEME = 3
# Chips per theme card / per the window-wide highlights row.
TAGS_PER_THEME = 3
TAGS_PER_HIGHLIGHTS = 5

_FALLBACK_HASHTAGS = ("#MedicareAdvantage",)


def _fold_payers(stories: list[dict]) -> list[dict]:
    """Count canonical payer groups mentioned across ``stories``.

    Each story counts a group once even when several of its aliases match.
    Aliases without a canonical group (e.g. agencies like CMS) are skipped —
    the chips link to payer pages, which only exist for grouped organizations.
    """
    counts: dict[str, int] = {}
    names: dict[str, str] = {}
    for s in stories:
        seen: set[str] = set()
        for alias in s.get("entities") or []:
            group = ALIAS_TO_GROUP.get(alias)
            if group is None or group.slug in seen:
                continue
            seen.add(group.slug)
            counts[group.slug] = counts.get(group.slug, 0) + 1
            names[group.slug] = group.name
    return [
        {"slug": slug, "name": names[slug], "count": n}
        for slug, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _fold_states(stories: list[dict]) -> list[dict]:
    """Count state codes mentioned across ``stories`` (once per story)."""
    counts: dict[str, int] = {}
    for s in stories:
        for code in set(s.get("states") or []):
            counts[code] = counts.get(code, 0) + 1
    return [
        {"code": code, "count": n}
        for code, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _momentum(count: int, prev_count: int) -> str:
    """Label this window's volume against the previous window's."""
    if prev_count == 0:
        return "new"
    if count > prev_count:
        return "up"
    if count < prev_count:
        return "down"
    return "steady"


def _fallback_hook(label: str, top_title: str, count: int) -> str:
    """A serviceable opening line when no story in the theme carries a draft.

    Most archived stories sit below the alert threshold and have no
    ``public_draft``, so this is the common path, not an edge case.
    """
    if count > 1:
        return (
            f"“{top_title}” — the strongest of {count} signals in {label} this period."
        )
    return f"“{top_title}” — a fresh signal in {label} worth a closer look."


def build_post_ideas(
    current: list[dict],
    previous: list[dict],
    config: AppConfig,
) -> dict:
    """Build the Post Ideas view-model from two adjacent story windows.

    ``current`` and ``previous`` are template-ready story dicts for the
    last-N-days window and the N days before it. Returns
    ``{"themes": [...], "highlights": {...}}`` with themes ranked by volume,
    then top score. ``uncategorized`` stories count toward the window total
    but never form a theme — they aren't a postable topic vertical.
    """
    prev_counts: dict[str, int] = {}
    for s in previous:
        key = s.get("primary_category") or "uncategorized"
        prev_counts[key] = prev_counts.get(key, 0) + 1

    by_theme: dict[str, list[dict]] = {}
    for s in current:
        key = s.get("primary_category") or "uncategorized"
        if key == "uncategorized":
            continue
        by_theme.setdefault(key, []).append(s)

    themes = []
    for key, stories in by_theme.items():
        stories = sorted(
            stories, key=lambda s: s.get("relevance_score") or 0.0, reverse=True
        )
        # Borrow the hook/hashtags from the strongest story that has a draft.
        hook = None
        hashtags: list[str] | None = None
        for s in stories:
            draft = s.get("public_draft") or {}
            if hook is None and draft.get("opening_hook"):
                hook = draft["opening_hook"]
            if hashtags is None and draft.get("suggested_hashtags"):
                hashtags = list(draft["suggested_hashtags"])
            if hook is not None and hashtags is not None:
                break
        label = get_category_label(key, config)
        count = len(stories)
        prev = prev_counts.get(key, 0)
        themes.append(
            {
                "key": key,
                "label": label,
                "count": count,
                "prev_count": prev,
                "momentum": _momentum(count, prev),
                "hook": hook or _fallback_hook(label, stories[0]["title"], count),
                "hashtags": hashtags or list(_FALLBACK_HASHTAGS),
                "stories": stories[:STORIES_PER_THEME],
                "top_score": stories[0].get("relevance_score") or 0.0,
                "payers": _fold_payers(stories)[:TAGS_PER_THEME],
                "states": _fold_states(stories)[:TAGS_PER_THEME],
            }
        )
    themes.sort(key=lambda t: (-t["count"], -t["top_score"], t["label"]))

    return {
        "themes": themes[:MAX_THEMES],
        "highlights": {
            "total": len(current),
            "payers": _fold_payers(current)[:TAGS_PER_HIGHLIGHTS],
            "states": _fold_states(current)[:TAGS_PER_HIGHLIGHTS],
        },
    }

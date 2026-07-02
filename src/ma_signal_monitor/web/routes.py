"""HTTP routes for the MA Signal Monitor web frontend.

All handlers read from ``request.app.state`` (config, store, templates) so the
app is easy to construct in tests with a seeded database.
"""

import json
import math
import sqlite3
from urllib.parse import quote_plus

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from ma_signal_monitor.geo import STATE_NAMES, state_name
from ma_signal_monitor.storage import VALID_VERDICTS


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
        "relevance_score": row["relevance_score"] or 0.0,
        "primary_category": row["primary_category"] or "uncategorized",
        "categories": json.loads(row["categories"] or "[]"),
        "entities": json.loads(row["entities"] or "[]"),
        "states": json.loads(row["states"] or "[]"),
        "public_draft": json.loads(row["public_draft"])
        if row["public_draft"]
        else None,
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


def register_routes(app: FastAPI, templates: Jinja2Templates) -> None:
    """Register all frontend routes on the app."""

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

        return templates.TemplateResponse(
            request,
            "search.html",
            {
                "query": query,
                "stories": stories,
                "total": total,
                "page": page,
                "total_pages": total_pages,
                "base_path": f"/search?q={quote_plus(query)}&",
            },
        )

    @app.get("/sources", response_class=HTMLResponse)
    def sources(request: Request) -> HTMLResponse:
        config = request.app.state.config
        default_cadence = f"every {config.ingest_interval_hours}h"
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

    @app.get("/story/{item_id}", response_class=HTMLResponse)
    def story(request: Request, item_id: str) -> HTMLResponse:
        store = request.app.state.store
        row = store.get_story(item_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Story not found")
        return templates.TemplateResponse(
            request,
            "story.html",
            {
                "story": _story_view(row),
                "feedback": store.get_feedback_summary(item_id),
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
            "stories": store.count_stories(),
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
        return templates.TemplateResponse(
            request,
            "status.html",
            {
                "total_stories": store.count_stories(),
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
                "min_relevance_score": config.min_relevance_score,
            },
        )

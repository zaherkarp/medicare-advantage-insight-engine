"""Static site export for GitHub Pages hosting.

Renders the dynamic web app to flat HTML by crawling its routes with an
in-process TestClient, then rewrites root-relative links/asset paths into
static ``.html`` files under a configurable base path (so it works under a
GitHub *project* Pages sub-path like ``/<repo>/``).

Paginated feeds (the main feed, topics, states, candidates) are crawled one
page at a time and written to numbered files (``index.html``, ``index-2.html``,
…) so the on-page ``?page=`` pager links resolve to real static files instead
of dumping the whole archive onto a single page.

Full-text search can't run server-side on Pages, so search is replaced with a
client-side page backed by a generated ``search-index.json``.
"""

import json
import logging
import math
import re
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from ma_signal_monitor.classify import get_category_label
from ma_signal_monitor.config import AppConfig
from ma_signal_monitor.payers import PAYER_GROUPS
from ma_signal_monitor.storage import StateStore
from ma_signal_monitor.web.app import create_app

logger = logging.getLogger("ma_signal_monitor.static_export")

_WEB_DIR = Path(__file__).parent / "web"
# Match href/src/action="/...": only root-relative URLs (skips http(s) externals).
_LINK_RE = re.compile(r'(href|src|action)="(/[^"]*)"')
# Cap how many stories/digests we render as standalone pages.
_MAX_STORY_PAGES = 2000


def _page_from_query(query: str) -> int:
    """Parse a 1-based ``page`` value from a raw query string (clamped >= 1)."""
    for part in query.split("&"):
        if part.startswith("page="):
            try:
                return max(1, int(part[len("page=") :]))
            except ValueError:
                return 1
    return 1


def _paginate_tail(tail: str, page: int) -> str:
    """Insert a ``-<page>`` suffix before the extension (page >= 2 only).

    ``index.html`` -> ``index-2.html``; ``topics/x.html`` -> ``topics/x-2.html``.
    Page 1 keeps the bare filename so pager links back to it resolve cleanly.
    """
    if page <= 1:
        return tail
    stem, dot, ext = tail.rpartition(".")
    return f"{stem}-{page}.{ext}" if dot else f"{tail}-{page}"


def _map_path(path_with_q: str, base: str) -> str:
    """Map a server route (with any ``?page=``) to its static file URL."""
    raw = path_with_q.split("#", 1)[0]
    path, _, query = raw.partition("?")
    page = _page_from_query(query)
    if path == "/":
        tail = "index.html"
    elif path.startswith("/static/"):
        tail = path.lstrip("/")
    elif path.startswith("/topics/"):
        tail = f"topics/{path[len('/topics/') :]}.html"
    elif path == "/states":
        tail = "states.html"
    elif path.startswith("/states/"):
        tail = f"states/{path[len('/states/') :]}.html"
    elif path == "/payers":
        tail = "payers.html"
    elif path.startswith("/payers/"):
        tail = f"payers/{path[len('/payers/') :]}.html"
    elif path.startswith("/story/"):
        tail = f"story/{path[len('/story/') :]}.html"
    elif path == "/sources":
        tail = "sources.html"
    elif path == "/candidates":
        tail = "candidates.html"
    elif path == "/briefing":
        tail = "briefing.html"
    elif path.startswith("/briefing/"):
        tail = f"briefing/{path[len('/briefing/') :]}.html"
    elif path == "/post-ideas":
        # Any ?days= preset collapses to the default-window page (the picker
        # is hidden on the static site anyway).
        tail = "post-ideas.html"
    elif path.startswith("/search"):
        tail = "search.html"
    elif path == "/status":
        tail = "status.html"
    elif path == "/about-feedback":
        tail = "about-feedback.html"
    else:
        return path_with_q  # unknown — leave as-is
    return f"{base}/{_paginate_tail(tail, page)}"


def _rewrite_links(html: str, base: str) -> str:
    """Rewrite all root-relative links/assets to static, base-prefixed URLs."""
    return _LINK_RE.sub(lambda m: f'{m.group(1)}="{_map_path(m.group(2), base)}"', html)


def _write(out_dir: Path, tail: str, html: str, base: str) -> None:
    dest = out_dir / tail
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_rewrite_links(html, base), encoding="utf-8")


def _search_index(store: StateStore, config: AppConfig, base: str) -> list[dict]:
    """Build the client-side search index from the archive.

    Honors ``archive_min_score`` so client-side search can't resurface the
    sub-floor noise that the rest of the static site hides.
    """
    index = []
    for row in store.get_stories(
        limit=_MAX_STORY_PAGES, min_score=config.archive_min_score
    ):
        cat = row["primary_category"] or "uncategorized"
        index.append(
            {
                "title": row["title"],
                "summary": row["summary"] or "",
                "source": row["source_name"],
                "category": get_category_label(cat, config),
                "url": f"{base}/story/{row['item_id']}.html",
                "date": (row["published_date"] or "")[:10],
            }
        )
    return index


# A self-contained client-side search page (no build step).
_SEARCH_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Search — MA Signal Monitor</title>
<link rel="stylesheet" href="{base}/static/style.css"></head>
<body>
<header class="site-header"><div class="wrap">
  <a class="brand" href="{base}/index.html">MA&nbsp;Signal&nbsp;Monitor</a>
  <nav class="nav">
    <a href="{base}/index.html">Feed</a>
    <a href="{base}/briefing.html">Daily Briefing</a>
    <a href="{base}/post-ideas.html">Post Ideas</a>
    <div class="dropdown">
      <span class="nav-label" tabindex="0" role="button" aria-haspopup="true">System ▾</span>
      <div class="dropdown-menu">
        <a href="{base}/sources.html">Sources</a>
        <a href="{base}/candidates.html">Candidates</a>
        <a href="{base}/status.html">Status</a>
      </div>
    </div>
  </nav>
</div></header>
<main class="wrap">
  <section class="page-head">
    <h1>Search</h1>
    <form class="search-form" onsubmit="return false;">
      <input type="search" id="q" placeholder="Search signals (e.g. Star Ratings, Humana, Texas)" autofocus>
    </form>
    <p class="muted" id="count"></p>
  </section>
  <ul class="story-list" id="results"></ul>
</main>
<footer class="site-footer"><div class="wrap"><p>Curated for analytic relevance —
  not legal, compliance, or investment advice.</p></div></footer>
<script>
let DATA = [];
const qEl = document.getElementById('q');
const out = document.getElementById('results');
const countEl = document.getElementById('count');
function esc(s){{return (s||'').replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));}}
function render(items, q){{
  countEl.textContent = q ? (items.length + ' result' + (items.length===1?'':'s') + ' for \\u201c' + q + '\\u201d') : '';
  out.innerHTML = items.map(it => (
    '<li class="story-card"><div class="story-meta">' +
    '<span class="badge badge-cat">' + esc(it.category) + '</span>' +
    '<span class="source">' + esc(it.source) + '</span>' +
    (it.date ? '<span class="date">' + esc(it.date) + '</span>' : '') +
    '</div><h2 class="story-title"><a href="' + it.url + '">' + esc(it.title) + '</a></h2>' +
    (it.summary ? '<p class="story-summary">' + esc(it.summary) + '</p>' : '') + '</li>'
  )).join('');
}}
function search(q){{
  q = (q||'').trim().toLowerCase();
  if(!q){{ render([], ''); return; }}
  const terms = q.split(/\\s+/);
  const hits = DATA.filter(it => {{
    const hay = (it.title + ' ' + it.summary + ' ' + it.source + ' ' + it.category).toLowerCase();
    return terms.every(t => hay.includes(t));
  }});
  render(hits, q);
}}
fetch('search-index.json').then(r => r.json()).then(d => {{
  DATA = d;
  const q = new URLSearchParams(location.search).get('q') || '';
  qEl.value = q; search(q);
}});
qEl.addEventListener('input', () => search(qEl.value));
</script>
</body></html>
"""


def build_site(
    store: StateStore,
    config: AppConfig,
    out_dir: Path,
    base_path: str = "",
) -> dict:
    """Render the whole site to ``out_dir`` as static files.

    Args:
        store: A StateStore opened on the populated archive DB.
        config: Application configuration.
        out_dir: Output directory (cleared and recreated).
        base_path: URL prefix for project Pages, e.g. "/my-repo" ("" for root).

    Returns:
        A dict of counts for logging/verification.
    """
    base = base_path.rstrip("/")
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    page_size = max(1, config.web_page_size)
    # Public archive floor: sub-floor stories (pure source-priority noise) are
    # kept in the DB but never rendered as pages, linked, counted, or indexed
    # on the published site. Mirrors the live web routes.
    floor = config.archive_min_score
    app = create_app(config, store, static_export=True)
    # A known origin so we can strip it: Starlette's url_for() (used for static
    # assets) emits absolute URLs against the client base.
    origin = "https://export.local"
    client = TestClient(app, base_url=origin)

    def grab(route: str, tail: str) -> None:
        resp = client.get(route)
        if resp.status_code == 200:
            html = resp.text.replace(origin, "")
            _write(out_dir, tail, html, base)

    def grab_paginated(route: str, page1_tail: str, total: int) -> None:
        """Render every page of a paginated feed to its own static file.

        Page 1 keeps the canonical filename (e.g. ``index.html``); pages 2..N
        get a ``-<page>`` suffix that matches the pager links rewritten by
        :func:`_map_path`, so navigation between pages works statically.
        """
        total_pages = max(1, math.ceil(total / page_size)) if total else 1
        grab(route, page1_tail)
        sep = "&" if "?" in route else "?"
        for p in range(2, total_pages + 1):
            grab(f"{route}{sep}page={p}", _paginate_tail(page1_tail, p))

    grab_paginated("/", "index.html", store.count_stories(min_score=floor))
    grab("/sources", "sources.html")
    grab_paginated("/candidates", "candidates.html", store.count_candidate_sources())
    grab("/states", "states.html")
    grab("/status", "status.html")
    grab("/about-feedback", "about-feedback.html")
    grab("/briefing", "briefing.html")
    # Frozen at the default window per build; the scheduled Pages workflow
    # rebuilds it, so "this week" tracks the latest deploy.
    grab("/post-ideas", "post-ideas.html")

    for c in config.categories:
        grab_paginated(
            f"/topics/{c.key}",
            f"topics/{c.key}.html",
            store.count_stories(category=c.key, min_score=floor),
        )
    for code in store.get_state_counts(min_score=floor):
        grab_paginated(
            f"/states/{code}",
            f"states/{code}.html",
            store.count_stories(state=code, min_score=floor),
        )
    grab("/payers", "payers.html")
    for group in PAYER_GROUPS:
        grab_paginated(
            f"/payers/{group.slug}",
            f"payers/{group.slug}.html",
            store.count_stories(entity_aliases=list(group.aliases), min_score=floor),
        )
    story_rows = store.get_stories(limit=_MAX_STORY_PAGES, min_score=floor)
    for row in story_rows:
        grab(f"/story/{row['item_id']}", f"story/{row['item_id']}.html")
    for d in store.list_digests(limit=400):
        grab(f"/briefing/{d['digest_date']}", f"briefing/{d['digest_date']}.html")

    # Client-side search page + index.
    (out_dir / "search.html").write_text(
        _SEARCH_HTML.format(base=base), encoding="utf-8"
    )
    (out_dir / "search-index.json").write_text(
        json.dumps(_search_index(store, config, base)), encoding="utf-8"
    )

    # Copy static assets and add Pages housekeeping files.
    shutil.copytree(_WEB_DIR / "static", out_dir / "static")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    counts = {
        "stories": len(story_rows),
        "topics": len(config.categories),
        "states": len(store.get_state_counts(min_score=floor)),
        "payers": len(PAYER_GROUPS),
        "digests": len(store.list_digests(limit=400)),
    }
    logger.info("Static site built at %s: %s", out_dir, counts)
    return counts


def _cli() -> None:
    """Console entry point (``ma-signal-build``)."""
    import argparse
    import os

    from ma_signal_monitor.config import load_config

    parser = argparse.ArgumentParser(description="Build the static Pages site.")
    parser.add_argument("--base-path", default=os.getenv("STATIC_BASE_PATH", ""))
    parser.add_argument("--out", default=os.getenv("STATIC_OUT", "site"))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    root = Path(args.root)
    config = load_config(root)
    store = StateStore(root / config.db_path)
    try:
        counts = build_site(store, config, Path(args.out), base_path=args.base_path)
    finally:
        store.close()
    print(f"Built static site at '{args.out}': {counts}")

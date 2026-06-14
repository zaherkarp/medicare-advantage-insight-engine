"""Static site export for GitHub Pages hosting.

Renders the dynamic web app to flat HTML by crawling its routes with an
in-process TestClient, then rewrites root-relative links/asset paths into
static ``.html`` files under a configurable base path (so it works under a
GitHub *project* Pages sub-path like ``/<repo>/``).

Full-text search can't run server-side on Pages, so search is replaced with a
client-side page backed by a generated ``search-index.json``.
"""

import json
import logging
import re
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from ma_signal_monitor.classify import get_category_label
from ma_signal_monitor.config import AppConfig
from ma_signal_monitor.storage import StateStore
from ma_signal_monitor.web.app import create_app

logger = logging.getLogger("ma_signal_monitor.static_export")

_WEB_DIR = Path(__file__).parent / "web"
# Match href/src/action="/...": only root-relative URLs (skips http(s) externals).
_LINK_RE = re.compile(r'(href|src|action)="(/[^"]*)"')
# Cap how many stories/digests we render as standalone pages.
_MAX_STORY_PAGES = 2000


def _map_path(path_with_q: str, base: str) -> str:
    """Map a server route to its static file URL (base-prefixed)."""
    path = path_with_q.split("?", 1)[0].split("#", 1)[0]
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
    elif path.startswith("/search"):
        tail = "search.html"
    elif path == "/status":
        tail = "status.html"
    elif path == "/about-feedback":
        tail = "about-feedback.html"
    else:
        return path_with_q  # unknown — leave as-is
    return f"{base}/{tail}"


def _rewrite_links(html: str, base: str) -> str:
    """Rewrite all root-relative links/assets to static, base-prefixed URLs."""
    return _LINK_RE.sub(lambda m: f'{m.group(1)}="{_map_path(m.group(2), base)}"', html)


def _write(out_dir: Path, tail: str, html: str, base: str) -> None:
    dest = out_dir / tail
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_rewrite_links(html, base), encoding="utf-8")


def _search_index(store: StateStore, config: AppConfig, base: str) -> list[dict]:
    """Build the client-side search index from the archive."""
    index = []
    for row in store.get_stories(limit=_MAX_STORY_PAGES):
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
    <a href="{base}/sources.html">Sources</a>
    <a href="{base}/candidates.html">Candidates</a>
    <a href="{base}/states.html">State Intelligence</a>
    <a href="{base}/status.html">Status</a>
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

    # Render against a single page so paginated feeds fit without ?page= links.
    config.web_page_size = _MAX_STORY_PAGES
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

    grab("/", "index.html")
    grab("/sources", "sources.html")
    grab("/candidates", "candidates.html")
    grab("/states", "states.html")
    grab("/status", "status.html")
    grab("/about-feedback", "about-feedback.html")
    grab("/briefing", "briefing.html")

    for c in config.categories:
        grab(f"/topics/{c.key}", f"topics/{c.key}.html")
    for code in store.get_state_counts():
        grab(f"/states/{code}", f"states/{code}.html")
    story_rows = store.get_stories(limit=_MAX_STORY_PAGES)
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
        "states": len(store.get_state_counts()),
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
